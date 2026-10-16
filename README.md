# VibeSplit

Have you ever found yourself skipping through your own playlist because the
vibe keeps whiplashing — a sad acoustic ballad, then arena pop, then a K-pop
track, none of it belonging in the same fifteen minutes?

VibeSplit fixes that without making you sort anything by hand. Connect
Spotify, pick the playlist that's gotten out of control, and watch it 
split into smaller playlists with a cohesive vibe.

## Stack

- **Backend:** FastAPI (Python), async SQLAlchemy over Postgres
- **Database:** Postgres (via Docker Compose)
- **Background jobs:** arq over Redis. A split runs in a worker process,
  not inside the request, and the frontend polls it for progress. Picked
  over Celery and RQ because the pipeline is async end to end.
- **Frontend:** React + Vite + TypeScript, plain CSS/CSS Modules, `motion`
  for animation — no component library
- **Auth:** Spotify OAuth (authorization code flow)
- **AI layer:** Groq (Llama) for genre/vibe tagging, cluster labeling, and
  outlier detection — clustering itself is metadata-based (genre tags +
  release era), not embedding/vector-similarity based

## Getting started

Start Postgres and Redis:

```bash
docker compose up -d
```

Backend:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  
uvicorn app.main:app --reload --reload-dir app
```

Worker, in a second terminal (splits sit in "queued" until this is running):

```bash
source .venv/bin/activate
arq app.worker.WorkerSettings
```

Frontend, in a third terminal:

```bash
cd frontend
npm install
npm run dev
```

Visit `http://127.0.0.1:5173` and log in with Spotify. (Registering a
Spotify developer app requires an active Premium subscription; dev-mode apps
are capped at 5 allowlisted users and can't use `localhost` as a redirect
URI — use `127.0.0.1`.)

Each split logs one timing line from the worker, e.g.
`split timing playlist=... tracks=412 tag_cache_hits=300 tagged_by_llm=112 clusters=6 fetch=1.20s tag=30.10s ...`,
and the API logs every request's duration.

The backend alone serves a hello-world check at `http://127.0.0.1:8000/` and
a health check at `http://127.0.0.1:8000/health`.


