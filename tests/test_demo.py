"""Tests for the no-login demo (app/demo.py), on a small made-up fixture."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import demo
from app.demo import apply_saved_labels
from app.main import app


def make_track(i: int, genre: str, year: int, energy: float, acoustic: float) -> dict:
    return {
        "id": f"t{i}",
        "name": f"Track {i}",
        "is_local": False,
        "popularity": None,
        "release_date": f"{year}-01-01",
        "artists": [{"id": f"a{i}", "name": f"Artist {i}"}],
        "genres": [genre],
        "audio": {"energy": energy, "valence": 0.5, "danceability": 0.5, "acousticness": acoustic},
    }


ROCK = [make_track(i, "rock", 1975, 0.9, 0.1) for i in range(6)]
JAZZ = [make_track(i, "jazz", 2015, 0.2, 0.9) for i in range(6, 12)]


def fixture_with(labels: list[dict]) -> dict:
    return {
        "playlists": [
            {"id": "demo-1", "name": "Sample Mix", "image_url": None, "tracks": ROCK + JAZZ, "labels": labels}
        ]
    }


SAVED_LABELS = [
    {"label": "Loud Seventies Rock", "track_keys": [t["id"] for t in ROCK]},
    {"label": "Quiet Modern Jazz", "track_keys": [t["id"] for t in JAZZ]},
]


@pytest.fixture
def use_fixture(monkeypatch):
    def install(fixture: dict) -> None:
        monkeypatch.setattr(demo, "load_fixture", lambda: fixture)

    monkeypatch.setattr(demo, "_split_cache", {})
    install(fixture_with(SAVED_LABELS))
    return install


@pytest.fixture
async def client():
    # No session cookie and no dependency overrides: the demo needs neither.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


class TestDemoEndpoints:
    async def test_lists_sample_playlists_without_login(self, client, use_fixture):
        response = await client.get("/api/demo/playlists")
        assert response.status_code == 200
        assert response.json() == {
            "total": 1,
            "playlists": [
                {"id": "demo-1", "name": "Sample Mix", "track_count": 12, "owner": "Sample data", "image_url": None}
            ],
        }

    async def test_split_runs_clustering_and_uses_saved_names(self, client, use_fixture):
        with patch("app.labeling.generate_structured", new=AsyncMock()) as llm:
            response = await client.post("/api/demo/playlists/demo-1/split")
        assert response.status_code == 200
        body = response.json()
        assert body["k"] == 2
        by_label = {c["label"]: {t["id"] for t in c["tracks"]} for c in body["clusters"]}
        assert by_label == {
            "Loud Seventies Rock": {t["id"] for t in ROCK},
            "Quiet Modern Jazz": {t["id"] for t in JAZZ},
        }
        assert body["original_coherence"] is not None
        assert all(c["coherence"] is not None for c in body["clusters"])
        llm.assert_not_awaited()

    async def test_unmatched_clusters_get_genre_names_not_an_llm_call(self, client, use_fixture):
        use_fixture(fixture_with([]))
        with patch("app.labeling.generate_structured", new=AsyncMock()) as llm:
            body = (await client.post("/api/demo/playlists/demo-1/split")).json()
        assert {c["label"] for c in body["clusters"]} == {"Rock", "Jazz"}
        llm.assert_not_awaited()

    async def test_result_is_computed_once_then_cached(self, client, use_fixture):
        with patch("app.demo.cluster_tracks", wraps=demo.cluster_tracks) as clustering:
            first = (await client.post("/api/demo/playlists/demo-1/split")).json()
            second = (await client.post("/api/demo/playlists/demo-1/split")).json()
        assert first == second
        assert clustering.call_count == 1

    async def test_unknown_playlist_is_404(self, client, use_fixture):
        assert (await client.post("/api/demo/playlists/nope/split")).status_code == 404

    async def test_missing_fixture_file_means_no_playlists(self, tmp_path):
        assert demo.load_fixture.__wrapped__(tmp_path / "missing.json") == {"playlists": []}


class TestApplySavedLabels:
    def cluster(self, ids: list[str], genre: str = "pop") -> dict:
        return {"cluster_id": 0, "tracks": [{"id": i, "genres": [genre]} for i in ids]}

    def test_close_but_not_exact_match_still_gets_the_saved_name(self):
        # 4 of 5 tracks shared: Jaccard 0.8.
        clusters = [self.cluster(["a", "b", "c", "d", "e"]), self.cluster(["x", "y"])]
        apply_saved_labels(clusters, [{"label": "Saved", "track_keys": ["a", "b", "c", "d"]}])
        assert clusters[0]["label"] == "Saved"
        assert clusters[1]["label"] == "Pop"

    def test_each_saved_name_is_used_once(self):
        clusters = [self.cluster(["a", "b", "c"]), self.cluster(["a", "b", "c", "d"])]
        apply_saved_labels(clusters, [{"label": "Saved", "track_keys": ["a", "b", "c"]}])
        assert [c["label"] for c in clusters] == ["Saved", "Pop"]

    def test_single_cluster_is_all_tracks(self):
        clusters = [self.cluster(["a"])]
        apply_saved_labels(clusters, SAVED_LABELS)
        assert clusters[0]["label"] == "All Tracks"
