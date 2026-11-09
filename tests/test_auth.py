"""Tests for login security: the signed session cookie, OAuth state kept
in Redis, logout, and the production SECRET_KEY check.

Uses a throwaway SQLite file and a tiny in-memory stand-in for Redis, so
no services are needed."""

import base64
import json
import time

import httpx
import pytest
from itsdangerous import TimestampSigner
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import (
    OAUTH_STATE_KEY_PREFIX,
    OAUTH_STATE_TTL_SECONDS,
    SESSION_COOKIE_NAME,
    SESSION_USER_KEY,
    consume_oauth_state,
    get_redis,
    save_oauth_state,
)
from app.config import Settings, get_settings
from app.db import Base, get_db
from app.main import app, check_settings
from app.models import User


class FakeRedis:
    """Just the two commands the OAuth state code uses."""

    def __init__(self):
        self.data: dict[str, tuple[str, int | None]] = {}

    async def set(self, key, value, ex=None):
        self.data[key] = (value, ex)

    async def getdel(self, key):
        entry = self.data.pop(key, None)
        return entry[0] if entry else None


def signed_session(data: dict, secret: str | None = None) -> str:
    """A session cookie value exactly as Starlette's SessionMiddleware writes it."""
    secret = secret or get_settings().secret_key
    payload = base64.b64encode(json.dumps(data).encode())
    return TimestampSigner(secret).sign(payload).decode()


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
async def client(tmp_path, redis):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(User(spotify_id="me", access_token="tok", expires_at=time.time() + 3600))
        await db.commit()

    async def db_override():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_redis] = lambda: redis
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


# GET /api/jobs/{id} for a job that doesn't exist: 401 if the session
# doesn't resolve to a user, 404 if it does. No Spotify call either way.
AUTH_PROBE = "/api/jobs/does-not-exist"


class TestSession:
    async def test_no_cookie_is_rejected(self, client):
        assert (await client.get(AUTH_PROBE)).status_code == 401

    async def test_signed_session_is_accepted(self, client):
        client.cookies.set(SESSION_COOKIE_NAME, signed_session({SESSION_USER_KEY: "me"}))
        assert (await client.get(AUTH_PROBE)).status_code == 404

    async def test_old_raw_id_cookie_no_longer_logs_you_in(self, client):
        client.cookies.set("spotify_user_id", "me")
        assert (await client.get(AUTH_PROBE)).status_code == 401

    async def test_session_signed_with_another_key_is_rejected(self, client):
        client.cookies.set(SESSION_COOKIE_NAME, signed_session({SESSION_USER_KEY: "me"}, secret="attacker-key"))
        assert (await client.get(AUTH_PROBE)).status_code == 401

    async def test_edited_session_is_rejected(self, client):
        genuine = signed_session({SESSION_USER_KEY: "someone-else"})
        # Swap in a different user's id but keep the original signature.
        _, timestamp, signature = genuine.split(".")
        forged_payload = base64.b64encode(json.dumps({SESSION_USER_KEY: "me"}).encode()).decode()
        client.cookies.set(SESSION_COOKIE_NAME, f"{forged_payload}.{timestamp}.{signature}")
        assert (await client.get(AUTH_PROBE)).status_code == 401

    async def test_logout_ends_the_session(self, client):
        client.cookies.set(SESSION_COOKIE_NAME, signed_session({SESSION_USER_KEY: "me"}))
        response = await client.post("/api/auth/logout")
        assert response.status_code == 200
        set_cookie = response.headers["set-cookie"]
        assert set_cookie.startswith(f"{SESSION_COOKIE_NAME}=null")
        assert "1970" in set_cookie  # expired, so the browser drops it


class TestOAuthState:
    async def test_login_stores_state_in_redis_with_an_expiry(self, client, redis, monkeypatch):
        monkeypatch.setattr(get_settings(), "spotify_client_id", "client-id")
        response = await client.get("/api/auth/login")
        assert response.status_code == 307
        state = httpx.URL(response.headers["location"]).params["state"]
        assert redis.data[OAUTH_STATE_KEY_PREFIX + state] == ("1", OAUTH_STATE_TTL_SECONDS)

    async def test_unknown_state_is_rejected(self, client):
        response = await client.get("/api/auth/callback", params={"code": "c", "state": "never-issued"})
        assert response.status_code == 400

    async def test_state_only_works_once(self, redis):
        await save_oauth_state(redis, "abc")
        assert await consume_oauth_state(redis, "abc") is True
        assert await consume_oauth_state(redis, "abc") is False

    async def test_good_callback_sets_a_signed_httponly_session(self, client, redis, monkeypatch):
        def spotify(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/token":
                return httpx.Response(200, json={"access_token": "new", "refresh_token": "r", "expires_in": 3600})
            return httpx.Response(200, json={"id": "me", "display_name": "Me"})

        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            "app.auth.httpx.AsyncClient", lambda: real_client(transport=httpx.MockTransport(spotify))
        )
        await save_oauth_state(redis, "abc")

        response = await client.get("/api/auth/callback", params={"code": "c", "state": "abc"})

        assert response.status_code == 307
        assert response.headers["location"] == get_settings().frontend_url
        session_cookie = next(
            h for h in response.headers.get_list("set-cookie") if h.startswith(SESSION_COOKIE_NAME)
        )
        assert "httponly" in session_cookie.lower()
        assert "samesite=lax" in session_cookie.lower()
        # The cookie holds a signature, not the bare id.
        assert response.cookies[SESSION_COOKIE_NAME] != "me"
        assert (await client.get(AUTH_PROBE)).status_code == 404

    async def test_reused_state_is_rejected_at_the_callback(self, client, redis):
        await save_oauth_state(redis, "abc")
        await consume_oauth_state(redis, "abc")  # the first, legitimate callback
        response = await client.get("/api/auth/callback", params={"code": "c", "state": "abc"})
        assert response.status_code == 400


class TestProductionSettings:
    @pytest.mark.parametrize("key", ["changeme", ""])
    def test_refuses_placeholder_secret_in_production(self, key):
        with pytest.raises(RuntimeError):
            check_settings(Settings(app_env="production", secret_key=key, _env_file=None))

    def test_allows_real_secret_in_production(self):
        check_settings(Settings(app_env="production", secret_key="x" * 48, _env_file=None))

    def test_placeholder_is_fine_in_development(self):
        check_settings(Settings(app_env="development", secret_key="changeme", _env_file=None))
