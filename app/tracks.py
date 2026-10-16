import asyncio
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio_features import get_audio_features
from app.auth import get_session_token
from app.db import get_db
from app.lastfm import get_artist_genres, track_genres
from app.timing import StageTimer

router = APIRouter(prefix="/playlists", tags=["tracks"])

SPOTIFY_API_BASE = "https://api.spotify.com/v1"

# How long a cached audio-features or artist-genre entry stays valid
# before it's treated as a miss and fetched again.
CACHE_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

# Spotify answers 429 with a Retry-After header (seconds). Honour it a few
# times before giving up, but cap each wait so one bad header can't stall
# a job for minutes.
SPOTIFY_MAX_ATTEMPTS = 4
SPOTIFY_MAX_RETRY_WAIT_SECONDS = 30


async def _spotify_get(client: httpx.AsyncClient, url: str, access_token: str, params: dict | None = None) -> httpx.Response:
    """GET against the Spotify API, waiting out 429 rate limits."""
    for attempt in range(1, SPOTIFY_MAX_ATTEMPTS + 1):
        response = await client.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params)
        if response.status_code != 429 or attempt == SPOTIFY_MAX_ATTEMPTS:
            return response
        try:
            wait_seconds = float(response.headers.get("Retry-After", ""))
        except ValueError:
            wait_seconds = 2 ** attempt
        await asyncio.sleep(min(wait_seconds, SPOTIFY_MAX_RETRY_WAIT_SECONDS))
    return response


async def get_playlist_snapshot_id(playlist_id: str, access_token: str) -> str:
    """Spotify's version stamp for a playlist, which changes on every edit.
    Also doubles as an access check: a playlist the user can't read 404s."""
    async with httpx.AsyncClient() as client:
        response = await _spotify_get(
            client, f"{SPOTIFY_API_BASE}/playlists/{playlist_id}", access_token, params={"fields": "snapshot_id"}
        )
    if response.status_code != 200:
        raise HTTPException(
            # Pass "no access" / "no such playlist" through; anything else is Spotify's problem.
            status_code=response.status_code if response.status_code in (403, 404) else 502,
            detail=f"Spotify playlist request failed: {response.status_code} {response.text}",
        )
    return response.json()["snapshot_id"]


async def _get_all_playlist_items(client: httpx.AsyncClient, playlist_id: str, access_token: str) -> list[dict]:
    """Page through a playlist's tracks. Spotify caps each page at 100 items
    and gives a `next` URL for the following page, or null when done."""
    items: list[dict] = []
    url = f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/items"
    params = {"limit": 100}

    # Keep fetching pages until Spotify stops giving a `next` URL.
    while url:
        response = await _spotify_get(client, url, access_token, params=params)
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Spotify playlist items request failed: {response.status_code} {response.text}",
            )
        data = response.json()
        items.extend(data.get("items", []))
        url = data.get("next")
        params = None

    return items


async def get_enriched_playlist_tracks(
    playlist_id: str,
    access_token: str,
    db: AsyncSession,
    timer: StageTimer | None = None,
) -> list[dict]:
    """Every track in the playlist, with Last.fm genres (`genres`, from its
    artists' listener tags) and measured audio features (`audio`, None when
    ReccoBeats doesn't know the track). Both are cached, so re-splitting
    mostly skips the network.

    There's no LLM in this step. It used to tag every track's genres and
    moods, which was slow (about 14 minutes for 790 new tracks) and noisy.
    With Last.fm genres and ReccoBeats audio in place, dropping the mood
    tags changed almost no splits.
    """
    timer = timer or StageTimer()

    with timer.stage("fetch"):
        async with httpx.AsyncClient() as client:
            items = await _get_all_playlist_items(client, playlist_id, access_token)

    tracks = []
    for item in items:
        track = item.get("item")
        if not track:
            continue  # e.g. a track that was removed from Spotify entirely.

        artists = [
            {"id": a.get("id"), "name": a.get("name")}
            for a in (track.get("artists") or [])
            if a
        ]

        tracks.append(
            {
                "id": track.get("id"),
                "name": track.get("name"),
                "is_local": item.get("is_local", False),
                "popularity": track.get("popularity"),
                "release_date": (track.get("album") or {}).get("release_date"),
                "artists": artists,
            }
        )

    # Local files have no Spotify id, so neither source can look them up.
    track_ids = {track["id"] for track in tracks if track["id"]}

    with timer.stage("audio"):
        audio_by_id, audio_cache_hits = await get_audio_features(db, track_ids, CACHE_TTL_SECONDS)

    with timer.stage("genres"):
        artist_names = {a["id"]: a["name"] for t in tracks for a in t["artists"] if a["id"] and a["name"]}
        genres_by_artist, artist_cache_hits = await get_artist_genres(db, artist_names, CACHE_TTL_SECONDS)

    for track in tracks:
        track["genres"] = sorted(track_genres(track, genres_by_artist))
        track["audio"] = audio_by_id.get(track["id"])

    timer.counts["tracks"] = len(tracks)
    timer.counts["audio_found"] = sum(1 for track in tracks if track["audio"])
    timer.counts["audio_cache_hits"] = audio_cache_hits
    timer.counts["genre_found"] = sum(1 for track in tracks if track["genres"])
    timer.counts["artist_genre_cache_hits"] = artist_cache_hits
    return tracks


@router.get("/{playlist_id}/tracks")
async def list_playlist_tracks(
    playlist_id: str,
    access_token: Annotated[str, Depends(get_session_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tracks = await get_enriched_playlist_tracks(playlist_id, access_token, db)
    return {"total": len(tracks), "tracks": tracks}
