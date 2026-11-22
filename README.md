# Song Tinder — Stage 3 demo

Flask + MySQL web app. The music catalog is the Kaggle
[Spotify Tracks Dataset](https://www.kaggle.com/datasets/maharshipandya/-spotify-tracks-dataset)
(~114k rows, ~89k unique tracks, 114 genres). Users and their activity (friends, playlists, swipes,
listening history) are synthetic, generated on top of those real tracks.

## Setup

1. **MySQL** — any MySQL 5.7+/8.x server (local install or the course server). Put its host, user,
   password and database name in [config.py](config.py) (or set `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`).
   The loader creates the database if your account is allowed to; otherwise create it first.
2. **Data** — download the dataset from Kaggle and unzip `dataset.csv` into `data/`.
3. **Install, load, run:**

```
pip install -r requirements.txt
python load_data.py            # drops + recreates all tables, loads the CSV, generates users
python app.py                  # http://127.0.0.1:5000
```

`python load_data.py path\to\dataset.csv --users 1000` to use another path or more synthetic users.
Every synthetic user's password is `password123`; the loader prints a few usernames to log in with.

## Running on the course server (db8.cse.nd.edu)

From your laptop (on campus or the ND VPN), copy the project up, then log in:

```
scp -r "C:\Users\jjdor\OneDrive - nd.edu\Desktop\Databses Project" NETID@db8.cse.nd.edu:~/songtinder
ssh NETID@db8.cse.nd.edu
```

On db8:

```
cd ~/songtinder
python3 -m pip install --user -r requirements.txt
nano config.py                       # MySQL host/user/password/database from the professor
python3 load_data.py                 # reads data/dataset.csv
HOST=0.0.0.0 PORT=YOURPORT nohup python3 app.py > app.log 2>&1 &
```

Then browse to `http://db8.cse.nd.edu:YOURPORT`. Use the port the course assigned you (ports are shared
with other students). Stop it later with `pkill -f "python3 app.py"`.

## Schema (13 relations)

| Relation | Source | What it holds |
|---|---|---|
| `tracks` | dataset | Spotify track ID, name, popularity, duration, explicit, audio features (energy, valence, tempo, key…) |
| `artists`, `albums`, `genres` | dataset | normalized out of the CSV |
| `track_artists` | dataset | many-to-many track ↔ artist (the CSV's `artists` column is `;`-separated) |
| `track_genres` | dataset | many-to-many track ↔ genre (the CSV repeats a track once per genre) |
| `users` | app / synthetic | username, salted password hash, exploration setting |
| `playlists`, `playlist_tracks`, `playlist_collaborators` | app / synthetic | playlists, their songs, shared editors |
| `swipes` | app / synthetic | every left/right swipe |
| `friendships` | app / synthetic | pending/accepted friend requests |
| `listening_history` | synthetic | plays with `ms_played` (drives the "Wrapped" stats) |

The **Database** page (`/stats`) shows the live row count of every table.

## Demo script (15 min)

1. **Real data** — open **Database** (`/stats`): row counts for all 13 relations.
2. **Insert** — **Sign up** a new account → `INSERT INTO users …`. The flash message shows the new `user_id`; the
   *SQL run for this page* panel at the bottom shows the exact statement (password shown as `<password hash>`).
   Try the same username again → rejected.
3. **Query** — **Log out**, **Log in** → `SELECT … FROM users WHERE username = %s` + hash check. Wrong password → rejected.
4. **Search** — Songs tab: search an artist (e.g. "Taylor Swift") and filter by genre → query across
   `tracks`, `artists`, `track_artists`, `albums`, `track_genres`, `genres`. People tab: search users.
   My playlists tab: search inside your playlists.
5. **Insert / update / delete** — create a playlist, add a song from Search, rename it (UPDATE), remove a song and delete the playlist (DELETE).
6. **Preview of the core feature** — *Start swiping* on a playlist: random unswiped song from the playlist's genre with
   a Spotify embed player; ← / → keys swipe; right-swipes insert into `playlist_tracks`.
7. Log in as a seeded user to show **Your Wrapped** and the **friends feed**.

All queries are parameterized (`%s` placeholders), so input like `' OR 1=1 --` is harmless.

## Plan for the next stage (advanced functions)

1. **Spotify OAuth + playlist export** — "Log in with Spotify" (Authorization Code + PKCE); push a finished playlist to the
   user's Spotify account (`POST /v1/users/{id}/playlists`, `POST /v1/playlists/{id}/tracks`).
2. **In-app playback** — the swipe card already uses Spotify's embed iframe; next is the Web Playback SDK for auto-play on each new card.
3. **Exploration-aware recommendation** — score candidates by distance between a track's audio-feature vector
   (energy, danceability, valence, acousticness, tempo) and the centroid of the playlist's right-swipes; with probability
   `users.exploration` draw from neighboring genres instead. The setting becomes a slider in user settings.
4. **Collaborative playlists** — invite friends (`playlist_collaborators`), see who added each song.
5. **Real listening stats** — import recently-played from Spotify into `listening_history` so Wrapped reflects real listening.
