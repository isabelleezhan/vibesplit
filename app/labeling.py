"""LLM naming: takes the raw output of app/clustering.py and asks the LLM
for a short vibe name per cluster. 
"""

import logging
from collections import Counter

from pydantic import BaseModel

from app.clustering import AUDIO_KEYS
from app.llm import generate_structured

logger = logging.getLogger(__name__)

SAMPLE_SIZE = 8  # tracks shown per cluster

_SYSTEM_PROMPT = (
    "You are a music curator assistant. You'll see several clusters of tracks "
    "that were grouped algorithmically by genre, measured audio features, and "
    "release era. Each cluster shows its average audio features (0 to 1: energy, "
    "valence = how happy/positive it sounds, danceability, acousticness), its "
    "most common genres, and a sample of its tracks.\n"
    "Give each cluster a short, human-readable vibe label (2-5 words, e.g. "
    "'Daniel Caesar R&B/Soul', 'Early BTS K-Pop', 'Bieber Dance-Pop Era', "
    "'Soft Acoustic Heartbreak'). Use the audio features, not just genre, when "
    "they're what sets a cluster apart: two pop clusters where one is low energy "
    "and highly acoustic should not both be called 'Pop'. Make every label "
    "distinct from the others."
)


class _ClusterLabel(BaseModel):
    cluster_id: int
    label: str


class _LabelingResponse(BaseModel):
    cluster_labels: list[_ClusterLabel]


def _format_track(track: dict) -> str:
    artists = ", ".join(a["name"] for a in track.get("artists", []) if a.get("name"))
    genres = ", ".join(track.get("genres", [])) or "no genre data"
    year = (track.get("release_date") or "")[:4]
    return f"  - \"{track.get('name')}\" by {artists} ({genres}) [{year}]"


def _audio_summary(tracks: list[dict]) -> str:
    known = [t["audio"] for t in tracks if t.get("audio")]
    if not known:
        return "no audio data"
    return ", ".join(f"{key} {sum(a[key] for a in known) / len(known):.2f}" for key in AUDIO_KEYS)


def _top_genres(tracks: list[dict], n: int) -> list[str]:
    return [genre for genre, _ in Counter(g for t in tracks for g in t.get("genres", [])).most_common(n)]


def _build_prompt(clusters: list[dict]) -> str:
    sections = []
    for cluster in clusters:
        tracks = cluster["tracks"]
        sample = tracks[:SAMPLE_SIZE]
        lines = [
            f"  average audio: {_audio_summary(tracks)}",
            f"  top genres: {', '.join(_top_genres(tracks, 5)) or 'none'}",
            *[_format_track(t) for t in sample],
        ]
        if len(tracks) > len(sample):
            lines.append(f"  ... and {len(tracks) - len(sample)} more tracks in this cluster")
        sections.append(f"Cluster {cluster['cluster_id']} ({len(tracks)} tracks total):\n" + "\n".join(lines))
    return "\n\n".join(sections)


def fallback_label(cluster: dict) -> str:
    """A plain name from the cluster's two most common genres."""
    genres = _top_genres(cluster["tracks"], 2)
    return " / ".join(g.title() for g in genres) if genres else f"Cluster {cluster['cluster_id']}"


async def label_clusters(cluster_result: dict) -> dict:
    """Takes cluster_tracks()'s output, returns the same shape with a
    `label` added to each cluster."""
    clusters = cluster_result["clusters"]
    if len(clusters) < 2:
        for cluster in clusters:
            cluster["label"] = "All Tracks"
        return cluster_result

    try:
        result = await generate_structured(_SYSTEM_PROMPT, _build_prompt(clusters), _LabelingResponse)
        labels_by_id = {label.cluster_id: label.label for label in result.cluster_labels}
    except Exception:
        logger.warning("cluster naming failed, using genre-based names", exc_info=True)
        labels_by_id = {}

    for cluster in clusters:
        cluster["label"] = labels_by_id.get(cluster["cluster_id"]) or fallback_label(cluster)
    return cluster_result
