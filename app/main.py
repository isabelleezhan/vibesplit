import logging
import time
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware

from app.auth import SESSION_COOKIE_NAME, SESSION_MAX_AGE_SECONDS
from app.auth import router as auth_router
from app.clusters import router as clusters_router
from app.config import Settings, get_settings
from app.db import init_db
from app.demo import router as demo_router
from app.jobs import router as jobs_router
from app.playlists import router as playlists_router
from app.tracks import router as tracks_router
from app.writeback import router as writeback_router

# Uvicorn only configures its own loggers, so without this the app's
# INFO lines (like the split timing summary) never reach the terminal.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app.requests")

INSECURE_SECRET_KEYS = {"", "changeme"}


def check_settings(settings: Settings) -> None:
    """Refuse to start in production with the placeholder SECRET_KEY:
    anyone who knows it could sign a session cookie for any user."""
    if settings.is_production and settings.secret_key in INSECURE_SECRET_KEYS:
        raise RuntimeError("SECRET_KEY must be set to a long random value when APP_ENV=production.")


settings = get_settings()
check_settings(settings)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # The queue split jobs get handed to (app/jobs.py -> app/worker.py).
    app.state.arq = await create_pool(RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379/0"))
    yield
    await app.state.arq.aclose()


app = FastAPI(title="VibeSplit", lifespan=lifespan)

# No CORS: the browser only ever talks to one origin. CloudFront serves
# the frontend and sends /api/* here in production, and Vite's dev server
# proxies /api/* here locally.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie=SESSION_COOKIE_NAME,
    max_age=SESSION_MAX_AGE_SECONDS,
    same_site="lax",
    # Secure flag: browsers only send it over HTTPS, which CloudFront gives us.
    https_only=settings.is_production,
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


def health() -> dict[str, str]:
    return {"status": "ok"}


# Everything lives under /api, which is the path CloudFront routes to this
# server (the rest of the site is the frontend, served from S3).
api = APIRouter(prefix="/api")
api.include_router(auth_router)
api.include_router(playlists_router)
api.include_router(tracks_router)
api.include_router(clusters_router)
api.include_router(jobs_router)
api.include_router(writeback_router)
api.include_router(demo_router)
api.add_api_route("/health", health, methods=["GET"])
app.include_router(api)

# Also at the root, for the container health check, which hits the
# server directly rather than through CloudFront.
app.add_api_route("/health", health, methods=["GET"])
