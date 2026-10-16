"""Clustering engine: groups tracks into "vibe" clusters with k-means over
one feature vector per track (genre tags, measured audio features,
release year).
"""

import math

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

# Pairwise distance weights, used for coherence scoring.
GENRE_WEIGHT = 0.7
ERA_WEIGHT = 0.3

# How much each part of a track's feature vector counts in clustering.
# Genre tags are scaled to length 1 before weighting, and the four audio
# features are 0 to 1 each.
VECTOR_GENRE_WEIGHT = 1.0
VECTOR_AUDIO_WEIGHT = 1.5
VECTOR_ERA_WEIGHT = 0.4
AUDIO_KEYS = ("energy", "valence", "danceability", "acousticness")

MIN_CLUSTERS = 2
MAX_CLUSTERS = 5
# A split where any cluster is smaller than this share of the playlist
# isn't a candidate. Silhouette alone happily picks "everything + a few
# outliers", which is useless as playlists.
MIN_CLUSTER_FRACTION = 0.05
NEUTRAL_DISTANCE = 0.5  # used when a numeric feature is missing on either side

MIN_TRACKS_FOR_COHERENCE = 4

def _extract_year(release_date: str | None) -> float | None:
    if not release_date:
        return None
    try:
        return float(release_date[:4])
    except ValueError:
        return None


def _normalize(values: list[float | None]) -> list[float | None]:
    """Min-max normalize to [0, 1], preserving None for missing values."""
    present = [v for v in values if v is not None]
    if not present:
        return [None] * len(values)
    lo, hi = min(present), max(present)
    if hi == lo:
        return [0.0 if v is not None else None for v in values]
    return [None if v is None else (v - lo) / (hi - lo) for v in values]


def _numeric_distance(a: float | None, b: float | None) -> float:
    if a is None or b is None:
        return NEUTRAL_DISTANCE
    return abs(a - b)


def _tag_overlap_distance(tags_a: set[str], tags_b: set[str]) -> float | None:
    """Jaccard distance between two tag sets — 0 = identical, 1 = disjoint.
    None (not a number) when either side has no tags to compare"""
    if not tags_a or not tags_b:
        return None
    return 1 - len(tags_a & tags_b) / len(tags_a | tags_b)


def _pairwise_distance(a: dict, b: dict) -> float:
    era_distance = _numeric_distance(a["_year_norm"], b["_year_norm"])
    genre_distance = _tag_overlap_distance(set(a["genres"]), set(b["genres"]))

    components = [(GENRE_WEIGHT, genre_distance), (ERA_WEIGHT, era_distance)]
    available = [(weight, distance) for weight, distance in components if distance is not None]
    total_weight = sum(weight for weight, _ in available)
    return sum(weight * distance for weight, distance in available) / total_weight


def playlist_year_range(tracks: list[dict]) -> tuple[float, float] | None:
    years = [_extract_year(t.get("release_date")) for t in tracks]
    present = [y for y in years if y is not None]
    return (min(present), max(present)) if present else None


def compute_coherence(tracks: list[dict], year_range: tuple[float, float] | None = None) -> float | None:
    """Average pairwise similarity within a group of tracks (1 - average
    pairwise distance)
    """
    if len(tracks) < MIN_TRACKS_FOR_COHERENCE:
        return None

    if year_range is None:
        year_range = playlist_year_range(tracks)

    if year_range is None:
        year_norm = [None] * len(tracks)
    else:
        lo, hi = year_range
        denom = hi - lo if hi > lo else 1.0
        year_norm = [None if (y := _extract_year(t.get("release_date"))) is None else (y - lo) / denom for t in tracks]

    enriched = [{**track, "_year_norm": y} for track, y in zip(tracks, year_norm)]

    distances = [
        _pairwise_distance(enriched[i], enriched[j])
        for i in range(len(enriched))
        for j in range(i + 1, len(enriched))
    ]
    avg_distance = sum(distances) / len(distances)
    return round(1 - avg_distance, 4)


def _max_k(n: int) -> int:
    return max(MIN_CLUSTERS, min(MAX_CLUSTERS, n - 1))


def _single_cluster_result(tracks: list[dict]) -> dict:
    return {
        "k": 1,
        "silhouette_score": None,
        "clusters": [{"cluster_id": 0, "track_count": len(tracks), "tracks": tracks}],
    }


def _tag_block(tags_per_track: list[list[str]], weight: float) -> np.ndarray:
    """One-hot rows over every tag in the playlist, each row scaled to
    length `weight` (all zeros for a track with no tags)."""
    vocabulary = {tag: i for i, tag in enumerate(sorted({t for tags in tags_per_track for t in tags}))}
    block = np.zeros((len(tags_per_track), len(vocabulary)))
    for row, tags in enumerate(tags_per_track):
        for tag in tags:
            block[row, vocabulary[tag]] = 1.0
        norm = np.linalg.norm(block[row])
        if norm:
            block[row] *= weight / norm
    return block


def build_feature_matrix(tracks: list[dict]) -> np.ndarray:
    """One row per track: weighted genre tags, audio features
    and release year. Missing audio or year is filled with the playlist
    average, so a gap reads as "typical" rather than as an extreme."""
    audio_rows = [
        [track["audio"][key] for key in AUDIO_KEYS] if track.get("audio") else None for track in tracks
    ]
    known_audio = [row for row in audio_rows if row is not None]
    audio_mean = np.mean(known_audio, axis=0) if known_audio else np.full(len(AUDIO_KEYS), 0.5)
    audio = np.array([row if row is not None else audio_mean for row in audio_rows])

    years = _normalize([_extract_year(t.get("release_date")) for t in tracks])
    known_years = [y for y in years if y is not None]
    year_mean = sum(known_years) / len(known_years) if known_years else 0.5
    era = np.array([[year_mean if y is None else y] for y in years])

    return np.hstack(
        [
            _tag_block([t.get("genres", []) for t in tracks], VECTOR_GENRE_WEIGHT),
            VECTOR_AUDIO_WEIGHT * audio,
            VECTOR_ERA_WEIGHT * era,
        ]
    )


def cluster_tracks(tracks: list[dict]) -> dict:
    """Cluster tracks by vibe. Tries k from MIN_CLUSTERS to MAX_CLUSTERS,
    and keeps the k with the best silhouette score among splits where
    every cluster is at least MIN_CLUSTER_FRACTION of the playlist.
    Falls back to a single cluster when there aren't enough tracks, or
    enough distinct tracks, to split."""
    n = len(tracks)
    if n < MIN_CLUSTERS + 1:
        return _single_cluster_result(tracks)

    features = build_feature_matrix(tracks)
    distinct = len(np.unique(features.round(9), axis=0))
    max_k = min(_max_k(n), distinct)
    min_size = max(1, math.ceil(MIN_CLUSTER_FRACTION * n))

    best_k, best_score, best_labels = None, -1.0, None
    for k in range(MIN_CLUSTERS, max_k + 1):
        # Fixed seed so the same playlist always splits the same way.
        labels = KMeans(n_clusters=k, n_init=10, random_state=0).fit_predict(features)
        sizes = np.bincount(labels)
        if len(sizes) < 2 or sizes.min() < min_size:
            continue
        score = silhouette_score(features, labels)
        if score > best_score:
            best_k, best_score, best_labels = k, score, labels

    if best_labels is None:
        # Identical tracks, or no split without a too-small cluster.
        return _single_cluster_result(tracks)

    clusters: dict[int, list[dict]] = {}
    for track, label in zip(tracks, best_labels):
        clusters.setdefault(int(label), []).append(track)

    return {
        "k": best_k,
        "silhouette_score": round(float(best_score), 4),
        "clusters": [
            {"cluster_id": cluster_id, "track_count": len(members), "tracks": members}
            for cluster_id, members in sorted(clusters.items())
        ],
    }
