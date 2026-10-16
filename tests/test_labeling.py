"""Unit tests for app/labeling.py"""

from unittest.mock import AsyncMock, patch

import pytest

from app.labeling import _build_prompt, _ClusterLabel, _LabelingResponse, fallback_label, label_clusters


def make_cluster(cluster_id: int, track_ids: list[str], genres: list[str] | None = None) -> dict:
    tracks = [
        {"id": tid, "name": tid, "artists": [], "genres": genres or [], "audio": None, "release_date": None}
        for tid in track_ids
    ]
    return {"cluster_id": cluster_id, "track_count": len(tracks), "tracks": tracks}


def cluster_result(clusters: list[dict]) -> dict:
    return {"k": len(clusters), "silhouette_score": 0.5, "clusters": clusters}


@pytest.fixture
def mock_llm():
    """Patches the actual network call so tests control exactly what the
    'LLM' returns, without touching the real API."""
    with patch("app.labeling.generate_structured", new=AsyncMock()) as mocked:
        yield mocked


class TestLabelClusters:
    async def test_single_cluster_skips_the_llm_call(self, mock_llm):
        result = await label_clusters(cluster_result([make_cluster(0, ["t1", "t2"])]))
        assert result["clusters"][0]["label"] == "All Tracks"
        mock_llm.assert_not_called()

    async def test_labels_get_applied_to_the_right_clusters(self, mock_llm):
        clusters = [make_cluster(0, ["t1", "t2"]), make_cluster(1, ["t3", "t4"])]
        mock_llm.return_value = _LabelingResponse(
            cluster_labels=[
                _ClusterLabel(cluster_id=0, label="Late-Night House"),
                _ClusterLabel(cluster_id=1, label="90s Rock"),
            ]
        )
        result = await label_clusters(cluster_result(clusters))
        labels_by_id = {c["cluster_id"]: c["label"] for c in result["clusters"]}
        assert labels_by_id == {0: "Late-Night House", 1: "90s Rock"}

    async def test_tracks_are_never_moved(self, mock_llm):
        clusters = [make_cluster(0, ["t1", "t2"]), make_cluster(1, ["t3"])]
        mock_llm.return_value = _LabelingResponse(cluster_labels=[])
        result = await label_clusters(cluster_result(clusters))
        assert [[t["id"] for t in c["tracks"]] for c in result["clusters"]] == [["t1", "t2"], ["t3"]]

    async def test_missing_label_falls_back_to_genre_name(self, mock_llm):
        clusters = [make_cluster(0, ["t1"]), make_cluster(1, ["t2"], genres=["k-pop", "pop"])]
        mock_llm.return_value = _LabelingResponse(cluster_labels=[_ClusterLabel(cluster_id=0, label="Only This One")])
        result = await label_clusters(cluster_result(clusters))
        labels_by_id = {c["cluster_id"]: c["label"] for c in result["clusters"]}
        assert labels_by_id == {0: "Only This One", 1: "K-Pop / Pop"}

    async def test_llm_failure_does_not_fail_the_split(self, mock_llm):
        clusters = [make_cluster(0, ["t1"], genres=["r&b"]), make_cluster(1, ["t2"])]
        mock_llm.side_effect = RuntimeError("Groq is down")
        result = await label_clusters(cluster_result(clusters))
        labels_by_id = {c["cluster_id"]: c["label"] for c in result["clusters"]}
        assert labels_by_id == {0: "R&B", 1: "Cluster 1"}


def test_fallback_label_uses_the_two_most_common_genres():
    cluster = make_cluster(0, ["t1", "t2"], genres=["pop", "k-pop"])
    cluster["tracks"][1]["genres"] = ["k-pop"]
    assert fallback_label(cluster) == "K-Pop / Pop"


def test_prompt_includes_average_audio():
    cluster = make_cluster(0, ["t1", "t2"])
    cluster["tracks"][0]["audio"] = {"energy": 0.2, "valence": 0.4, "danceability": 0.6, "acousticness": 0.8}
    cluster["tracks"][1]["audio"] = {"energy": 0.4, "valence": 0.4, "danceability": 0.6, "acousticness": 0.8}
    assert "energy 0.30" in _build_prompt([cluster])
