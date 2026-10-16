import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.clustering import cluster_tracks, compute_coherence, playlist_year_range
from app.labeling import label_clusters
from app.timing import StageTimer
from app.tracks import get_enriched_playlist_tracks

logger = logging.getLogger(__name__)

# Bump this whenever a change to enrichment, clustering, or labeling would
# produce a different split, so cached results from the old code stop
# being served (see SplitResultCache in app/models.py).
ALGORITHM_VERSION = "3"

# Awaited as progress(stage, done, total). done/total are for stages that
# can count their work; no current stage is slow enough to need them.
ProgressCallback = Callable[[str, int | None, int | None], Awaitable[None]]


async def _no_progress(stage: str, done: int | None = None, total: int | None = None) -> None:
    pass


async def run_split_pipeline(
    playlist_id: str,
    access_token: str,
    db: AsyncSession,
    progress: ProgressCallback = _no_progress,
) -> dict:
    """Import tracks, enrich with genres and audio features, cluster by
    vibe, then have the LLM name each cluster. Returns the clusters plus
    a `timings` breakdown, and logs that breakdown as one line per split."""
    timer = StageTimer()

    await progress("fetching", None, None)
    tracks = await get_enriched_playlist_tracks(playlist_id, access_token, db, timer=timer)

    await progress("clustering", None, None)
    with timer.stage("cluster"):
        # Fixed year basis so before/after scores are measured on the same scale
        year_range = playlist_year_range(tracks)
        original_coherence = compute_coherence(tracks, year_range=year_range)
        clustered = cluster_tracks(tracks)

    await progress("labeling", None, None)
    with timer.stage("label"):
        result = await label_clusters(clustered)

    scored_coherences = []
    for cluster in result["clusters"]:
        coherence = compute_coherence(cluster["tracks"], year_range=year_range)
        cluster["coherence"] = coherence
        if coherence is not None:
            scored_coherences.append(coherence)

    result["original_coherence"] = original_coherence
    result["avg_new_coherence"] = (
        round(sum(scored_coherences) / len(scored_coherences), 4) if scored_coherences else None
    )

    timer.counts["clusters"] = len(result["clusters"])
    timings = timer.summary()
    result["timings"] = {**timings, **timer.counts}
    logger.info(
        "split timing playlist=%s %s %s",
        playlist_id,
        " ".join(f"{name}={value}" for name, value in timer.counts.items()),
        " ".join(f"{name}={seconds:.2f}s" for name, seconds in timings.items()),
    )
    return result
