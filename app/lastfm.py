"""Genre tags per artist, from Last.fm's crowd-sourced tags.

Spotify no longer returns artist genres, and asking the LLM to guess them
from a title and artist name was the noisiest input to clustering (it
called ILLIT "electropop" and KiiiKiii "hip hop"). Last.fm's artist tags
are what real listeners tagged, and they covered every artist we tried.

Artist tags rather than track tags: track tags are missing for about 40%
of tracks and full of fan in-jokes ("petala owns this song"). Raw tags
are also inconsistent ("kpop" / "k-pop", "rnb" / "r&b") and mixed with
non-genres ("female vocalists", "british"), so only tags that map onto
GENRES are kept.

Optional like ReccoBeats: with no LASTFM_API_KEY, or when Last.fm fails,
tracks just keep their LLM genre tags.
"""

import asyncio
import logging
import time

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ArtistGenreCache

logger = logging.getLogger(__name__)

LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"
MAX_ATTEMPTS = 3
# Last.fm asks for no more than about 5 requests a second.
MAX_CONCURRENT_REQUESTS = 4
# Last.fm scores each tag 0 to 100 relative to the artist's top tag.
# Below this it's usually one listener's personal tag.
MIN_TAG_COUNT = 10
MAX_GENRES_PER_ARTIST = 4

GENRES = {
    "pop", "k-pop", "j-pop", "c-pop", "dance pop", "electropop", "synthpop", "indie pop",
    "dream pop", "alternative pop", "art pop", "dark pop", "bedroom pop", "hyperpop",
    "teen pop", "girl group", "boy band", "city pop",
    "r&b", "alternative r&b", "neo-soul", "soul", "funk", "disco",
    "hip hop", "rap", "pop rap", "trap", "drill",
    "rock", "indie rock", "alternative rock", "alternative", "indie", "garage rock",
    "punk", "pop punk", "post-punk", "emo", "grunge", "metal", "shoegaze", "j-rock",
    "folk", "indie folk", "singer-songwriter", "acoustic", "country",
    "electronic", "edm", "house", "deep house", "tech house", "future house", "techno",
    "dubstep", "drum and bass", "uk garage", "trance",
    "jazz", "blues", "classical", "soundtrack", "lo-fi", "ambient",
    "latin", "reggaeton", "afrobeats", "reggae",
}

# Other spellings of the same genre, mapped onto the GENRES spelling.
GENRE_ALIASES = {
    "kpop": "k-pop", "k pop": "k-pop", "jpop": "j-pop", "cpop": "c-pop",
    "dance-pop": "dance pop", "electro pop": "electropop", "electro-pop": "electropop",
    "synth pop": "synthpop", "synth-pop": "synthpop", "indie-pop": "indie pop",
    "dreampop": "dream pop", "alt-pop": "alternative pop", "alt pop": "alternative pop",
    "rnb": "r&b", "r and b": "r&b", "r'n'b": "r&b", "rhythm and blues": "r&b",
    "alternative rnb": "alternative r&b", "alternative r and b": "alternative r&b",
    "neo soul": "neo-soul", "hip-hop": "hip hop", "hiphop": "hip hop",
    "indie-rock": "indie rock", "alt rock": "alternative rock", "alternative-rock": "alternative rock",
    "pop-punk": "pop punk", "post punk": "post-punk", "singer songwriter": "singer-songwriter",
    "electronica": "electronic", "drum n bass": "drum and bass", "dnb": "drum and bass",
    "lofi": "lo-fi", "lo fi": "lo-fi", "afrobeat": "afrobeats",
}


def normalize_genre(tag: str) -> str | None:
    """The GENRES spelling of a Last.fm tag, or None if it isn't a genre."""
    tag = " ".join(tag.lower().split())
    tag = GENRE_ALIASES.get(tag, tag)
    return tag if tag in GENRES else None


def genres_from_tags(tags: list[dict]) -> list[str]:
    """Last.fm's tag list (already sorted by count) down to at most
    MAX_GENRES_PER_ARTIST known genres, most-tagged first."""
    genres: list[str] = []
    for tag in tags:
        try:
            count = int(tag.get("count", 0))
        except (TypeError, ValueError):
            continue
        genre = normalize_genre(tag.get("name", ""))
        if count >= MIN_TAG_COUNT and genre and genre not in genres:
            genres.append(genre)
        if len(genres) == MAX_GENRES_PER_ARTIST:
            break
    return genres


async def fetch_artist_genres(client: httpx.AsyncClient, artist_name: str, api_key: str) -> list[str] | None:
    """Genres for one artist ([] if Last.fm has none that are genres), or
    None if the request failed, so the caller doesn't cache a false miss."""
    params = {
        "method": "artist.gettoptags",
        "artist": artist_name,
        "autocorrect": 1,
        "api_key": api_key,
        "format": "json",
    }
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.get(LASTFM_API_URL, params=params)
        except httpx.TimeoutException:
            if attempt < MAX_ATTEMPTS:
                continue
            logger.warning("Last.fm timed out for artist %r", artist_name)
            return None
        except httpx.HTTPError as e:
            logger.warning("Last.fm request failed for artist %r: %s", artist_name, e)
            return None

        data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        # Error 6 is "artist not found", a real answer. Anything else
        # (rate limit 29, bad key 10, server trouble) is a failure.
        if data.get("error") == 6:
            return []
        if response.status_code == 429 or data.get("error") == 29:
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(2**attempt)
                continue
        if response.status_code != 200 or "error" in data:
            logger.warning("Last.fm returned %s for artist %r: %s", response.status_code, artist_name, response.text[:200])
            return None
        return genres_from_tags(data.get("toptags", {}).get("tag", []))
    return None


async def get_artist_genres(
    db: AsyncSession, artists: dict[str, str], ttl_seconds: float
) -> tuple[dict[str, list[str]], int]:
    """Genres per Spotify artist id, from the cache where possible.
    `artists` is {spotify_artist_id: name}. Returns ({id: genres}, number
    of cache hits); artists with no known genres map to []."""
    api_key = get_settings().lastfm_api_key
    if not artists or not api_key:
        return {}, 0

    now = time.time()
    rows = await db.execute(select(ArtistGenreCache).where(ArtistGenreCache.artist_id.in_(artists.keys())))
    cached = {row.artist_id: row for row in rows.scalars()}
    fresh = {artist_id: row.genres for artist_id, row in cached.items() if now - row.fetched_at < ttl_seconds}

    missing = sorted(artists.keys() - fresh.keys())
    if missing:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

        async def fetch(client: httpx.AsyncClient, artist_id: str) -> tuple[str, list[str] | None]:
            async with semaphore:
                return artist_id, await fetch_artist_genres(client, artists[artist_id], api_key)

        async with httpx.AsyncClient(timeout=10) as client:
            fetched = await asyncio.gather(*(fetch(client, artist_id) for artist_id in missing))

        for artist_id, genres in fetched:
            if genres is None:
                continue  # failed, so ask again next time
            row = cached.get(artist_id)
            if row:
                row.genres = genres
                row.fetched_at = now
            else:
                db.add(ArtistGenreCache(artist_id=artist_id, genres=genres, fetched_at=now))
            fresh[artist_id] = genres
        await db.commit()

    return fresh, len(artists) - len(missing)


def track_genres(track: dict, genres_by_artist: dict[str, list[str]]) -> list[str]:
    """A track's genres from its artists' Last.fm genres, lead artist
    first, capped like a single artist's. [] if none of them are known."""
    genres: list[str] = []
    for artist in track.get("artists", []):
        for genre in genres_by_artist.get(artist.get("id") or "", []):
            if genre not in genres:
                genres.append(genre)
    return genres[:MAX_GENRES_PER_ARTIST]
