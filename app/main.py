import logging
import time
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.auth import router as auth_router
from app.clusters import router as clusters_router
from app.config import get_settings
from app.db import init_db
from app.jobs import router as jobs_router
from app.playlists import router as playlists_router
from app.tracks import router as tracks_router
from app.writeback import router as writeback_router

# Uvicorn only configures its own loggers, so without this the app's
# INFO lines (like the split timing summary) never reach the terminal.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app.requests")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # The queue split jobs get handed to (app/jobs.py -> app/worker.py).
    app.state.arq = await create_pool(RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379/0"))
    yield
    await app.state.arq.aclose()


app = FastAPI(title="VibeSplit", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.middleware("http")
async def log_request_duration(request: Request, call_next):
    """Logs how long each request took, which is the number the job
    queue is meant to shrink: the split request itself, not the split."""
    start = time.perf_counter()
    response = await call_next(request)
    logger.info(
        "%s %s -> %s in %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - start) * 1000,
    )
    return response


app.include_router(auth_router)
app.include_router(playlists_router)
app.include_router(tracks_router)
app.include_router(clusters_router)
app.include_router(jobs_router)
app.include_router(writeback_router)

@app.get("/")
def hello_world() -> dict[str, str]:
    return {"message": "Hello, VibeSplit!"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
