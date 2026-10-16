"""Background worker: runs splits off the request path.

Start it next to the API with:

    arq app.worker.WorkerSettings

arq pulls job ids off Redis; everything about a job's state lives in the
split_jobs table (see app/models.py). arq was picked over Celery and RQ
because the whole pipeline is async (httpx, asyncpg, the Groq client),
and both of those run tasks synchronously.
"""

import asyncio
import logging
import time

from arq import cron, func
from arq.connections import RedisSettings
from fastapi import HTTPException
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import ensure_fresh_token
from app.config import get_settings
from app.db import async_session_factory, init_db
from app.jobs import HEARTBEAT_INTERVAL_SECONDS, fail_stale_jobs
from app.models import (
    ACTIVE_JOB_STATUSES,
    JOB_DONE,
    JOB_FAILED,
    JOB_RUNNING,
    ArtistGenreCache,
    SplitJob,
    SplitResultCache,
    TrackAudioFeatures,
    User,
)
from app.pipeline import ALGORITHM_VERSION, run_split_pipeline
from app.tracks import CACHE_TTL_SECONDS

# arq sets up logging for its own "arq" logger only, so the app's lines
# (like the split timing summary) need their own handler. Configuring the
# root logger instead would print every arq line twice.
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logging.getLogger("app").addHandler(_handler)
logging.getLogger("app").setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# Finished jobs are only useful to the frontend that's polling them.
FINISHED_JOB_RETENTION_SECONDS = 7 * 24 * 60 * 60
RESULT_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
GENERIC_ERROR = "Something went wrong while splitting this playlist. Please try again."


async def startup(ctx: dict) -> None:
    await init_db()
    ctx["session_factory"] = async_session_factory


async def _heartbeat(session_factory: async_sessionmaker[AsyncSession], job_id: str) -> None:
    """Proves the worker is alive while a job runs, independent of how long
    any one pipeline stage takes. Own session, so it never interleaves
    with the pipeline's."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        async with session_factory() as db:
            await db.execute(update(SplitJob).where(SplitJob.id == job_id).values(heartbeat_at=time.time()))
            await db.commit()


async def _mark_failed(session_factory: async_sessionmaker[AsyncSession], job_id: str, error: str) -> None:
    # Fresh session: the pipeline's session may be mid-transaction or broken.
    async with session_factory() as db:
        await db.execute(
            update(SplitJob)
            .where(SplitJob.id == job_id)
            .values(status=JOB_FAILED, error=error, stage=None, finished_at=time.time())
        )
        await db.commit()


async def run_split_job(ctx: dict, job_id: str) -> None:
    session_factory = ctx["session_factory"]

    async with session_factory() as db:
        job = await db.get(SplitJob, job_id)
        # Already finished, or failed as stale while it waited: nothing to do.
        # A job that's still "running" here is arq retrying after the
        # previous worker died, so it runs again from the top.
        if job is None or job.status not in ACTIVE_JOB_STATUSES:
            return

        now = time.time()
        job.status = JOB_RUNNING
        job.started_at = now
        job.heartbeat_at = now
        await db.commit()

        heartbeat = asyncio.create_task(_heartbeat(session_factory, job_id))
        try:
            user = await db.get(User, job.user_id)
            if user is None:
                raise HTTPException(status_code=401, detail="Your session is gone. Please log in again.")
            access_token = await ensure_fresh_token(user, db)

            async def progress(stage: str, done: int | None, total: int | None) -> None:
                job.stage = stage
                job.progress_done = done
                job.progress_total = total
                job.heartbeat_at = time.time()
                await db.commit()

            result = await run_split_pipeline(job.playlist_id, access_token, db, progress)
        except HTTPException as e:
            # The pipeline reports failures as HTTPExceptions (it also runs
            # inside a request), and their details are already readable.
            logger.warning("split job %s failed: %s", job_id, e.detail)
            await _mark_failed(session_factory, job_id, str(e.detail))
            return
        except Exception:
            logger.exception("split job %s crashed", job_id)
            await _mark_failed(session_factory, job_id, GENERIC_ERROR)
            return
        finally:
            heartbeat.cancel()

        job.status = JOB_DONE
        job.stage = None
        job.result = result
        job.finished_at = time.time()
        if job.snapshot_id:
            await db.merge(
                SplitResultCache(
                    playlist_id=job.playlist_id,
                    snapshot_id=job.snapshot_id,
                    algorithm_version=ALGORITHM_VERSION,
                    result=result,
                    created_at=time.time(),
                )
            )
        await db.commit()


async def nightly_cleanup(ctx: dict) -> None:
    """Housekeeping that no request should have to wait on: fail jobs whose
    worker died, and drop old jobs and expired cache rows."""
    now = time.time()
    async with ctx["session_factory"]() as db:
        stale = await fail_stale_jobs(db)
        old_jobs = await db.execute(
            delete(SplitJob).where(
                SplitJob.status.in_((JOB_DONE, JOB_FAILED)),
                SplitJob.finished_at < now - FINISHED_JOB_RETENTION_SECONDS,
            )
        )
        old_results = await db.execute(
            delete(SplitResultCache).where(
                (SplitResultCache.algorithm_version != ALGORITHM_VERSION)
                | (SplitResultCache.created_at < now - RESULT_CACHE_TTL_SECONDS)
            )
        )
        old_artist_genres = await db.execute(
            delete(ArtistGenreCache).where(ArtistGenreCache.fetched_at < now - CACHE_TTL_SECONDS)
        )
        old_audio = await db.execute(
            delete(TrackAudioFeatures).where(TrackAudioFeatures.fetched_at < now - CACHE_TTL_SECONDS)
        )
        await db.commit()
    logger.info(
        "nightly cleanup: failed %d stale jobs, deleted %d old jobs, %d cached results, %d audio feature rows, %d artist genre rows",
        stale,
        old_jobs.rowcount,
        old_results.rowcount,
        old_audio.rowcount,
        old_artist_genres.rowcount,
    )


class WorkerSettings:
    functions = [
        # keep_result=0: results live in Postgres, not in arq's Redis keys.
        # max_tries covers a worker dying mid-job (arq re-queues it).
        func(run_split_job, max_tries=3, keep_result=0),
    ]
    cron_jobs = [cron(nightly_cleanup, hour={10}, minute={0})]  # 10:00 UTC, about 3am Vancouver
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379/0")
    # Big playlists wait on Groq's rate limits, so leave plenty of room.
    job_timeout = 15 * 60
    # Groq's free tier is the bottleneck; more parallel jobs just share it.
    max_jobs = 4
