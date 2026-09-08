from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_session_token

router = APIRouter(prefix="/playlists", tags=["playlists"])

SPOTIFY_API_BASE = "https://api.spotify.com/v1"


@router.get("")
async def list_playlists(access_token: Annotated[str, Depends(get_session_token)]) -> dict:
    """Let the user see their playlists so they can pick which overgrown one to split."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{SPOTIFY_API_BASE}/me/playlists",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"limit": 50},
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Spotify playlists request failed: {response.status_code} {response.text}",
        )

    data = response.json()

    playlists = []
    for item in data.get("items", []):
        if not item:
            continue
        images = item.get("images") or []  # empty/auto-gen playlists have no cover
        playlists.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                
                "track_count": (item.get("items") or {}).get("total"),
                "owner": (item.get("owner") or {}).get("display_name"),
                "image_url": images[0].get("url") if images else None,
            }
        )

    return {"total": data.get("total"), "playlists": playlists}
