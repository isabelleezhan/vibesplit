"""No-login demo: splits a few sample playlists saved in the repo, so
visitors can try VibeSplit without a Spotify account. (Spotify's dev mode
only lets 5 allow-listed users log in.)

The sample data is captured once by scripts/capture_demo_fixture.py.
Clustering is the real k-means from app/clustering.py, run on that saved
data. Nothing here calls Spotify or the LLM: cluster names come from the
fixture, which recorded what the LLM named each cluster at capture time,
so visitors can't spend the Groq quota.
"""

import copy
import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.clustering import cluster_tracks, compute_coherence, playlist_year_range
from app.labeling import fallback_label
from app.pipeline import add_coherence_scores

router = APIRouter(prefix="/demo", tags=["demo"])

FIXTURE_PATH = Path(__file__).parent / "demo_data" / "demo_playlists.json"

# A saved name is reused for a cluster that shares at least this much of
# its tracks (Jaccard) with the cluster it was written for. Exact matches
# are the norm, since k-means is seeded; the slack covers tiny float
# differences between the machine that captured the fixture and the server.
LABEL_MATCH_THRESHOLD = 0.6

# Demo results never change for a given fixture, so each playlist is split
# once per process and then served from here.
_split_cache: dict[str, dict] = {}


@lru_cache
def load_fixture(path: Path = FIXTURE_PATH) -> dict:
    if not path.exists():
        return {"playlists": []}
    return json.loads(path.read_text())


def _playlists_by_id() -> dict[str, dict]:
    return {playlist["id"]: playlist for playlist in load_fixture()["playlists"]}


def _track_key(track: dict) -> str:
    """Identifies a track within one playlist. Local files have no id."""
    return track.get("id") or f"{track.get('name')}|{track.get('release_date')}"


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


def apply_saved_labels(clusters: list[dict], saved: list[dict]) -> None:
    """Names each cluster after the saved cluster it overlaps most, or by
    its top genres when nothing saved is close enough. Each saved name is
    used at most once."""
    if len(clusters) < 2:
        for cluster in clusters:
            cluster["label"] = "All Tracks"
        return

    saved_sets = [(set(entry["track_keys"]), entry["label"]) for entry in saved]
    pairs = sorted(
        (
            (_jaccard({_track_key(t) for t in cluster["tracks"]}, keys), ci, si)
            for ci, cluster in enumerate(clusters)
            for si, (keys, _) in enumerate(saved_sets)
        ),
        reverse=True,
    )
    assigned: dict[int, str] = {}
    used: set[int] = set()
    for score, ci, si in pairs:
        if score < LABEL_MATCH_THRESHOLD:
            break
        if ci in assigned or si in used:
            continue
        assigned[ci] = saved_sets[si][1]
        used.add(si)

    for ci, cluster in enumerate(clusters):
        cluster["label"] = assigned.get(ci) or fallback_label(cluster)


def split_demo_playlist(playlist: dict) -> dict:
    # Deep copy: clustering hands back the same track dicts, and the
    # fixture stays cached for the next request.
    tracks = copy.deepcopy(playlist["tracks"])
    year_range = playlist_year_range(tracks)
    original_coherence = compute_coherence(tracks, year_range=year_range)
    result = cluster_tracks(tracks)
    apply_saved_labels(result["clusters"], playlist.get("labels", []))
    add_coherence_scores(result, original_coherence, year_range)
    return result


def _summary(playlist: dict) -> dict:
    return {
        "id": playlist["id"],
        "name": playlist["name"],
        "track_count": len(playlist["tracks"]),
        "owner": "Sample data",
        "image_url": playlist.get("image_url"),
    }


@router.get("/playlists")
def list_demo_playlists() -> dict:
    playlists = [_summary(p) for p in load_fixture()["playlists"]]
    return {"total": len(playlists), "playlists": playlists}


# A plain `def`, so FastAPI runs the (CPU-bound) k-means in its threadpool
# instead of blocking the event loop.
@router.post("/playlists/{playlist_id}/split")
def split_demo(playlist_id: str) -> dict:
    playlist = _playlists_by_id().get(playlist_id)
    if playlist is None:
        raise HTTPException(status_code=404, detail="No such demo playlist.")
    if playlist_id not in _split_cache:
        _split_cache[playlist_id] = split_demo_playlist(playlist)
    return _split_cache[playlist_id]
