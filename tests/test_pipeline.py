"""Tests for app/pipeline.py's timing/progress reporting and the Spotify
429 handling in app/tracks.py."""

from unittest.mock import AsyncMock, patch

import httpx

from app.pipeline import run_split_pipeline
from app.tracks import SPOTIFY_MAX_ATTEMPTS, _spotify_get


async def fake_enrich(playlist_id, access_token, db, timer):
    with timer.stage("fetch"):
        pass
    timer.counts["tracks"] = 2
    return [{"id": "t1"}, {"id": "t2"}]


async def test_reports_stages_in_order_and_returns_timings():
    stages = []

    async def progress(stage, done, total):
        stages.append((stage, done, total))

    with (
        patch("app.pipeline.get_enriched_playlist_tracks", new=fake_enrich),
        patch("app.pipeline.cluster_tracks", return_value={"k": 1, "clusters": []}),
        patch("app.pipeline.label_clusters", new=AsyncMock(return_value={"k": 1, "clusters": []})),
        patch("app.pipeline.compute_coherence", return_value=0.5),
        patch("app.pipeline.playlist_year_range", return_value=None),
    ):
        result = await run_split_pipeline("pl1", "tok", db=None, progress=progress)

    assert stages == [
        ("fetching", None, None),
        ("clustering", None, None),
        ("labeling", None, None),
    ]
    assert {"fetch", "cluster", "label", "total", "tracks", "clusters"} <= result["timings"].keys()


def spotify_client(statuses: list[int]) -> tuple[httpx.AsyncClient, list[int]]:
    calls = []

    def handler(request):
        status = statuses[len(calls)]
        calls.append(status)
        return httpx.Response(status, headers={"Retry-After": "1"}, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


async def test_spotify_429_is_retried_until_it_succeeds():
    client, calls = spotify_client([429, 429, 200])
    with patch("app.tracks.asyncio.sleep", new=AsyncMock()) as sleep:
        response = await _spotify_get(client, "https://api.spotify.com/v1/x", "tok")
    assert response.status_code == 200
    assert calls == [429, 429, 200]
    sleep.assert_awaited_with(1.0)


async def test_spotify_429_gives_up_after_max_attempts():
    client, calls = spotify_client([429] * SPOTIFY_MAX_ATTEMPTS)
    with patch("app.tracks.asyncio.sleep", new=AsyncMock()):
        response = await _spotify_get(client, "https://api.spotify.com/v1/x", "tok")
    assert response.status_code == 429
    assert len(calls) == SPOTIFY_MAX_ATTEMPTS


async def test_other_errors_are_not_retried():
    client, calls = spotify_client([404])
    response = await _spotify_get(client, "https://api.spotify.com/v1/x", "tok")
    assert response.status_code == 404
    assert calls == [404]
