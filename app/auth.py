import secrets
import time
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"
# The signed session cookie (Starlette's SessionMiddleware, set up in
# app/main.py) stores the logged-in Spotify id under this key. The cookie
# is signed with SECRET_KEY, so editing it to someone else's id breaks
# the signature and the session reads as empty.
SESSION_USER_KEY = "spotify_id"
SESSION_COOKIE_NAME = "vibesplit_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30
# The old unsigned cookie. Cleared on login so it doesn't linger.
LEGACY_USER_ID_COOKIE_NAME = "spotify_user_id"

# Read the user's saved library, read private playlists,
# and create/modify playlists on their behalf.
SCOPES = " ".join(
    [
        "user-library-read",
        "playlist-read-private",
        "playlist-modify-private",
    ]
)

# CSRF guard for the login->callback round trip. Each state lives in
# Redis (not in this process) so it survives a restart and works across
# several app instances, and expires on its own if the user never comes back.
OAUTH_STATE_TTL_SECONDS = 10 * 60
OAUTH_STATE_KEY_PREFIX = "oauth_state:"


def get_redis(request: Request) -> Redis:
    """FastAPI dependency: the Redis connection opened in app/main.py's
    lifespan (the same pool arq uses for the job queue)."""
    return request.app.state.arq


async def save_oauth_state(redis: Redis, state: str) -> None:
    await redis.set(OAUTH_STATE_KEY_PREFIX + state, "1", ex=OAUTH_STATE_TTL_SECONDS)


async def consume_oauth_state(redis: Redis, state: str) -> bool:
    """True the first time a state we issued comes back, False for an
    unknown, expired or already used one. GETDEL reads and deletes in one
    atomic step, so two requests racing with the same state can't both win."""
    return await redis.getdel(OAUTH_STATE_KEY_PREFIX + state) is not None


@router.get("/login")
async def login(redis: Annotated[Redis, Depends(get_redis)]) -> RedirectResponse:
    settings = get_settings()
    if not settings.spotify_client_id:
        raise HTTPException(
            status_code=500,
            detail="SPOTIFY_CLIENT_ID is not set — check your .env file.",
        )

    state = secrets.token_urlsafe(16)
    await save_oauth_state(redis, state)

    params = {
        "response_type": "code",
        "client_id": settings.spotify_client_id,
        "scope": SCOPES,
        "redirect_uri": settings.spotify_redirect_uri,
        "state": state,
    }
    # Send the browser to Spotify's own login/consent page.
    return RedirectResponse(f"{SPOTIFY_AUTHORIZE_URL}?{urlencode(params)}")


@router.get("/callback")
async def callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> RedirectResponse:
    # Spotify redirects the browser here after the user approves (or
    # denies) access, appending `code`, `state`, and (on denial) `error`
    # as query parameters.
    settings = get_settings()

    error = request.query_params.get("error")
    if error:
        raise HTTPException(status_code=400, detail=f"Spotify returned an error: {error}")

    code = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing 'code' or 'state' in callback.")
    if not await consume_oauth_state(redis, state):
        raise HTTPException(status_code=400, detail="Unknown or reused 'state' — possible CSRF, please log in again.")

    # `async with httpx.AsyncClient()` opens an HTTP client for the
    # duration of this block, closing its connections automatically
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.spotify_redirect_uri,
            },
            auth=(settings.spotify_client_id, settings.spotify_client_secret),
        )
        if token_response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Spotify token exchange failed: {token_response.status_code} {token_response.text}",
            )
        token_data = token_response.json()

        profile_response = await client.get(
            f"{SPOTIFY_API_BASE}/me",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        if profile_response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Spotify profile request failed: {profile_response.status_code} {profile_response.text}",
            )
        profile = profile_response.json()

    spotify_id = profile["id"]
    expires_at = time.time() + token_data.get("expires_in", 3600)

    # Upsert: update the existing row if this user has logged in before,
    # otherwise create a new one. `db.get(User, spotify_id)` is a
    # primary-key lookup.
    user = await db.get(User, spotify_id)
    if user:
        user.access_token = token_data["access_token"]
        user.refresh_token = token_data.get("refresh_token", user.refresh_token)
        user.expires_at = expires_at
        user.display_name = profile.get("display_name")
    else:
        user = User(
            spotify_id=spotify_id,
            display_name=profile.get("display_name"),
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_at=expires_at,
        )
        db.add(user)
    await db.commit()

    # Start a fresh session rather than adding to whatever was there.
    # SessionMiddleware signs it and sets the cookie on this response.
    request.session.clear()
    request.session[SESSION_USER_KEY] = spotify_id

    # Hand the browser back to the frontend app.
    resp = RedirectResponse(settings.frontend_url)
    resp.delete_cookie(LEGACY_USER_ID_COOKIE_NAME)
    return resp


@router.post("/logout")
def logout(request: Request) -> dict[str, bool]:
    """Ends the session. SessionMiddleware sees it's empty and expires the
    cookie. Spotify tokens stay in Postgres for the worker's sake; they
    can't be reached without a session."""
    request.session.clear()
    return {"ok": True}


async def _refresh(refresh_token: str) -> dict:
    """Trade a refresh token for a new access token, once the old one's
    expired. Same server-to-server pattern as the initial code exchange."""
    settings = get_settings()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            auth=(settings.spotify_client_id, settings.spotify_client_secret),
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Spotify token refresh failed: {response.status_code} {response.text}",
        )

    return response.json()


async def ensure_fresh_token(user: User, db: AsyncSession) -> str:
    """Returns a usable access token for `user`, refreshing it (and
    persisting the refresh) if it's expired. Shared by the request path
    below and the background worker, which has no request or cookie."""
    # 30s buffer.
    if user.expires_at <= time.time() + 30:
        if not user.refresh_token:
            raise HTTPException(status_code=401, detail="Session expired — please log in again.")
        refreshed = await _refresh(user.refresh_token)
        user.access_token = refreshed["access_token"]
        user.expires_at = time.time() + refreshed.get("expires_in", 3600)

        user.refresh_token = refreshed.get("refresh_token", user.refresh_token)
        await db.commit()

    return user.access_token


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """FastAPI dependency: resolve the caller's signed session to their
    Postgres row, transparently refreshing the Spotify token (and
    persisting the refresh) if it's expired. Returns the whole User row
    rather than just the token, so a route that also needs the Spotify
    user id (e.g. to create a playlist "for" them) doesn't need a second
    session read."""
    spotify_id = request.session.get(SESSION_USER_KEY)
    if not spotify_id:
        raise HTTPException(status_code=401, detail="Not authenticated — please log in.")

    user = await db.get(User, spotify_id)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated — please log in.")

    await ensure_fresh_token(user, db)
    return user


async def get_session_token(user: Annotated[User, Depends(get_current_user)]) -> str:
    """FastAPI dependency: just the access token, for the common case of a
    route that only talks to Spotify and never needs the user's own id.
    Built on top of get_current_user."""
    return user.access_token
