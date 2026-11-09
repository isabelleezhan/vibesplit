# VibeSplit — Project Context

Background context for a Claude session working on a *different* project, so it understands what VibeSplit is, why it's shaped the way it is, and where it stands. Paste this into that project's context (e.g. its own CLAUDE.md or a pasted memory) — it is not itself VibeSplit's CLAUDE.md. Last updated 2026-09-28; some of this describes work not yet committed to the repo (noted inline).

## What it is

VibeSplit is a portfolio project: a web app where a user connects Spotify, picks a big/messy playlist, and the tool splits it into several smaller, coherent "vibe" playlists. It clusters tracks by audio features + genre tags (not raw embeddings), then uses an LLM only to name each resulting cluster.

**Design choice that changed:** earlier versions deliberately avoided embeddings/vector-similarity, to stay narratively distinct from the user's separate from-scratch HNSW vector-index project (`~/hnsw-cpp`). **As of 2026-09-28 that constraint is dropped** — the user no longer cares about keeping VibeSplit embedding-free for that reason, so embeddings are now a legitimate option for clustering if it comes up again. `PROJECT.md` and `README.md` in the repo still state the old no-embeddings rule; they haven't been updated to match yet.

## Stack

- **Backend:** FastAPI (Python), async SQLAlchemy over Postgres
- **Database:** Postgres (Docker Compose)
- **Job queue:** arq (Redis-backed), chosen over Celery/RQ because the pipeline is fully async and Celery's per-task `asyncio.run` breaks the module-level asyncpg engine
- **Frontend:** React + Vite + TypeScript, plain CSS + CSS Modules (no Tailwind, no component library), `motion` npm package for animation
- **Auth:** Spotify OAuth (authorization code flow) — see the auth caveat below, it's not production-safe yet
- **AI layer:** Groq (Llama), now used *only* to name clusters (`app/labeling.py`'s `label_clusters`) — genre tagging and misfit reassignment by LLM have both been removed (see Clustering rebuild below)

## Status (as of 2026-09-28)

Original PRD's 8-step MVP build order is functionally done (steps 1-7 from earlier, step 8 background-jobs/caching finished 2026-09-27), and the clustering approach itself was substantially rebuilt on 2026-09-28. Current focus is **deploying to AWS as a "production backend" resume project** — see Next steps below.

**Verified end-to-end against a real Spotify account on 2026-09-06** (pre-rebuild): login → playlist pick → clustering → LLM labeling → confirm split → new playlists actually appeared in Spotify.

### Clustering rebuild (2026-09-28) — the big recent change

Real splits of 224 and 790 tracks came out lopsided (223+1, 788+2 tracks). Root causes diagnosed:
- Spotify no longer returns artist genres or popularity at all (confirmed live) — so genre came entirely from LLM guesses, which were sparse, inconsistently spelled, and left 81/790 tracks untagged
- average-linkage clustering + silhouette score rewarded a degenerate "one big cluster + outliers" split

Fixes shipped (committed in `f82eb48`, 2026-10-16):
- **`app/audio_features.py`** — energy/valence/danceability/acousticness per track from **ReccoBeats** (free, no API key, keyed by Spotify track id; Spotify's own `audio-features` endpoint is closed to apps created after Nov 27, 2024). Optional/best-effort: ~77% track coverage, split still runs on tags alone if it's down.
- **`app/lastfm.py`** — genre per **artist** (not track) from Last.fm's crowd-sourced tags, mapped onto a fixed `GENRES` vocabulary. ~787/790 coverage in testing, far better and more consistent than the old LLM guesses. Needs `LASTFM_API_KEY` in `.env`; falls back to LLM genre tags if missing/failing.
- **`app/clustering.py`** rewritten to k-means over per-track feature vectors (audio features + genre), with a 5% minimum cluster size to stop degenerate splits.
- **LLM mood tagging removed entirely** — an offline test showed splits scored 0.96-0.98 identical with vs. without it, so it wasn't earning its cost. `app/genre_tagging.py` and the `TrackTagCache` model are **gone** (the old `track_tag_cache` Postgres table is still there, just unused — not dropped).
- LLM's only remaining job: naming clusters, with a genre-based fallback name if Groq fails. Misfit/outlier reassignment by LLM was removed along with mood tagging.
- Coherence scoring is now genre (0.7 weight) + era (0.3 weight) only — **not comparable to old coherence numbers** from before this change.
- `ALGORITHM_VERSION` (in `app/pipeline.py`) bumped to `"3"` — this is what invalidates old cached split results.
- Performance (790-track playlist; numbers verified from run logs on 2026-10-27):
  - Before: 841.5s (~14 min) measured, 833.8s of it LLM tagging. 317 of 790 tracks were already in the tag cache, so a fully cold run would have been slower.
  - After, warm cache: 5.6s measured (audio-feature and Last.fm steps 0.0s because cached).
  - After, first run: ~20s **estimated**, summed from separately measured steps (fetch 3.2s + ReccoBeats 7.6s + Last.fm 6.6s + cluster/label 2.1s = 19.5s). Never timed end to end.
  - Intermediate run that still used LLM mood tagging: 162.9s (136.9s tagging), which shows the speedup came from removing per-song LLM calls, not from k-means.
- Coherence, original playlist → its split under the new pipeline (NOT old algorithm vs. new algorithm): 96 tracks 0.351→0.537 (+53%), 224 tracks 0.339→0.487 (+44%), 790 tracks 0.379→0.426 (+12%). Relative increases, not percentage points.
- Committed and pushed as `f82eb48` (2026-10-16). **`PROJECT.md`, `README.md` still describe the pre-rebuild approach** (LLM genre/mood tagging, per-track LLM calls) — don't trust those two files for how clustering currently works, trust this note and the actual code in `app/clustering.py`/`app/lastfm.py`/`app/audio_features.py` instead.

### Background jobs (PRD step 8, built 2026-09-27, committed in `f82eb48`)

- arq job queue; Postgres `split_jobs` table is the source of truth (Redis is only the queue transport)
- Partial unique index enforces one active job per (user, playlist)
- Worker heartbeat every 15s, 120s staleness timeout
- `split_result_cache` keyed by `(playlist_id, snapshot_id, ALGORITHM_VERSION)`
- Nightly arq cron job cleans up old cache/job rows
- The old synchronous `POST /playlists/{id}/clusters` endpoint is kept only for baseline timing comparisons — meant to be deleted eventually
- 87 tests collected and passing as of 2026-10-27 (was 59 when the job queue was first built; `tests/test_jobs.py`, `test_pipeline.py`, `test_audio_features.py`, `test_lastfm.py` added alongside)
- Docker Compose runs only PostgreSQL and Redis; the FastAPI app and arq worker run outside Docker

## Frontend specifics

Flow is a strict linear step machine in `App.tsx` (no router): Login → PlaylistPicker → clustering loader (now polls the arq job instead of blocking on a sync request, via `frontend/src/lib/splitJob.ts`) → ClusterPreview (rename cluster label, exclude a cluster, drag tracks between clusters via native HTML5 DnD) → confirming loader → SplitSuccess.

Aesthetic: **dark console + frosted glass + glowing components**, chosen explicitly by the user. Per-cluster glow-color palette lives in `frontend/src/lib/clusterColors.ts`, kept consistent across screens. Fonts: Inter (display + body) + JetBrains Mono (data), via Google Fonts — note Inter is a deliberate, conscious choice even though the project's own CLAUDE.md has a standing anti-genericism/anti-Inter directive for frontend work; don't "fix" this if you see it.

## Key constraints worth knowing

- Spotify redirect URI must use `127.0.0.1`, not `localhost`.
- Registering a Spotify developer app requires active Spotify Premium.
- Dev-mode Spotify apps are capped at one client ID per developer and **five allowlisted users** — this is a real blocker for a public AWS demo, not just a dev inconvenience (see Next steps).
- Required scopes: `user-library-read`, `playlist-read-private`, `playlist-modify-private`.
- Cannot use `audio-features`, `audio-analysis`, `recommendations`, `related-artists`, or `featured-playlists` (Spotify) — all restricted for apps created after Nov 27, 2024. `audio-features` is substituted by ReccoBeats now (see above).
- Spotify no longer returns artist genres or popularity either, as of the 2026-09-28 investigation — this wasn't previously known and affected the original clustering design.

## Next goal: AWS deployment (not started as of 2026-09-28)

Framed as a "production backend" resume project, on a **new** AWS account (credits-based free plan, not the legacy 12-month free tier). Agreed near-free architecture:
- ECS on the **EC2 launch type** (not Fargate)
- Redis as a plain container (not ElastiCache)
- RDS `db.t4g.micro` for Postgres
- No ALB, no NAT gateway
- CloudFront in front of both the S3-hosted frontend and the backend instance, for free HTTPS on a `*.cloudfront.net` domain
- Terraform for infra, GitHub Actions with OIDC for deploy

**Blockers to resolve before going public:**
1. **Auth is not safe to expose publicly yet.** The session cookie `spotify_user_id` (`app/auth.py`) is the raw, unsigned Spotify user id — anyone can impersonate any user by just setting that cookie value. Needs signing/proper session tokens before this goes on the open internet.
2. `_pending_states` (OAuth state storage) is in-memory, won't survive multiple instances/restarts.
3. Spotify dev mode's 5-user cap means a real public demo needs a no-login demo mode (canned/sample data path), since real visitors can't OAuth in past that cap.

Resume metrics are settled (see Performance and Coherence above). Current resume wording: "Cut a 790-track split from 14 minutes to 20 seconds (6 cached)" and "splits 12–53% more coherent than the original". The one gap: nobody has timed a single cold 790-track split end to end, so ~20s is still an estimate.

## Related but separate project

The user has a third portfolio project idea in mind: a RAG app built on their existing from-scratch `~/hnsw-cpp` HNSW index (pybind11 binding exposing only `add(vector)`/`search(query, k, ef_search)` — no persistence or metadata storage built in), extending its MovieLens content-recommender demo into real retrieval-augmented generation. **Scoped but not started.** This was originally kept deliberately separate from VibeSplit to preserve VibeSplit's "no embeddings" narrative — that reasoning no longer applies (see "Design choice that changed" above), but the RAG idea is still a separate, unstarted project either way.

## Working-style notes if you pick this project back up

- When asked to *explain* how to build something, give the explanation and stop — don't start implementing until explicitly told to proceed.
- VibeSplit's own `CLAUDE.md` (present in the repo but currently untracked/uncommitted) carries a project-scoped anti-"AI slop" frontend directive (distinctive typography, cohesive color themes, CSS-first motion, atmospheric backgrounds, no purple-gradient-on-white). That directive is scoped to VibeSplit's repo — don't assume it's a general instruction outside this project unless told so.
- `PROJECT.md` and `README.md` are stale relative to the 2026-09-28 clustering rebuild (see above) — if asked to update docs, that's the known gap.
