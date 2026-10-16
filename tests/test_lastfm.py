"""Tests for app/lastfm.py. Last.fm is faked with httpx.MockTransport
and the cache runs on a throwaway SQLite file."""

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.lastfm import (
    MAX_ATTEMPTS,
    MAX_GENRES_PER_ARTIST,
    fetch_artist_genres,
    genres_from_tags,
    get_artist_genres,
    normalize_genre,
    track_genres,
)
from app.models import ArtistGenreCache

TTL = 30 * 24 * 60 * 60


def tag(name: str, count: int = 100) -> dict:
    return {"name": name, "count": count}


class TestNormalizeGenre:
    def test_known_genre(self):
        assert normalize_genre("Indie Rock") == "indie rock"

    def test_aliases(self):
        assert normalize_genre("kpop") == "k-pop"
        assert normalize_genre("RnB") == "r&b"
        assert normalize_genre("Hip-Hop") == "hip hop"

    def test_non_genres_dropped(self):
        assert normalize_genre("female vocalists") is None
        assert normalize_genre("petala owns this song") is None


class TestGenresFromTags:
    def test_keeps_order_and_dedupes_aliases(self):
        tags = [tag("k-pop"), tag("kpop", 90), tag("korean", 80), tag("pop", 50)]
        assert genres_from_tags(tags) == ["k-pop", "pop"]

    def test_drops_rarely_used_tags(self):
        assert genres_from_tags([tag("pop"), tag("metal", 3)]) == ["pop"]

    def test_caps_genre_count(self):
        tags = [tag(g) for g in ["pop", "rock", "jazz", "soul", "funk", "disco"]]
        assert len(genres_from_tags(tags)) == MAX_GENRES_PER_ARTIST

    def test_bad_count_skipped(self):
        assert genres_from_tags([{"name": "pop", "count": "lots"}, tag("rock")]) == ["rock"]


def lastfm(responses: list):
    """Fake Last.fm that answers each request with the next item: a
    (status, json) pair, or an exception to raise."""
    responses = list(responses)
    calls = []

    def handler(request):
        calls.append(request.url.params["artist"])
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


async def test_fetch_parses_tags():
    client, _ = lastfm([(200, {"toptags": {"tag": [tag("rnb"), tag("soul", 60)]}})])
    assert await fetch_artist_genres(client, "Daniel Caesar", "key") == ["r&b", "soul"]


async def test_unknown_artist_is_a_real_empty_answer():
    client, _ = lastfm([(200, {"error": 6, "message": "The artist you supplied could not be found"})])
    assert await fetch_artist_genres(client, "Nobody", "key") == []


async def test_timeouts_are_retried():
    client, calls = lastfm([httpx.ReadTimeout("slow"), (200, {"toptags": {"tag": [tag("pop")]}})])
    assert await fetch_artist_genres(client, "A", "key") == ["pop"]
    assert len(calls) == 2


async def test_rate_limit_retried_then_gives_up():
    client, calls = lastfm([(200, {"error": 29, "message": "Rate limit exceeded"})] * MAX_ATTEMPTS)
    with patch("app.lastfm.asyncio.sleep", new=AsyncMock()):
        assert await fetch_artist_genres(client, "A", "key") is None
    assert len(calls) == MAX_ATTEMPTS


async def test_other_errors_are_failures_not_empty_answers():
    client, calls = lastfm([(200, {"error": 10, "message": "Invalid API key"})])
    assert await fetch_artist_genres(client, "A", "key") is None
    assert len(calls) == 1


class TestTrackGenres:
    def test_lead_artist_first_then_features(self):
        track = {"artists": [{"id": "a1"}, {"id": "a2"}]}
        genres = {"a1": ["k-pop", "pop"], "a2": ["hip hop", "pop"]}
        assert track_genres(track, genres) == ["k-pop", "pop", "hip hop"]

    def test_unknown_artists(self):
        assert track_genres({"artists": [{"id": "a1"}, {"id": None}]}, {}) == []


@pytest.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def with_key(key: str):
    return patch("app.lastfm.get_settings", return_value=type("S", (), {"lastfm_api_key": key})())


async def test_no_api_key_skips_last_fm(session_factory):
    fetch = AsyncMock()
    async with session_factory() as db:
        with with_key(""), patch("app.lastfm.fetch_artist_genres", new=fetch):
            assert await get_artist_genres(db, {"a1": "A"}, TTL) == ({}, 0)
    fetch.assert_not_awaited()


async def test_cache_hits_skip_last_fm_and_failures_are_not_cached(session_factory):
    async with session_factory() as db:
        db.add(ArtistGenreCache(artist_id="cached", genres=["pop"], fetched_at=time.time()))
        await db.commit()

    answers = {"New": ["rock"], "Unknown": [], "Broken": None}
    fetch = AsyncMock(side_effect=lambda client, name, key: answers[name])
    artists = {"cached": "Cached", "new": "New", "unknown": "Unknown", "broken": "Broken"}
    async with session_factory() as db:
        with with_key("key"), patch("app.lastfm.fetch_artist_genres", new=fetch):
            genres, hits = await get_artist_genres(db, artists, TTL)

    assert hits == 1
    assert genres == {"cached": ["pop"], "new": ["rock"], "unknown": []}
    assert fetch.await_count == 3
    async with session_factory() as db:
        assert (await db.get(ArtistGenreCache, "unknown")).genres == []
        assert await db.get(ArtistGenreCache, "broken") is None
