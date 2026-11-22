"""One-time upgrade for a database loaded before eras and languages existed (keeps all data).
Safe to run more than once.

    DB_USER=ikeating python3 migrate_v2.py      # asks for the MySQL password

Adds tracks.release_year, tracks.language, playlists.era_start/era_end and the
playlist_languages table, then fills in every track's (inferred) language.
"""
import getpass

import pymysql

import vibes
from config import DB


def connect():
    params = dict(DB)
    if not params["password"] and "read_default_file" not in params:
        params["password"] = getpass.getpass(f"MySQL password for {params['user'] or 'your account'}: ")
    return pymysql.connect(**params, charset="utf8mb4")


def has_column(cur, table, column):
    cur.execute("SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s", (table, column))
    return cur.fetchone() is not None


def main():
    conn = connect()
    with conn.cursor() as cur:
        if not has_column(cur, "tracks", "release_year"):
            cur.execute("ALTER TABLE tracks ADD COLUMN release_year SMALLINT, ADD INDEX idx_tracks_release_year (release_year)")
            print("added tracks.release_year")
        if not has_column(cur, "tracks", "language"):
            cur.execute("ALTER TABLE tracks ADD COLUMN language VARCHAR(20), ADD INDEX idx_tracks_language (language)")
            print("added tracks.language")
        if not has_column(cur, "playlists", "era_start"):
            cur.execute("ALTER TABLE playlists ADD COLUMN era_start SMALLINT, ADD COLUMN era_end SMALLINT")
            print("added playlists.era_start / era_end")
        cur.execute("""CREATE TABLE IF NOT EXISTS playlist_languages (
                           playlist_id INT NOT NULL,
                           language    VARCHAR(20) NOT NULL,
                           PRIMARY KEY (playlist_id, language),
                           FOREIGN KEY (playlist_id) REFERENCES playlists(playlist_id) ON DELETE CASCADE
                       ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

        print("inferring languages ...")
        cur.execute("""SELECT t.track_id, t.name, t.instrumentalness, GROUP_CONCAT(g.name) AS genres
                       FROM tracks t
                       JOIN track_genres tg ON tg.track_id = t.track_id
                       JOIN genres g ON g.genre_id = tg.genre_id
                       GROUP BY t.track_id""")
        rows = [(vibes.infer_language(name, (genres or "").split(","), instr), tid)
                for tid, name, instr, genres in cur.fetchall()]
        # load the guesses into a temporary table, then update every track with one join
        cur.execute("""CREATE TEMPORARY TABLE tmp_language (
                           track_id CHAR(22) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
                           language VARCHAR(20))""")
        for start in range(0, len(rows), 5000):
            cur.executemany("INSERT INTO tmp_language (language, track_id) VALUES (%s, %s)", rows[start:start + 5000])
        cur.execute("UPDATE tracks t JOIN tmp_language x ON x.track_id = t.track_id SET t.language = x.language")
        conn.commit()

        cur.execute("SELECT language, COUNT(*) FROM tracks GROUP BY language ORDER BY COUNT(*) DESC")
        print("languages:", ", ".join(f"{lang} {n:,}" for lang, n in cur.fetchall()))
    conn.close()
    print("done")


if __name__ == "__main__":
    main()
