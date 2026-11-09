"""Tests for the split job queue: app/jobs.py (endpoints) and app/worker.py.

Runs against a throwaway SQLite file instead of Postgres, and fakes the
arq queue and the Spotify/Groq pipeline, so no services are needed."""

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import get_current_user
from app.db import Base, get_db
from app.jobs import QUEUED_STALE_SECONDS, RUNNING_STALE_SECONDS, STALE_RUNNING_ERROR, get_queue
from app.main import app
from app.models import JOB_DONE, JOB_FAILED, JOB_QUEUED, JOB_RUNNING, SplitJob, SplitResultCache, TrackAudioFeatures, User
from app.pipeline import ALGORITHM_VERSION
from app.tracks import CACHE_TTL_SECONDS
from app.worker import GENERIC_ERROR, nightly_cleanup, run_split_job

FAKE_RESULT = {"k": 1, "clusters": [{"cluster_id": 0, "label": "All Tracks", "tracks": []}]}


@pytest.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(User(spotify_id="me", access_token="tok", expires_at=time.time() + 3600))
        db.add(User(spotify_id="someone-else", access_token="tok2", expires_at=time.time() + 3600))
        await db.commit()
    yield factory
    await engine.dispose()


async def add_job(session_factory, **fields) -> SplitJob:
    defaults = {
        "id": "job1",
        "user_id": "me",
        "playlist_id": "pl1",
        "snapshot_id": "snap1",
        "status": JOB_QUEUED,
        "cache_hit": False,
        "created_at": time.time(),
    }
    job = SplitJob(**{**defaults, **fields})
    async with session_factory() as db:
        db.add(job)
        await db.commit()
    return job


async def get_job_row(session_factory, job_id: str) -> SplitJob:
    async with session_factory() as db:
        return await db.get(SplitJob, job_id)


# --- Endpoints --------------------------------------------------------------


@pytest.fixture
def queue():
    fake = AsyncMock()
    fake.enqueue_job = AsyncMock()
    return fake


@pytest.fixture
def snapshot():
    with patch("app.jobs.get_playlist_snapshot_id", new=AsyncMock(return_value="snap1")) as mocked:
        yield mocked


@pytest.fixture
async def client(session_factory, queue, snapshot):
    async def db_override():
        async with session_factory() as db:
            yield db

    async def user_override():
        async with session_factory() as db:
            return await db.get(User, "me")

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    app.dependency_overrides[get_queue] = lambda: queue
    # No lifespan here, so no real Postgres or Redis gets touched.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class TestStartSplitJob:
    async def test_creates_a_queued_job_and_enqueues_it(self, client, queue, session_factory):
        response = await client.post("/api/playlists/pl1/split-jobs")
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == JOB_QUEUED
        assert body["result"] is None
        queue.enqueue_job.assert_awaited_once_with("run_split_job", body["id"], _job_id=body["id"])
        row = await get_job_row(session_factory, body["id"])
        assert (row.user_id, row.playlist_id, row.snapshot_id) == ("me", "pl1", "snap1")

    async def test_second_start_returns_the_same_active_job(self, client, queue):
        first = (await client.post("/api/playlists/pl1/split-jobs")).json()
        second = (await client.post("/api/playlists/pl1/split-jobs")).json()
        assert first["id"] == second["id"]
        assert queue.enqueue_job.await_count == 1

    async def test_different_playlists_get_separate_jobs(self, client):
        first = (await client.post("/api/playlists/pl1/split-jobs")).json()
        second = (await client.post("/api/playlists/pl2/split-jobs")).json()
        assert first["id"] != second["id"]

    async def test_cache_hit_returns_a_finished_job_without_queueing(self, client, queue, session_factory):
        async with session_factory() as db:
            db.add(
                SplitResultCache(
                    playlist_id="pl1",
                    snapshot_id="snap1",
                    algorithm_version=ALGORITHM_VERSION,
                    result=FAKE_RESULT,
                    created_at=time.time(),
                )
            )
            await db.commit()
        body = (await client.post("/api/playlists/pl1/split-jobs")).json()
        assert body["status"] == JOB_DONE
        assert body["cache_hit"] is True
        assert body["result"] == FAKE_RESULT
        queue.enqueue_job.assert_not_awaited()

    async def test_edited_playlist_misses_the_cache(self, client, queue, session_factory, snapshot):
        async with session_factory() as db:
            db.add(
                SplitResultCache(
                    playlist_id="pl1",
                    snapshot_id="snap-before-edit",
                    algorithm_version=ALGORITHM_VERSION,
                    result=FAKE_RESULT,
                    created_at=time.time(),
                )
            )
            await db.commit()
        body = (await client.post("/api/playlists/pl1/split-jobs")).json()
        assert body["status"] == JOB_QUEUED
        queue.enqueue_job.assert_awaited_once()

    async def test_old_algorithm_version_misses_the_cache(self, client, queue, session_factory):
        async with session_factory() as db:
            db.add(
                SplitResultCache(
                    playlist_id="pl1",
                    snapshot_id="snap1",
                    algorithm_version="old",
                    result=FAKE_RESULT,
                    created_at=time.time(),
                )
            )
            await db.commit()
        body = (await client.post("/api/playlists/pl1/split-jobs")).json()
        assert body["status"] == JOB_QUEUED

    async def test_stale_active_job_is_failed_and_replaced(self, client, session_factory):
        long_ago = time.time() - RUNNING_STALE_SECONDS - 1
        await add_job(session_factory, id="dead", status=JOB_RUNNING, heartbeat_at=long_ago)
        body = (await client.post("/api/playlists/pl1/split-jobs")).json()
        assert body["id"] != "dead"
        assert body["status"] == JOB_QUEUED
        dead = await get_job_row(session_factory, "dead")
        assert dead.status == JOB_FAILED
        assert dead.error == STALE_RUNNING_ERROR

    async def test_queue_down_fails_the_job_with_503(self, client, queue, session_factory):
        queue.enqueue_job.side_effect = ConnectionError("redis is down")
        response = await client.post("/api/playlists/pl1/split-jobs")
        assert response.status_code == 503
        # The failed job doesn't block the next attempt.
        queue.enqueue_job.side_effect = None
        retry = await client.post("/api/playlists/pl1/split-jobs")
        assert retry.json()["status"] == JOB_QUEUED

    async def test_unreadable_playlist_passes_spotifys_error_through(self, client, snapshot):
        snapshot.side_effect = HTTPException(status_code=404, detail="not found")
        response = await client.post("/api/playlists/pl1/split-jobs")
        assert response.status_code == 404


class TestGetJob:
    async def test_returns_progress_while_running(self, client, session_factory):
        await add_job(
            session_factory,
            status=JOB_RUNNING,
            heartbeat_at=time.time(),
            stage="clustering",
            progress_done=40,
            progress_total=200,
        )
        body = (await client.get("/api/jobs/job1")).json()
        assert body["stage"] == "clustering"
        assert body["progress"] == {"done": 40, "total": 200}

    async def test_other_users_job_is_a_404(self, client, session_factory):
        await add_job(session_factory, user_id="someone-else")
        assert (await client.get("/api/jobs/job1")).status_code == 404

    async def test_missing_job_is_a_404(self, client):
        assert (await client.get("/api/jobs/nope")).status_code == 404

    async def test_running_job_with_quiet_heartbeat_reads_as_failed(self, client, session_factory):
        await add_job(session_factory, status=JOB_RUNNING, heartbeat_at=time.time() - RUNNING_STALE_SECONDS - 1)
        body = (await client.get("/api/jobs/job1")).json()
        assert body["status"] == JOB_FAILED
        assert body["error"] == STALE_RUNNING_ERROR

    async def test_slow_but_alive_job_stays_running(self, client, session_factory):
        await add_job(
            session_factory,
            status=JOB_RUNNING,
            started_at=time.time() - 3600,
            heartbeat_at=time.time() - 5,
        )
        assert (await client.get("/api/jobs/job1")).json()["status"] == JOB_RUNNING

    async def test_queued_job_nobody_picks_up_reads_as_failed(self, client, session_factory):
        await add_job(session_factory, created_at=time.time() - QUEUED_STALE_SECONDS - 1)
        assert (await client.get("/api/jobs/job1")).json()["status"] == JOB_FAILED


# --- Worker -----------------------------------------------------------------


@pytest.fixture
def fake_token():
    with patch("app.worker.ensure_fresh_token", new=AsyncMock(return_value="tok")):
        yield


class TestRunSplitJob:
    async def test_success_stores_result_and_caches_it(self, session_factory, fake_token):
        await add_job(session_factory)
        stages_seen = []

        async def fake_pipeline(playlist_id, access_token, db, progress):
            await progress("fetching", 20, 40)
            stages_seen.append((await get_job_row(session_factory, "job1")).stage)
            return FAKE_RESULT

        with patch("app.worker.run_split_pipeline", new=fake_pipeline):
            await run_split_job({"session_factory": session_factory}, "job1")

        assert stages_seen == ["fetching"]
        job = await get_job_row(session_factory, "job1")
        assert job.status == JOB_DONE
        assert job.result == FAKE_RESULT
        assert job.stage is None
        async with session_factory() as db:
            cached = await db.get(SplitResultCache, ("pl1", "snap1", ALGORITHM_VERSION))
        assert cached.result == FAKE_RESULT

    async def test_pipeline_http_error_becomes_the_job_error(self, session_factory, fake_token):
        await add_job(session_factory)
        failing = AsyncMock(side_effect=HTTPException(status_code=502, detail="Groq API hit the free tier's rate limit"))
        with patch("app.worker.run_split_pipeline", new=failing):
            await run_split_job({"session_factory": session_factory}, "job1")
        job = await get_job_row(session_factory, "job1")
        assert job.status == JOB_FAILED
        assert job.error == "Groq API hit the free tier's rate limit"

    async def test_unexpected_crash_gets_a_generic_error(self, session_factory, fake_token):
        await add_job(session_factory)
        with patch("app.worker.run_split_pipeline", new=AsyncMock(side_effect=KeyError("snapshot_id"))):
            await run_split_job({"session_factory": session_factory}, "job1")
        job = await get_job_row(session_factory, "job1")
        assert job.status == JOB_FAILED
        assert job.error == GENERIC_ERROR

    async def test_finished_job_is_not_rerun(self, session_factory, fake_token):
        await add_job(session_factory, status=JOB_DONE, result=FAKE_RESULT)
        pipeline = AsyncMock()
        with patch("app.worker.run_split_pipeline", new=pipeline):
            await run_split_job({"session_factory": session_factory}, "job1")
        pipeline.assert_not_awaited()

    async def test_retry_after_worker_death_runs_again(self, session_factory, fake_token):
        # arq re-delivers a job whose worker died, and the row still says running.
        await add_job(session_factory, status=JOB_RUNNING, heartbeat_at=time.time())
        with patch("app.worker.run_split_pipeline", new=AsyncMock(return_value=FAKE_RESULT)):
            await run_split_job({"session_factory": session_factory}, "job1")
        assert (await get_job_row(session_factory, "job1")).status == JOB_DONE


class TestNightlyCleanup:
    async def test_cleans_up_old_and_stale_rows_only(self, session_factory):
        now = time.time()
        long_ago = now - 8 * 24 * 60 * 60
        await add_job(session_factory, id="old-done", playlist_id="a", status=JOB_DONE, finished_at=long_ago)
        await add_job(session_factory, id="new-done", playlist_id="b", status=JOB_DONE, finished_at=now)
        await add_job(session_factory, id="dead", playlist_id="c", status=JOB_RUNNING, heartbeat_at=long_ago)
        async with session_factory() as db:
            db.add(SplitResultCache(playlist_id="a", snapshot_id="s", algorithm_version="old", result={}, created_at=now))
            db.add(
                SplitResultCache(
                    playlist_id="b", snapshot_id="s", algorithm_version=ALGORITHM_VERSION, result={}, created_at=now
                )
            )
            db.add(TrackAudioFeatures(track_id="stale", features=None, fetched_at=now - CACHE_TTL_SECONDS - 1))
            db.add(TrackAudioFeatures(track_id="fresh", features=None, fetched_at=now))
            await db.commit()

        await nightly_cleanup({"session_factory": session_factory})

        async with session_factory() as db:
            assert await db.get(SplitJob, "old-done") is None
            assert await db.get(SplitJob, "new-done") is not None
            assert (await db.get(SplitJob, "dead")).status == JOB_FAILED
            assert await db.get(SplitResultCache, ("a", "s", "old")) is None
            assert await db.get(SplitResultCache, ("b", "s", ALGORITHM_VERSION)) is not None
            assert await db.get(TrackAudioFeatures, "stale") is None
            assert await db.get(TrackAudioFeatures, "fresh") is not None
