from sqlalchemy import JSON, Boolean, Float, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    """Spotify OAuth tokens + basic profile, per PROJECT.md's data model."""

    # The actual Postgres table name this class maps to.
    __tablename__ = "users"

    spotify_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    access_token: Mapped[str] = mapped_column(String, nullable=False)
    # `str | None` is the modern way to write "optional"
    refresh_token: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)


class TrackAudioFeatures(Base):
    """Cached audio features per Spotify track id, from ReccoBeats (see
    app/audio_features.py). `features` is None when ReccoBeats doesn't
    know the track, so a miss is remembered too and not re-asked on
    every split."""

    __tablename__ = "track_audio_features"

    track_id: Mapped[str] = mapped_column(String, primary_key=True)
    features: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Unix timestamp; expires after CACHE_TTL_SECONDS (app/tracks.py).
    fetched_at: Mapped[float] = mapped_column(Float, nullable=False)


class ArtistGenreCache(Base):
    """Cached Last.fm genres per Spotify artist id (see app/lastfm.py).
    An empty list means Last.fm had no genre tags for the artist, which
    is remembered so it isn't re-asked on every split."""

    __tablename__ = "artist_genre_cache"

    artist_id: Mapped[str] = mapped_column(String, primary_key=True)
    genres: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[float] = mapped_column(Float, nullable=False)


# Job statuses. "queued" and "running" count as active: a user can have
# at most one active job per playlist (enforced by the index below).
JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"
ACTIVE_JOB_STATUSES = (JOB_QUEUED, JOB_RUNNING)


class SplitJob(Base):
    """One background split of one playlist for one user. This table is
    the source of truth for job state; Redis only carries the queue
    itself (see app/worker.py), so a Redis restart can't lose a result."""

    __tablename__ = "split_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    playlist_id: Mapped[str] = mapped_column(String, nullable=False)
    snapshot_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    # Which pipeline stage is running ("fetching", "clustering", ...), plus
    # done/total counts for stages that can count their work.
    stage: Mapped[str | None] = mapped_column(String, nullable=True)
    progress_done: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Unix timestamps, matching the rest of the schema.
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Bumped every few seconds by the worker while the job runs. A running
    # job whose heartbeat goes quiet means the worker died, not that the
    # job is slow (see app/jobs.py).
    heartbeat_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        # Makes "one active job per user per playlist" hold even when two
        # requests race each other, which a check-then-insert can't.
        Index(
            "uq_split_jobs_one_active",
            "user_id",
            "playlist_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )


class SplitResultCache(Base):
    """A finished split, keyed by exactly what it depends on. Spotify
    changes a playlist's snapshot_id whenever it's edited, and
    ALGORITHM_VERSION (app/pipeline.py) changes whenever the split logic
    does, so an unchanged playlist on unchanged code is a safe hit.
    Stores the result before any user edits, since renames and track
    moves in the frontend are per-user and never reach the server."""

    __tablename__ = "split_result_cache"

    playlist_id: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String, primary_key=True)
    algorithm_version: Mapped[str] = mapped_column(String, primary_key=True)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
