"""Load the Kaggle "Spotify Tracks Dataset" (maharshipandya) into MySQL, then generate
synthetic users and their activity (friends, playlists, swipes, listening history).

    python load_data.py                      # reads data/dataset.csv
    python load_data.py path/to/dataset.csv --users 1000

Download the dataset from https://www.kaggle.com/datasets/maharshipandya/-spotify-tracks-dataset
and unzip dataset.csv into data/. Connection settings live in config.py.
Every music row comes from that CSV; only users and their activity are synthetic.
"""
import argparse
import csv
import os
import random
import re
import sys
import time
from bisect import bisect_right
from datetime import datetime, timedelta

import pymysql
from werkzeug.security import generate_password_hash

import vibes
from config import DB

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(HERE, "data", "dataset.csv")
DEMO_PASSWORD = "password123"  # every synthetic user gets this password
EXPECTED = ["track_id", "artists", "album_name", "track_name", "popularity", "duration_ms", "explicit",
            "danceability", "energy", "key", "loudness", "mode", "speechiness", "acousticness",
            "instrumentalness", "liveness", "valence", "tempo", "time_signature", "track_genre"]
MAX_NAME = 512

FIRST = ["alex", "sam", "jordan", "taylor", "casey", "riley", "morgan", "jamie", "avery", "quinn",
         "maria", "liam", "noah", "emma", "olivia", "ava", "mia", "lucas", "ethan", "sofia",
         "mateo", "isabella", "aiden", "chloe", "grace", "leo", "zoe", "nora", "owen", "ella",
         "jack", "lily", "henry", "aria", "kai", "maya", "eli", "ruby", "theo", "ivy"]
LAST = ["smith", "johnson", "lee", "garcia", "brown", "nguyen", "patel", "kim", "martinez", "davis",
        "lopez", "wilson", "anderson", "thomas", "moore", "clark", "lewis", "walker", "hall", "young",
        "obrien", "murphy", "kelly", "ryan", "sullivan", "chen", "wang", "singh", "ali", "khan"]
PLAYLIST_SUFFIXES = ["Vibes", "Mix", "Essentials", "for the Gym", "Late Night", "Road Trip",
                     "Study Session", "Throwbacks", "Discoveries", "Rotation", "Mood", "Party"]


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def i(v):
    x = f(v)
    return int(x) if x is not None else None


def connect():
    """Connect to the server, creating the database if it doesn't exist (and we're allowed to)."""
    params = {k: v for k, v in DB.items() if k != "database"}
    conn = pymysql.connect(**params, charset="utf8mb4", autocommit=False)
    with conn.cursor() as cur:
        try:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB['database']}` CHARACTER SET utf8mb4")
        except pymysql.err.OperationalError:
            pass  # no CREATE privilege (e.g. a shared course server) - the database must already exist
    try:
        conn.select_db(DB["database"])
    except pymysql.err.OperationalError as e:
        sys.exit(f"Can't use database '{DB['database']}': {e.args[1]}\n"
                 "Run  mysql -u <user> -p -e \"SHOW DATABASES;\"  and put one you can access in config.py.")
    return conn


def run_schema(conn):
    with open(os.path.join(HERE, "schema.sql"), encoding="utf-8") as fh:
        sql = re.sub(r"--[^\n]*", "", fh.read())  # drop comments so a ";" inside one can't split a statement
    with conn.cursor() as cur:
        for stmt in sql.split(";"):
            if stmt.strip():
                cur.execute(stmt)
    conn.commit()


def insert_many(cur, sql, rows, chunk=5000):
    for start in range(0, len(rows), chunk):
        cur.executemany(sql, rows[start:start + chunk])


def load_catalog(conn, csv_path):
    csv.field_size_limit(10_000_000)
    genres, artists, albums = {}, {}, {}
    tracks, track_artists, track_genres = {}, set(), set()
    genre_names_of = {}  # track_id -> genre names, for language inference

    def get_id(cache, key):
        if key not in cache:
            cache[key] = len(cache) + 1
        return cache[key]

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in EXPECTED if c not in reader.fieldnames]
        if missing:
            sys.exit(f"{csv_path} doesn't look like the Spotify Tracks Dataset; missing columns {missing}")
        for row in reader:
            track_id = row["track_id"].strip()
            name = row["track_name"].strip()[:MAX_NAME]
            artist_names = [a.strip()[:MAX_NAME] for a in row["artists"].split(";") if a.strip()]
            if len(track_id) != 22 or not name or not artist_names:
                continue  # a handful of rows have no title/artist
            genre_id = get_id(genres, row["track_genre"].strip().lower())
            track_genres.add((track_id, genre_id))
            genre_names_of.setdefault(track_id, set()).add(row["track_genre"].strip().lower())
            if track_id in tracks:  # same track listed again under another genre
                continue
            artist_ids = [get_id(artists, a) for a in artist_names]
            album_name = row["album_name"].strip()[:MAX_NAME]
            album_id = get_id(albums, (album_name, artist_ids[0])) if album_name else None
            tracks[track_id] = (
                track_id, name, album_id, i(row["popularity"]), i(row["duration_ms"]),
                row["explicit"].strip().lower() == "true", f(row["danceability"]), f(row["energy"]),
                i(row["key"]), f(row["loudness"]), i(row["mode"]), f(row["speechiness"]),
                f(row["acousticness"]), f(row["instrumentalness"]), f(row["liveness"]), f(row["valence"]),
                f(row["tempo"]), i(row["time_signature"]))
            track_artists.update((track_id, aid) for aid in artist_ids)

    with conn.cursor() as cur:
        insert_many(cur, "INSERT INTO genres (genre_id, name) VALUES (%s, %s)",
                    [(gid, n) for n, gid in genres.items()])
        insert_many(cur, "INSERT INTO artists (artist_id, name) VALUES (%s, %s)",
                    [(aid, n) for n, aid in artists.items()])
        insert_many(cur, "INSERT INTO albums (album_id, name, artist_id) VALUES (%s, %s, %s)",
                    [(alid, n, aid) for (n, aid), alid in albums.items()])
        insert_many(cur, "INSERT INTO tracks (track_id, name, album_id, popularity, duration_ms, explicit, "
                         "danceability, energy, musical_key, loudness, mode, speechiness, acousticness, "
                         "instrumentalness, liveness, valence, tempo, time_signature, language) "
                         "VALUES (" + ", ".join(["%s"] * 19) + ")",
                    [t + (vibes.infer_language(t[1], genre_names_of[t[0]], t[13]),) for t in tracks.values()])
        insert_many(cur, "INSERT INTO track_artists (track_id, artist_id) VALUES (%s, %s)", sorted(track_artists))
        insert_many(cur, "INSERT INTO track_genres (track_id, genre_id) VALUES (%s, %s)", sorted(track_genres))
    conn.commit()
    return len(tracks)


def rand_time(rng, days_back=180, after=None):
    now = datetime.now().replace(microsecond=0)
    start = after or now - timedelta(days=days_back)
    span = max(1, int((now - start).total_seconds()))
    return start + timedelta(seconds=rng.randrange(span))


def generate_users_and_activity(conn, n_users, seed=42):
    rng = random.Random(seed)
    pw_hash = generate_password_hash(DEMO_PASSWORD)  # one hash reused so seeding stays fast
    with conn.cursor() as cur:
        cur.execute("""SELECT tg.genre_id, t.track_id, COALESCE(t.popularity, 0), COALESCE(t.duration_ms, 200000)
                       FROM track_genres tg JOIN tracks t ON t.track_id = tg.track_id""")
        by_genre = {}
        for gid, track_id, pop, dur in cur.fetchall():
            by_genre.setdefault(gid, []).append((track_id, pop + 1, dur))
        cur.execute("SELECT genre_id, name FROM genres")
        genre_names = dict(cur.fetchall())

    # tracks per genre weighted by popularity, so synthetic users mostly play well-known songs
    pools = {}
    for gid, items in by_genre.items():
        cum, total = [], 0
        for _, w, _ in items:
            total += w
            cum.append(total)
        pools[gid] = (items, cum)
    genre_ids = list(pools)

    def sample(gid, k):
        items, cum = pools[gid]
        return [items[bisect_right(cum, rng.random() * cum[-1])] for _ in range(k)]

    # users
    usernames, users = set(), []
    while len(users) < n_users:
        first, last = rng.choice(FIRST), rng.choice(LAST)
        uname = f"{first}{last[0]}{rng.randint(1, 999)}" if rng.random() < 0.6 else f"{first}_{last}"
        if uname in usernames:
            continue
        usernames.add(uname)
        users.append((len(users) + 1, uname, pw_hash, f"{first.title()} {last.title()}",
                      round(rng.uniform(0.1, 0.6), 2), rand_time(rng, 365)))
    user_ids = [u[0] for u in users]
    favorites = {uid: rng.sample(genre_ids, min(3, len(genre_ids))) for uid in user_ids}

    # friendships
    pairs, friend_rows = set(), []
    for uid in user_ids:
        for fid in rng.sample(user_ids, rng.randint(3, 8)):
            key = frozenset((uid, fid))
            if fid == uid or key in pairs:
                continue
            pairs.add(key)
            friend_rows.append((uid, fid, "accepted" if rng.random() < 0.85 else "pending", rand_time(rng, 200)))
    friends_of = {}
    for a, b, status, _ in friend_rows:
        if status == "accepted":
            friends_of.setdefault(a, []).append(b)
            friends_of.setdefault(b, []).append(a)

    # listening history
    history = []
    for uid in user_ids:
        for _ in range(rng.randint(60, 220)):
            gid = rng.choice(favorites[uid]) if rng.random() < 0.7 else rng.choice(genre_ids)
            track_id, _, dur = sample(gid, 1)[0]
            history.append((uid, track_id, rand_time(rng, 120), int(dur * rng.uniform(0.3, 1.0))))

    # playlists, their tracks, collaborators, and the swipes that built them
    playlists, pt_rows, swipe_rows, collab_rows = [], [], [], []
    for uid in user_ids:
        for _ in range(rng.randint(1, 4)):
            pid = len(playlists) + 1
            gid = rng.choice(favorites[uid])
            created = rand_time(rng, 150)
            playlists.append((pid, uid, f"{genre_names[gid].title()} {rng.choice(PLAYLIST_SUFFIXES)}", gid,
                              f"Swiped together from {genre_names[gid]}", created))
            collaborators = []
            if friends_of.get(uid) and rng.random() < 0.2:
                collaborators = [rng.choice(friends_of[uid])]
                collab_rows.append((pid, collaborators[0]))
            chosen = {t[0] for t in sample(gid, rng.randint(8, 30))}
            for track_id in chosen:
                adder = rng.choice([uid] + collaborators)
                t = rand_time(rng, after=created)
                pt_rows.append((pid, track_id, adder, t))
                swipe_rows.append((adder, pid, track_id, True, t))
            for track_id, _, _ in sample(gid, int(len(chosen) * rng.uniform(1.0, 2.5))):
                if track_id not in chosen:
                    swipe_rows.append((uid, pid, track_id, False, rand_time(rng, after=created)))

    with conn.cursor() as cur:
        insert_many(cur, "INSERT INTO users (user_id, username, password_hash, display_name, exploration, created_at) "
                         "VALUES (%s, %s, %s, %s, %s, %s)", users)
        insert_many(cur, "INSERT INTO friendships (user_id, friend_id, status, created_at) VALUES (%s, %s, %s, %s)",
                    friend_rows)
        insert_many(cur, "INSERT INTO listening_history (user_id, track_id, played_at, ms_played) "
                         "VALUES (%s, %s, %s, %s)", history)
        insert_many(cur, "INSERT INTO playlists (playlist_id, owner_id, name, genre_id, description, created_at) "
                         "VALUES (%s, %s, %s, %s, %s, %s)", playlists)
        insert_many(cur, "INSERT INTO playlist_tracks (playlist_id, track_id, added_by, added_at) "
                         "VALUES (%s, %s, %s, %s)", pt_rows)
        insert_many(cur, "INSERT INTO playlist_collaborators (playlist_id, user_id) VALUES (%s, %s)", collab_rows)
        insert_many(cur, "INSERT INTO swipes (user_id, playlist_id, track_id, liked, swiped_at) "
                         "VALUES (%s, %s, %s, %s, %s)", swipe_rows)
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="?", default=DEFAULT_CSV, help="path to dataset.csv (default: data/dataset.csv)")
    ap.add_argument("--users", type=int, default=500, help="number of synthetic users (default 500)")
    args = ap.parse_args()
    if not os.path.exists(args.csv):
        sys.exit(f"Can't find {args.csv}.\nDownload it from "
                 "https://www.kaggle.com/datasets/maharshipandya/-spotify-tracks-dataset and unzip dataset.csv there.")

    start = time.time()
    conn = connect()
    run_schema(conn)
    n = load_catalog(conn, args.csv)
    print(f"Imported {n:,} unique tracks from {args.csv}")
    generate_users_and_activity(conn, args.users)
    print(f"\nLoaded MySQL database '{DB['database']}' on {DB['host']} in {time.time() - start:.1f}s")

    total = 0
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name")
        for (table,) in cur.fetchall():
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            c = cur.fetchone()[0]
            total += c
            print(f"  {table:<24}{c:>10,}")
        print(f"  {'TOTAL':<24}{total:>10,}")
        cur.execute("SELECT username FROM users ORDER BY user_id LIMIT 3")
        print(f"\nEvery synthetic user's password is '{DEMO_PASSWORD}'. Example usernames:",
              ", ".join(r[0] for r in cur.fetchall()))
    conn.close()


if __name__ == "__main__":
    main()
