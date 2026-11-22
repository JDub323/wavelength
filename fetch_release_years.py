"""Fill tracks.release_year from the Spotify Web API so vibes like "90s road trip" can filter by era.

    SPOTIFY_CLIENT_ID=... DB_USER=ikeating python3 fetch_release_years.py --limit 5000

Needs a Spotify developer app (https://developer.spotify.com/dashboard). Since February 2026
new apps in Development Mode can only look tracks up one at a time (GET /v1/tracks/{id}), and the
app owner needs Spotify Premium. Every track on an album shares its release date, so this asks
for one track per album - most popular albums first - and copies the year to the whole album.

Stop it any time (Ctrl+C) and run it again later: it only asks about albums that still have no year.
"""
import argparse
import base64
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pymysql

from config import DB

API = "https://api.spotify.com/v1/tracks/"
TOKEN_URL = "https://accounts.spotify.com/api/token"


def connect():
    params = dict(DB)
    if not params["password"] and "read_default_file" not in params:
        params["password"] = getpass.getpass(f"MySQL password for {params['user'] or 'your account'}: ")
    return pymysql.connect(**params, charset="utf8mb4")


class Spotify:
    def __init__(self, client_id, client_secret):
        self.auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self.token, self.expires = None, 0

    def _refresh(self):
        req = urllib.request.Request(TOKEN_URL, data=b"grant_type=client_credentials",
                                     headers={"Authorization": f"Basic {self.auth}",
                                              "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.load(resp)
        self.token, self.expires = body["access_token"], time.time() + body["expires_in"] - 60

    def release_year(self, track_id):
        """Album release year for a track, or None if Spotify doesn't know it. Raises SystemExit on a long rate limit."""
        for attempt in range(5):
            if time.time() > self.expires:
                self._refresh()
            req = urllib.request.Request(API + urllib.parse.quote(track_id),
                                         headers={"Authorization": f"Bearer {self.token}"})
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    date = json.load(resp)["album"].get("release_date") or ""
                year = int(date[:4]) if date[:4].isdigit() else 0
                return year if 1900 <= year <= 2100 else None
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    self.expires = 0  # token expired early: get a new one
                elif e.code == 429:
                    wait = int(e.headers.get("Retry-After", "30"))
                    if wait > 300:
                        raise SystemExit(f"\nSpotify rate limit: asked to wait {wait // 60} minutes. "
                                         "Progress is saved - run this again later to continue.")
                    print(f"  rate limited, waiting {wait}s ...")
                    time.sleep(wait + 1)
                elif e.code in (400, 404):
                    return None
                else:
                    time.sleep(2 ** attempt)
            except (urllib.error.URLError, TimeoutError):
                time.sleep(2 ** attempt)
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="stop after this many Spotify requests")
    ap.add_argument("--min-popularity", type=int, default=15,
                    help="skip albums whose most popular track is below this (default 15, same as the feed)")
    ap.add_argument("--rate", type=float, default=4.0, help="max requests per second (default 4)")
    args = ap.parse_args()

    client_id = os.environ.get("SPOTIFY_CLIENT_ID") or input("Spotify client ID: ").strip()
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET") or getpass.getpass("Spotify client secret: ").strip()
    spotify = Spotify(client_id, client_secret)
    try:
        spotify._refresh()
    except urllib.error.HTTPError as e:
        sys.exit(f"Spotify rejected the client ID/secret (HTTP {e.code}).")

    conn = connect()
    with conn.cursor() as cur:
        # one track per album still missing a year, most popular albums first; tracks with no album go alone
        cur.execute("""
            SELECT t.album_id, MIN(t.track_id), MAX(t.popularity) AS pop
            FROM tracks t
            WHERE t.release_year IS NULL AND t.album_id IS NOT NULL
            GROUP BY t.album_id HAVING MAX(t.popularity) >= %s
            UNION ALL
            SELECT NULL, t.track_id, t.popularity
            FROM tracks t
            WHERE t.release_year IS NULL AND t.album_id IS NULL AND t.popularity >= %s
            ORDER BY pop DESC""", (args.min_popularity, args.min_popularity))
        todo = cur.fetchall()
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo):,} Spotify lookups to do (about {len(todo) / args.rate / 60:.0f} minutes at {args.rate:g}/s)")

    start, found, gap = time.time(), 0, 1 / args.rate
    try:
        with conn.cursor() as cur:
            for n, (album_id, track_id, _pop) in enumerate(todo, 1):
                t0 = time.time()
                year = spotify.release_year(track_id)
                if year:
                    found += 1
                    if album_id is None:
                        cur.execute("UPDATE tracks SET release_year = %s WHERE track_id = %s", (year, track_id))
                    else:
                        cur.execute("UPDATE tracks SET release_year = %s WHERE album_id = %s AND release_year IS NULL",
                                    (year, album_id))
                if n % 50 == 0:
                    conn.commit()
                if n % 250 == 0 or n == len(todo):
                    rate = n / (time.time() - start)
                    print(f"  {n:,}/{len(todo):,} looked up, {found:,} years found, "
                          f"~{(len(todo) - n) / rate / 60:.0f} min left")
                time.sleep(max(0, gap - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\nstopped - progress saved")
    finally:
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), SUM(release_year IS NOT NULL) FROM tracks")
            total, dated = cur.fetchone()
        print(f"{int(dated or 0):,} of {total:,} tracks now have a release year")
        conn.close()


if __name__ == "__main__":
    main()
