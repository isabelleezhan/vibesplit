from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_session_token
from app.db import get_db
from app.pipeline import run_split_pipeline

router = APIRouter(prefix="/playlists", tags=["clusters"])


@router.post("/{playlist_id}/clusters")
async def get_playlist_clusters(
    playlist_id: str,
    access_token: Annotated[str, Depends(get_session_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """The original synchronous split: runs the whole pipeline inside this
    request. Kept so the in-request time can be measured as a baseline
    against the job queue (POST /playlists/{id}/split-jobs)."""
    return await run_split_pipeline(playlist_id, access_token, db)
