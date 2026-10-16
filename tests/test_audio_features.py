"""Tests for app/audio_features.py. ReccoBeats is faked with
httpx.MockTransport and the cache runs on a throwaway SQLite file."""

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.audio_features import (
    BATCH_SIZE,
    FEATURE_KEYS,
    MAX_ATTEMPTS,
    _spotify_id_from_href,
    fetch_audio_features,
    get_audio_features,
)
from app.db import Base
from app.models import TrackAudioFeatures

TTL = 30 * 24 * 60 * 60


def entry(track_id: str, **overrides) -> dict:
    features = {key: 0.5 for key in FEATURE_KEYS}
    return {"href": f"https://open.spotify.com/track/{track_id}", "tempo": 120.0, **features, **overrides}


def reccobeats(known: dict[str, dict], statuses: list[int] | None = None):
    """Fake ReccoBeats that answers with `known` entries for requested ids.
    `statuses`, if given, are returned in order before normal answers."""
    requests = []
    statuses = list(statuses or [])

    def handler(request):
        ids = request.url.params["ids"].split(",")
        requests.append(ids)
        if statuses:
            return httpx.Response(statuses.pop(0), json={})
        return httpx.Response(200, json={"content": [known[i] for i in ids if i in known]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), requests


class TestSpotifyIdFromHref:
    def test_plain_link(self):
        assert _spotify_id_from_href("https://open.spotify.com/track/abc123") == "abc123"

    def test_strips_query(self):
        assert _spotify_id_from_href("https://open.spotify.com/track/abc123?si=x") == "abc123"

    def test_missing(self):
        assert _spotify_id_from_href(None) is None
        assert _spotify_id_from_href("https://open.spotify.com/album/abc") is None


async def test_known_and_unknown_tracks():
    client, _ = reccobeats({"a": entry("a", energy=0.9)})
    result = await fetch_audio_features(client, ["a", "b"])
    assert result["a"]["energy"] == 0.9
    assert set(result["a"]) == set(FEATURE_KEYS)  # tempo and the rest dropped
    assert result["b"] is None


async def test_batches_requests():
    ids = [f"t{i}" for i in range(BATCH_SIZE + 5)]
    client, requests = reccobeats({i: entry(i) for i in ids})
    result = await fetch_audio_features(client, ids)
    assert [len(r) for r in requests] == [BATCH_SIZE, 5]
    assert all(result[i] for i in ids)


async def test_entry_with_missing_feature_counts_as_unknown():
    client, _ = reccobeats({"a": entry("a", valence=None)})
    assert (await fetch_audio_features(client, ["a"]))["a"] is None


async def test_429_is_retried():
    client, requests = reccobeats({"a": entry("a")}, statuses=[429])
    with patch("app.audio_features.asyncio.sleep", new=AsyncMock()):
        result = await fetch_audio_features(client, ["a"])
    assert result["a"] is not None
    assert len(requests) == 2


async def test_failed_batch_is_left_out_not_marked_unknown():
    client, requests = reccobeats({}, statuses=[500])
    assert await fetch_audio_features(client, ["a"]) == {}
    assert len(requests) == 1

    client, requests = reccobeats({}, statuses=[429] * MAX_ATTEMPTS)
    with patch("app.audio_features.asyncio.sleep", new=AsyncMock()):
        assert await fetch_audio_features(client, ["a"]) == {}
    assert len(requests) == MAX_ATTEMPTS


@pytest.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_cache_hits_skip_the_api_and_misses_are_saved(session_factory):
    async with session_factory() as db:
        db.add(TrackAudioFeatures(track_id="cached", features={"energy": 0.1}, fetched_at=time.time()))
        db.add(TrackAudioFeatures(track_id="stale", features={"energy": 0.2}, fetched_at=time.time() - TTL - 1))
        await db.commit()

    fetch = AsyncMock(return_value={"stale": {"energy": 0.3}, "new": None})
    async with session_factory() as db:
        with patch("app.audio_features.fetch_audio_features", new=fetch):
            features, hits = await get_audio_features(db, {"cached", "stale", "new"}, TTL)

    assert fetch.await_args.args[1] == ["new", "stale"]
    assert hits == 1
    assert features == {"cached": {"energy": 0.1}, "stale": {"energy": 0.3}, "new": None}

    async with session_factory() as db:
        assert (await db.get(TrackAudioFeatures, "stale")).features == {"energy": 0.3}
        new_row = await db.get(TrackAudioFeatures, "new")
        assert new_row is not None and new_row.features is None
