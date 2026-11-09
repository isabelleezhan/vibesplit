# VibeSplit

## One-sentence pitch
User connects Spotify, picks a big/messy playlist, and the tool splits it into
several smaller, coherent "vibe" playlists — using genre/metadata-based
clustering plus an LLM to interpret and label the resulting groups.

## Stack
- Backend: FastAPI (Python)
- Database: Postgres
- Cache / job queue backing: Redis
- Background jobs: Celery or RQ
- Frontend: React
- Auth: Spotify OAuth (authorization code flow)
- AI layer: LLM API call for cluster labeling + outlier detection (not for
  clustering itself — clustering is metadata-based, not embedding-based)

## Why this shape (context for future me / for Claude Code)
- Spotify deprecated the `audio-features` / `audio-analysis` endpoints for
  any app created after Nov 27, 2024. No BPM/key data is available to new
  apps. This ruled out a harmonic-mixing/compatibility-scoring approach.
- Deliberately avoiding embedding/nearest-neighbor similarity for the
  clustering step, to stay distinct from an existing from-scratch HNSW
  project. Clustering here should be categorical/metadata-based (genre tags,
  release era, popularity), not vector similarity search.
- The LLM's job is semantic labeling and outlier judgment on top of a
  deterministic clustering algorithm — not the clustering itself.

## Data model (initial)
- `users` — Spotify OAuth tokens (access + refresh), basic profile
- `tracks` — spotify_id, artist(s), genres, release year, popularity, cached
  metadata, last_synced_at
- `source_playlists` — the messy playlist a user picked to split
- `generated_playlists` — user_id, source_playlist_id, name/label, track
  list, cluster method used
- `sync_jobs` — track_id, status, last_run

## Build order
1. Spotify OAuth flow (authorization redirect, callback, token exchange,
   refresh logic) — verify end-to-end against a real account before building
   anything else on top of it
2. Playlist import — let user pick a playlist, pull full track list
3. Metadata enrichment — fetch artist genres per track (genres attach to
   artists, not tracks directly in Spotify's model); handle missing/sparse
   genre data
4. Clustering engine — group tracks using genre tags + release era +
   popularity; use a distance metric appropriate for categorical data
   (e.g. k-modes, or hierarchical clustering over genre-overlap distance);
   choose cluster count algorithmically (e.g. silhouette score), not
   hardcoded
5. LLM labeling + refinement layer — for each cluster, send a sample of
   tracks/genres to an LLM to (a) name the vibe and (b) flag likely misfits
   for reassignment to their next-best cluster
6. Write-back to Spotify — create the new smaller playlists via the write
   API (`playlist-modify-private` scope), named with the LLM's labels
7. Background sync + caching — keep artist-genre metadata fresh (short TTL,
   genres can shift over time); cache per-playlist clustering results so
   re-running an unchanged playlist doesn't reprocess from scratch

## Spotify app setup notes
- Registering a developer app requires an active Spotify Premium
  subscription (as of Feb 2026)
- Redirect URI must use `127.0.0.1`, not `localhost`
- Development-mode apps are capped at one client ID per developer and five
  allowlisted users — fine for a portfolio project, just know the ceiling
- Required scopes: `user-library-read`, `playlist-read-private`,
  `playlist-modify-private`
- Do NOT rely on `audio-features`, `audio-analysis`, `recommendations`,
  `related-artists`, or `featured-playlists` — all restricted/removed for
  apps created after Nov 27, 2024

## Talking points for later (resume / interview prep)
- Choosing a distance metric over categorical genre data (why Euclidean
  distance doesn't apply, what was used instead and why)
- Method for choosing cluster count algorithmically rather than guessing
- The LLM as a judgment layer on top of a deterministic algorithm, and how
  disagreements between the LLM's outlier calls and the clustering are
  resolved
- Handling Spotify's genre-data sparsity/messiness in the metadata model
