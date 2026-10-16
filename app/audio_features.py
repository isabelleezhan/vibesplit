"""Audio features (energy, valence, danceability, acousticness) per track,
from ReccoBeats.

Spotify's own audio-features endpoint is closed to apps created after
Nov 27, 2024, and Spotify no longer returns artist genres either, so
without this every signal about how a track sounds would be an LLM guess.
ReccoBeats serves the same kind of measured numbers keyed by Spotify
track id, free and without an API key.

It's a small third-party service, so it's treated as optional: if it's
down or rate limits us past a few retries, the split still runs on tags
alone, and tracks it doesn't know simply have no audio features.
"""

import asyncio
import logging
import time

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TrackAudioFeatures

logger = logging.getLogger(__name__)

RECCOBEATS_URL = "https://api.reccobeats.com/v1/audio-features"
BATCH_SIZE = 40  # ids per request
MAX_ATTEMPTS = 3

# All on a 0 to 1 scale. 
FEATURE_KEYS = ("energy", "valence", "danceability", "acousticness")


def _spotify_id_from_href(href: str | None) -> str | None:
    # ReccoBeats echoes each track back as an open.spotify.com/track/<id> link.
    if not href or "/track/" not in href:
        return None
    return href.rsplit("/track/", 1)[1].split("?")[0]


async def _fetch_batch(client: httpx.AsyncClient, track_ids: list[str]) -> dict[str, dict] | None:
    """{spotify_id: features} for whichever ids ReccoBeats knows, or None
    if the request itself failed (so the caller doesn't cache a false miss)."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.get(RECCOBEATS_URL, params={"ids": ",".join(track_ids)})
        except httpx.HTTPError as e:
            logger.warning("ReccoBeats request failed: %s", e)
            return None
        if response.status_code == 429 and attempt < MAX_ATTEMPTS:
            await asyncio.sleep(2**attempt)
            continue
        if response.status_code != 200:
            logger.warning("ReccoBeats returned %s: %s", response.status_code, response.text[:200])
            return None

        found = {}
        for entry in response.json().get("content", []):
            track_id = _spotify_id_from_href(entry.get("href"))
            if track_id and all(isinstance(entry.get(key), (int, float)) for key in FEATURE_KEYS):
                found[track_id] = {key: float(entry[key]) for key in FEATURE_KEYS}
        return found
    return None


async def fetch_audio_features(client: httpx.AsyncClient, track_ids: list[str]) -> dict[str, dict | None]:
    """{spotify_id: features or None} for every id in a batch that got an
    answer. Ids from failed batches are left out entirely, so they get
    asked again next time instead of being remembered as unknown."""
    results: dict[str, dict | None] = {}
    for start in range(0, len(track_ids), BATCH_SIZE):
        batch = track_ids[start : start + BATCH_SIZE]
        found = await _fetch_batch(client, batch)
        if found is None:
            continue
        for track_id in batch:
            results[track_id] = found.get(track_id)
    return results


async def get_audio_features(db: AsyncSession, track_ids: set[str], ttl_seconds: float) -> tuple[dict[str, dict | None], int]:
    """Audio features for the given Spotify ids, from the cache where
    possible. Returns ({id: features or None}, number of cache hits)."""
    if not track_ids:
        return {}, 0

    now = time.time()
    rows = await db.execute(select(TrackAudioFeatures).where(TrackAudioFeatures.track_id.in_(track_ids)))
    cached = {row.track_id: row for row in rows.scalars()}
    fresh = {track_id: row.features for track_id, row in cached.items() if now - row.fetched_at < ttl_seconds}

    missing = sorted(track_ids - fresh.keys())
    if missing:
        async with httpx.AsyncClient(timeout=30) as client:
            fetched = await fetch_audio_features(client, missing)
        for track_id, features in fetched.items():
            row = cached.get(track_id)
            if row:
                row.features = features
                row.fetched_at = now
            else:
                db.add(TrackAudioFeatures(track_id=track_id, features=features, fetched_at=now))
        await db.commit()
        fresh.update(fetched)

    return fresh, len(track_ids) - len(missing)
