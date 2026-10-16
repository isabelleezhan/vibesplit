import time
import uuid
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_db
from app.models import (
    ACTIVE_JOB_STATUSES,
    JOB_DONE,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    SplitJob,
    SplitResultCache,
    User,
)
from app.pipeline import ALGORITHM_VERSION
from app.tracks import get_playlist_snapshot_id

router = APIRouter(tags=["jobs"])

# The worker bumps heartbeat_at every HEARTBEAT_INTERVAL_SECONDS while a
# job runs, from a background task that keeps ticking even while the
# pipeline is stuck waiting on a Groq rate limit. So a quiet heartbeat
# means the worker is gone, not that the job is slow.
HEARTBEAT_INTERVAL_SECONDS = 15
RUNNING_STALE_SECONDS = 120
# A queued job nobody picks up usually means no worker is running.
QUEUED_STALE_SECONDS = 10 * 60

STALE_RUNNING_ERROR = "The worker stopped responding partway through this split. Please try again."
STALE_QUEUED_ERROR = "No worker picked up this split. Please try again in a minute."


async def fail_stale_jobs(db: AsyncSession) -> int:
    """Marks every stale active job failed. Returns how many it caught."""
    now = time.time()
    running = await db.execute(
        update(SplitJob)
        .where(SplitJob.status == JOB_RUNNING, SplitJob.heartbeat_at < now - RUNNING_STALE_SECONDS)
        .values(status=JOB_FAILED, error=STALE_RUNNING_ERROR, finished_at=now)
    )
    queued = await db.execute(
        update(SplitJob)
        .where(SplitJob.status == JOB_QUEUED, SplitJob.created_at < now - QUEUED_STALE_SECONDS)
        .values(status=JOB_FAILED, error=STALE_QUEUED_ERROR, finished_at=now)
    )
    await db.commit()
    return running.rowcount + queued.rowcount


async def _fail_if_stale(job: SplitJob, db: AsyncSession) -> None:
    now = time.time()
    if job.status == JOB_RUNNING and job.heartbeat_at is not None and job.heartbeat_at < now - RUNNING_STALE_SECONDS:
        job.error = STALE_RUNNING_ERROR
    elif job.status == JOB_QUEUED and job.created_at < now - QUEUED_STALE_SECONDS:
        job.error = STALE_QUEUED_ERROR
    else:
        return
    job.status = JOB_FAILED
    job.finished_at = now
    await db.commit()


def serialize_job(job: SplitJob) -> dict:
    return {
        "id": job.id,
        "playlist_id": job.playlist_id,
        "status": job.status,
        "stage": job.stage,
        "progress": (
            {"done": job.progress_done, "total": job.progress_total} if job.progress_total is not None else None
        ),
        "cache_hit": job.cache_hit,
        "error": job.error,
        # Only sent once there is one: it's the full cluster payload.
        "result": job.result if job.status == JOB_DONE else None,
    }


async def _active_job(db: AsyncSession, user_id: str, playlist_id: str) -> SplitJob | None:
    result = await db.execute(
        select(SplitJob).where(
            SplitJob.user_id == user_id,
            SplitJob.playlist_id == playlist_id,
            SplitJob.status.in_(ACTIVE_JOB_STATUSES),
        )
    )
    return result.scalar_one_or_none()


def get_queue(request: Request) -> ArqRedis:
    """FastAPI dependency: the arq Redis pool opened in app/main.py's lifespan."""
    return request.app.state.arq


@router.post("/playlists/{playlist_id}/split-jobs", status_code=202)
async def start_split_job(
    playlist_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    queue: Annotated[ArqRedis, Depends(get_queue)],
) -> dict:
    """Starts splitting a playlist in the background and returns right
    away with a job to poll via GET /jobs/{id}. Returns an already
    finished job if this exact version of the playlist was split before,
    and the existing job if one is already running for it."""
    snapshot_id = await get_playlist_snapshot_id(playlist_id, user.access_token)
    now = time.time()

    cached = await db.get(SplitResultCache, (playlist_id, snapshot_id, ALGORITHM_VERSION))
    if cached:
        job = SplitJob(
            id=uuid.uuid4().hex,
            user_id=user.spotify_id,
            playlist_id=playlist_id,
            snapshot_id=snapshot_id,
            status=JOB_DONE,
            result=cached.result,
            cache_hit=True,
            created_at=now,
            started_at=now,
            finished_at=now,
        )
        db.add(job)
        await db.commit()
        return serialize_job(job)

    existing = await _active_job(db, user.spotify_id, playlist_id)
    if existing:
        await _fail_if_stale(existing, db)
        if existing.status in ACTIVE_JOB_STATUSES:
            return serialize_job(existing)

    job = SplitJob(
        id=uuid.uuid4().hex,
        user_id=user.spotify_id,
        playlist_id=playlist_id,
        snapshot_id=snapshot_id,
        status=JOB_QUEUED,
        cache_hit=False,
        created_at=now,
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError:
        # Lost a race with a second click: the other request's job won
        # the unique index, so hand back that one instead.
        await db.rollback()
        existing = await _active_job(db, user.spotify_id, playlist_id)
        if existing is None:
            raise
        return serialize_job(existing)

    try:
        # Using our id as arq's id means arq won't queue it twice either.
        await queue.enqueue_job("run_split_job", job.id, _job_id=job.id)
    except Exception as e:
        job.status = JOB_FAILED
        job.error = "Couldn't reach the job queue."
        job.finished_at = time.time()
        await db.commit()
        raise HTTPException(status_code=503, detail="Couldn't reach the job queue. Please try again.") from e

    return serialize_job(job)


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    job = await db.get(SplitJob, job_id)
    # Someone else's job gets the same 404 as a missing one.
    if job is None or job.user_id != user.spotify_id:
        raise HTTPException(status_code=404, detail="No such job.")
    await _fail_if_stale(job, db)
    return serialize_job(job)
