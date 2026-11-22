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

## Vibe playlists (advanced function)

When creating a playlist, describe the vibe in plain words ("a nighttime drive", "rainy sunday morning coffee").
The app picks songs whose *sound* matches, and the swipe feed keeps learning from your swipes.

1. **Prompt → target.** [vibes.py](vibes.py) holds 28 hand-written reference vibes, each with target audio features
   (energy, valence, danceability, acousticness, instrumentalness, tempo) and genre weights. The prompt and every
   reference description are turned into vectors by a small sentence-embedding model (fastembed,
   `BAAI/bge-small-en-v1.5`, CPU only). The closest references by cosine similarity are blended into one target point.
   A genre named in the prompt ("k-pop workout") gets full weight. The target is stored on the playlist
   (`playlists.target_*`) and the genre weights in `playlist_genres`.
2. **Ranking in SQL.** Each track's score is a weighted squared distance between its audio features and the target
   (z-scored, so tempo in bpm and the 0–1 features count equally), minus a genre bonus and a small popularity bonus.
   `ORDER BY score` over all ~89k tracks takes well under a second. Songs already in the playlist or already swiped are skipped.
3. **Swipe refinement.** A right swipe moves the target 20% of the way toward that song and raises its genres'
   weights. A left swipe moves the target 5% away and lowers them. Each is one `UPDATE … JOIN` statement.
4. **Exploration.** With probability `users.exploration` (a slider on the swipe page, saved as soon as it moves),
   the next card is picked at random from the top 300 matches instead of the top 20.
5. **Manual tuning.** The playlist page's Vibe sliders (energy, mood, dance, acoustic, instrumental, tempo) can be
   dragged by hand; *Update matches* saves them as the new target.
6. **Era.** A decade or year in the prompt ("90s", "early 2000s", "classic", "2015") sets `playlists.era_start/era_end`,
   which limit matches to that release-year range. Editable next to the sliders. Needs release years (below).
7. **Languages.** Pick any number of languages from the dropdown when creating a playlist (or next to the sliders).
   The dataset has no language field, so `tracks.language` is inferred: the title's alphabet (Hangul, kana, Han,
   Cyrillic, Arabic, Indic), then language-specific genres (k-pop, sertanejo, french, ...), then instrumentalness.
   Everything else counts as English, so non-English songs filed under general genres (pop, rock) are mislabeled.

Playlists made without a vibe start from the average sound of their genre, or of the whole catalog.
Limitation: only vibes that show up in the audio features work well. "Songs about the ocean" can't be measured.

The model (~65 MB) downloads to `~/.cache/fastembed` on the first vibe prompt, so the first one takes several seconds.
Without fastembed installed, the app falls back to keyword matching against the reference vibes.

Set `SHOW_SQL=1` when starting the app to show the SQL each page ran at the bottom of the page.

### Release years from Spotify

The dataset has no release dates. [fetch_release_years.py](fetch_release_years.py) looks them up with the Spotify
Web API and stores them in `tracks.release_year`.

1. Create an app at https://developer.spotify.com/dashboard (the account needs Spotify Premium, as of February 2026)
   and copy its Client ID and Client Secret.
2. Run it on db8 from the project folder. It asks for the MySQL password and the client secret:
   ```
   SPOTIFY_CLIENT_ID=your_client_id DB_USER=ikeating python3 fetch_release_years.py --limit 7500
   ```

New Spotify apps can only look up one track at a time, so the script asks about one track per album and copies the
year to the rest of the album, most popular albums first. 7,500 lookups (~30 min) covers every album with a song at
popularity 60+. All 47,000 albums the feed can recommend take several hours. Stop it any time with Ctrl+C, and run it
again to continue. Remastered albums carry the reissue year, not the original one.

### Upgrading the db8 database

Keeps existing accounts and playlists. Run once each from the project folder (each asks for the MySQL password):

```
python3 -m pip install --user -r requirements.txt
mysql -u ikeating -p ikeating < migrate_vibes.sql      # vibe columns (skip if already done)
DB_USER=ikeating python3 migrate_v2.py                  # era, release year, language columns + languages
```

## Schema (15 relations)

| Relation | Source | What it holds |
|---|---|---|
| `tracks` | dataset + Spotify API | Spotify track ID, name, popularity, duration, audio features, release year, inferred language |
| `artists`, `albums`, `genres` | dataset | normalized out of the CSV |
| `track_artists` | dataset | many-to-many track ↔ artist (the CSV's `artists` column is `;`-separated) |
| `track_genres` | dataset | many-to-many track ↔ genre (the CSV repeats a track once per genre) |
| `users` | app / synthetic | username, salted password hash, exploration setting |
| `playlists` | app / synthetic | name, owner, genre, vibe prompt, its target point in audio-feature space, era |
| `playlist_tracks`, `playlist_collaborators` | app / synthetic | playlists' songs and shared editors |
| `playlist_genres` | app | per-playlist genre weights used in ranking, refined by swipes |
| `playlist_languages` | app | languages a playlist is limited to |
| `swipes` | app / synthetic | every left/right swipe |
| `friendships` | app / synthetic | pending/accepted friend requests |
| `listening_history` | synthetic | plays with `ms_played` (drives the "Wrapped" stats) |

The **Database** page (`/stats`) shows the live row count of every table.

## Demo script

Start the app with `SHOW_SQL=1` so each page shows the SQL it ran at the bottom.

1. **Real data** — open **Database** (`/stats`): row counts for all 15 relations.
2. **Insert** — **Sign up** a new account → `INSERT INTO users …` (password shown as `<password hash>`).
3. **Query** — **Log out**, **Log in** → `SELECT … FROM users WHERE username = %s` + hash check.
4. **Search** — Songs tab: search an artist and filter by genre. People tab: search users. My playlists tab: search inside your playlists.
5. **Vibe playlist** — create a playlist with the vibe "a 90s nighttime drive" and a language or two. The playlist
   page shows the sliders, era and closest songs, with the ranking query in the SQL panel.
6. **Tune it** — drag a slider (say, Energy up), click *Update matches*, and the closest songs change.
7. **Swipe** — *Start swiping*, swipe a few times, go back to the playlist, and the sliders have moved.
   Raise *Exploration* to see "exploring" cards.
8. **Update / delete** — rename the playlist or change its vibe, remove a song, delete the playlist.
9. Log in as a seeded user to show **Your Wrapped** and the **friends feed**.

All queries are parameterized (`%s` placeholders), so input like `' OR 1=1 --` is harmless.

## Still planned

1. **Spotify OAuth + playlist export** — "Log in with Spotify"; push a finished playlist to the user's Spotify account.
2. **In-app playback** — Spotify Web Playback SDK for auto-play on each new card (needs Spotify Premium).
3. **Collaborative playlists** — invite friends (`playlist_collaborators`), see who added each song.
4. **Real listening stats** — import recently-played from Spotify into `listening_history`.
