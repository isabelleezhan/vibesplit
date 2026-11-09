from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Reads .env via pydantic-settings
class Settings(BaseSettings):
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    # Signs the session cookie (app/main.py). Must be a long random value
    # in production: python -c "import secrets; print(secrets.token_urlsafe(48))"
    secret_key: str = "changeme"

    # --- Spotify OAuth ---
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    # The API lives under /api on the same origin as the frontend: CloudFront
    # in production, Vite's dev proxy locally (frontend/vite.config.ts).
    spotify_redirect_uri: str = "http://127.0.0.1:5173/api/auth/callback"

    # Where to send the browser after a successful OAuth callback
    frontend_url: str = "http://127.0.0.1:5173"

    # --- Postgres / Redis ---
    database_url: str = ""
    redis_url: str = ""

    # --- Groq (cluster naming) ---
    # Free tier, no credit card — get a key at https://console.groq.com/keys
    groq_api_key: str = ""

    # --- Last.fm (artist genre tags) ---
    # Optional. Free key at https://www.last.fm/api/account/create
    lastfm_api_key: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

@lru_cache
def get_settings() -> Settings:
    return Settings()
