"""Captures sample playlists for the no-login demo (app/demo.py).

Run it once, locally, after logging in to the local app with your own
Spotify account (it uses the tokens that login saved in Postgres):

    .venv/bin/python -m scripts.capture_demo_fixture <playlist_id> [<playlist_id> ...]

A playlist id is the part after /playlist/ in its Spotify share link.
2 or 3 playlists of roughly 80-300 tracks each make a good demo.

For each playlist this fetches the tracks plus the Last.fm genres and
ReccoBeats audio features the real pipeline uses, splits it once, asks
Groq to name the clusters once, and writes everything to
app/demo_data/demo_playlists.json. Only public track data is saved: names,
artists, release dates, genres, audio features. Not your account details,
and not the playlist's owner.
"""

import argparse
import asyncio
import copy
import json
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from app.auth import SPOTIFY_API_BASE, ensure_fresh_token
from app.clustering import cluster_tracks
from app.db import async_session_factory, engine
from app.demo import FIXTURE_PATH, _track_key
from app.labeling import label_clusters
from app.models import User
from app.pipeline import ALGORITHM_VERSION
from app.tracks import get_enriched_playlist_tracks

TRACK_FIELDS = ("id", "name", "is_local", "popularity", "release_date", "artists", "genres", "audio")


async def _playlist_info(playlist_id: str, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{SPOTIFY_API_BASE}/playlists/{playlist_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "name,images"},
        )
    response.raise_for_status()
    return response.json()


async def capture(playlist_ids: list[str], user_id: str | None) -> None:
    async with async_session_factory() as db:
        users = (await db.execute(select(User))).scalars().all()
        if user_id:
            users = [u for u in users if u.spotify_id == user_id]
        if len(users) != 1:
            ids = ", ".join(u.spotify_id for u in users) or "none"
            raise SystemExit(
                f"Need exactly one logged-in user, found: {ids}. "
                "Log in to the local app first, or pass --user <spotify id>."
            )
        user = users[0]
        access_token = await ensure_fresh_token(user, db)

        playlists = []
        for number, playlist_id in enumerate(playlist_ids, start=1):
            info = await _playlist_info(playlist_id, access_token)
            print(f"[{number}/{len(playlist_ids)}] {info['name']}: fetching tracks…")
            enriched = await get_enriched_playlist_tracks(playlist_id, access_token, db)
            tracks = [{field: track.get(field) for field in TRACK_FIELDS} for track in enriched]

            # The one LLM call: name this split's clusters, and save the
            # names so the demo never has to ask again.
            labeled = await label_clusters(cluster_tracks(copy.deepcopy(tracks)))
            labels = [
                {"label": cluster["label"], "track_keys": [_track_key(t) for t in cluster["tracks"]]}
                for cluster in labeled["clusters"]
            ]
            print(f"    {len(tracks)} tracks -> " + ", ".join(f"{c['label']} ({c['track_count']})" for c in labeled["clusters"]))

            images = info.get("images") or []
            playlists.append(
                {
                    "id": f"demo-{number}",
                    "name": info["name"],
                    "image_url": images[0]["url"] if images else None,
                    "tracks": tracks,
                    "labels": labels,
                }
            )

    fixture = {
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "algorithm_version": ALGORITHM_VERSION,
        "playlists": playlists,
    }
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(fixture, ensure_ascii=False, separators=(",", ":")))
    size_kb = FIXTURE_PATH.stat().st_size / 1024
    print(f"Wrote {FIXTURE_PATH} ({size_kb:.0f} KB, {len(playlists)} playlists)")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("playlist_ids", nargs="+", help="Spotify playlist ids to capture")
    parser.add_argument("--user", help="Spotify id of the local user whose login to use, if there are several")
    args = parser.parse_args()
    start = time.perf_counter()
    asyncio.run(capture(args.playlist_ids, args.user))
    print(f"Done in {time.perf_counter() - start:.1f}s")


if __name__ == "__main__":
    main()
