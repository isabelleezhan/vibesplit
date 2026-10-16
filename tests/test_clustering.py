"""Unit tests for app/clustering.py"""

import pytest

from app.clustering import (
    MIN_CLUSTER_FRACTION,
    MIN_TRACKS_FOR_COHERENCE,
    VECTOR_AUDIO_WEIGHT,
    _extract_year,
    _normalize,
    _numeric_distance,
    _pairwise_distance,
    _tag_overlap_distance,
    build_feature_matrix,
    cluster_tracks,
    compute_coherence,
)


def make_track(genre: str | None, year_norm: float = 0.5) -> dict:
    return {"genres": [genre] if genre else [], "_year_norm": year_norm}


class TestExtractYear:
    def test_valid_date(self):
        assert _extract_year("2020-05-14") == 2020.0

    def test_none(self):
        assert _extract_year(None) is None

    def test_empty_string(self):
        assert _extract_year("") is None

    def test_malformed(self):
        assert _extract_year("not-a-date") is None


class TestNormalize:
    def test_spread(self):
        assert _normalize([2000.0, 2010.0, 2020.0]) == [0.0, 0.5, 1.0]

    def test_all_same_value(self):
        assert _normalize([2020.0, 2020.0]) == [0.0, 0.0]

    def test_all_none(self):
        assert _normalize([None, None]) == [None, None]

    def test_mixed_none_preserved(self):
        result = _normalize([2000.0, None, 2020.0])
        assert result[1] is None
        assert result[0] == 0.0
        assert result[2] == 1.0


class TestNumericDistance:
    def test_both_present(self):
        assert _numeric_distance(0.2, 0.8) == pytest.approx(0.6)

    def test_either_missing_falls_back_to_neutral(self):
        from app.clustering import NEUTRAL_DISTANCE

        assert _numeric_distance(None, 0.5) == NEUTRAL_DISTANCE
        assert _numeric_distance(0.5, None) == NEUTRAL_DISTANCE
        assert _numeric_distance(None, None) == NEUTRAL_DISTANCE


class TestTagOverlapDistance:
    def test_identical_sets(self):
        assert _tag_overlap_distance({"pop", "r&b"}, {"pop", "r&b"}) == 0.0

    def test_disjoint_sets(self):
        assert _tag_overlap_distance({"pop"}, {"metal"}) == 1.0

    def test_partial_overlap(self):
        # 1 shared / 3 total union -> distance 1 - 1/3
        assert _tag_overlap_distance({"pop", "r&b"}, {"pop", "hip hop"}) == pytest.approx(2 / 3)

    def test_either_side_empty_is_none_not_maximal_distance(self):
        assert _tag_overlap_distance(set(), {"pop"}) is None
        assert _tag_overlap_distance({"pop"}, set()) is None
        assert _tag_overlap_distance(set(), set()) is None


class TestPairwiseDistance:
    def test_identical_genre_and_era(self):
        assert _pairwise_distance(make_track("house"), make_track("house")) == 0.0

    def test_different_era_is_not_identical(self):
        assert _pairwise_distance(make_track("house", 0.0), make_track("house", 1.0)) > 0

    def test_missing_genre_data_is_dropped_not_penalized(self):
        assert _pairwise_distance(make_track("house"), make_track(None)) == 0.0

    def test_genre_dominates_over_era(self):
        from app.clustering import ERA_WEIGHT, GENRE_WEIGHT

        same_genre_diff_era = _pairwise_distance(make_track("house", 0.0), make_track("house", 1.0))
        same_era_diff_genre = _pairwise_distance(make_track("house"), make_track("jazz"))
        assert GENRE_WEIGHT > ERA_WEIGHT  # sanity-check the config itself
        assert same_era_diff_genre > same_genre_diff_era


class TestComputeCoherence:
    def test_below_minimum_returns_none(self):
        tracks = [make_track("pop") for _ in range(MIN_TRACKS_FOR_COHERENCE - 1)]
        assert compute_coherence(tracks) is None

    def test_identical_tracks_are_maximally_coherent(self):
        tracks = [{"genres": ["pop"], "release_date": "2020-01-01"} for _ in range(5)]
        assert compute_coherence(tracks) == 1.0

    def test_scattered_tracks_score_lower_than_tight_ones(self):
        tight = [{"genres": ["pop"], "release_date": "2020-01-01"} for _ in range(5)]
        scattered = [
            {"genres": [g], "release_date": d}
            for g, d in [
                ("pop", "2020-01-01"),
                ("metal", "1995-01-01"),
                ("jazz", "1960-01-01"),
                ("house", "2015-01-01"),
                ("folk", "1975-01-01"),
            ]
        ]
        assert compute_coherence(tight) > compute_coherence(scattered)


class TestClusterTracks:
    def _tracks(self, n: int, genre: str, year: int) -> list[dict]:
        return [
            {"id": f"{genre}{i}", "genres": [genre], "release_date": f"{year}-01-01"}
            for i in range(n)
        ]

    def test_too_few_tracks_falls_back_to_single_cluster(self):
        # MIN_CLUSTERS is 2, so fewer than 3 tracks can't be meaningfully split.
        result = cluster_tracks(self._tracks(2, "pop", 2020))
        assert result["k"] == 1
        assert len(result["clusters"]) == 1

    def test_three_distinct_groups_split_into_three_clusters(self):
        tracks = self._tracks(8, "pop", 2020) + self._tracks(8, "metal", 1995) + self._tracks(8, "jazz", 1960)
        result = cluster_tracks(tracks)
        assert result["k"] == 3
        sizes = sorted(c["track_count"] for c in result["clusters"])
        assert sizes == [8, 8, 8]

    def test_small_clusters_are_allowed(self):
        tracks = self._tracks(4, "pop", 2020) + self._tracks(4, "metal", 1995) + self._tracks(4, "jazz", 1960)
        result = cluster_tracks(tracks)
        assert result["k"] == 3
        sizes = sorted(c["track_count"] for c in result["clusters"])
        assert sizes == [4, 4, 4]

    def test_every_track_is_accounted_for(self):
        tracks = self._tracks(5, "pop", 2020) + self._tracks(5, "metal", 1995)
        result = cluster_tracks(tracks)
        total = sum(c["track_count"] for c in result["clusters"])
        assert total == len(tracks)

    def test_fully_identical_tracks_still_partition_cleanly(self):
        tracks = [{"id": f"t{i}", "genres": ["pop"], "release_date": "2020-01-01"} for i in range(6)]
        result = cluster_tracks(tracks)
        assert sum(c["track_count"] for c in result["clusters"]) == len(tracks)


    def test_audio_separates_tracks_with_the_same_tags(self):
        loud = [{**t, "audio": {"energy": 0.9, "valence": 0.8, "danceability": 0.9, "acousticness": 0.05}}
                for t in self._tracks(10, "pop", 2020)]
        soft = [{**t, "id": f"soft{i}", "audio": {"energy": 0.2, "valence": 0.2, "danceability": 0.3, "acousticness": 0.9}}
                for i, t in enumerate(self._tracks(10, "pop", 2020))]
        result = cluster_tracks(loud + soft)
        assert result["k"] == 2
        groups = sorted(sorted(t["id"] for t in c["tracks"]) for c in result["clusters"])
        assert groups == sorted([sorted(t["id"] for t in loud), sorted(t["id"] for t in soft)])

    def test_tiny_outlier_group_is_not_split_off(self):
        # 2 of 62 tracks is under MIN_CLUSTER_FRACTION, so "everything +
        # the 2 odd ones" isn't an acceptable split.
        assert 2 < MIN_CLUSTER_FRACTION * 62
        tracks = self._tracks(60, "pop", 2020) + self._tracks(2, "metal", 1995)
        result = cluster_tracks(tracks)
        assert result["k"] == 1


class TestBuildFeatureMatrix:
    AUDIO = {"energy": 1.0, "valence": 0.0, "danceability": 1.0, "acousticness": 0.0}

    def test_one_row_per_track_and_tags_scaled_to_unit_length(self):
        tracks = [
            {"genres": ["pop", "k-pop"], "audio": self.AUDIO, "release_date": "2020"},
            {"genres": ["rock"], "audio": self.AUDIO, "release_date": "2010"},
        ]
        matrix = build_feature_matrix(tracks)
        # 3 genres + 4 audio + 1 era
        assert matrix.shape == (2, 8)
        assert matrix[0, :3] == pytest.approx([2 ** -0.5, 2 ** -0.5, 0])

    def test_missing_audio_is_filled_with_playlist_average(self):
        other = {**self.AUDIO, "energy": 0.0}
        tracks = [
            {"genres": ["pop"], "audio": self.AUDIO},
            {"genres": ["pop"], "audio": other},
            {"genres": ["pop"], "audio": None},
        ]
        matrix = build_feature_matrix(tracks)
        # Column 0 is the one genre, so energy is column 1.
        assert matrix[2, 1] == pytest.approx(VECTOR_AUDIO_WEIGHT * 0.5)
