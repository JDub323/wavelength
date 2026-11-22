"""Wavelength web app (Flask + MySQL).

Run:  python app.py   then open db8.cse.nd.edu:5043
Connection settings live in config.py.
"""
import functools
import os
import random
import re

import pymysql
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import vibes
from config import DB

app = Flask(__name__)
# random per start unless set, so logins reset when the app restarts
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32).hex()
# SHOW_SQL=1 shows the SQL each page ran at the bottom of the page (for demos/debugging)
SHOW_SQL = os.environ.get("SHOW_SQL") == "1"

# artists / genres of a track, as one comma-separated string (used inside SELECT lists)
ARTISTS_OF = """(SELECT GROUP_CONCAT(a.name ORDER BY a.name SEPARATOR ', ')
                 FROM track_artists ta JOIN artists a ON a.artist_id = ta.artist_id
                 WHERE ta.track_id = t.track_id)"""
GENRES_OF = """(SELECT GROUP_CONCAT(gg.name ORDER BY gg.name SEPARATOR ', ')
                FROM track_genres tg JOIN genres gg ON gg.genre_id = tg.genre_id
                WHERE tg.track_id = t.track_id)"""


# ---------------------------------------------------------------- database helpers
def get_db():
    if "db" not in g:
        g.db = pymysql.connect(**DB, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _log(sql, params):
    """Remember each statement so the page can show the SQL it ran (handy for the demo)."""
    shown = ["<password hash>" if isinstance(p, str) and p.startswith(("scrypt:", "pbkdf2:")) else p
             for p in params]
    g.setdefault("queries", []).append([" ".join(sql.split())[:900], [str(p) for p in shown]])


def query(sql, params=(), one=False):
    _log(sql, params)
    with get_db().cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    """Run an INSERT/UPDATE/DELETE, commit, and return the new row's id (for inserts)."""
    _log(sql, params)
    db = get_db()
    with db.cursor() as cur:
        cur.execute(sql, params)
        new_id = cur.lastrowid
    db.commit()
    return new_id


@app.after_request
def carry_sql_across_redirect(response):
    # A POST usually redirects; stash its SQL so the next page can still display it.
    if SHOW_SQL and response.status_code in (301, 302, 303) and g.get("queries"):
        session["pending_sql"] = g.queries[-4:]  # keep the session cookie under the 4 KB limit
    return response


@app.context_processor
def inject_globals():
    return {
        "me": session.get("username"),
        "show_sql": SHOW_SQL,
        "sql_log": lambda: session.pop("pending_sql", []) + g.get("queries", []),
    }


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def my_playlists():
    return query("""
        SELECT p.playlist_id, p.name FROM playlists p
        WHERE p.owner_id = %s
           OR p.playlist_id IN (SELECT playlist_id FROM playlist_collaborators WHERE user_id = %s)
        ORDER BY p.name""", (session["user_id"], session["user_id"]))


def playlist_or_404(playlist_id):
    """Fetch a playlist the current user owns or collaborates on."""
    pl = query("""
        SELECT p.*, g.name AS genre, u.username AS owner
        FROM playlists p
        LEFT JOIN genres g ON g.genre_id = p.genre_id
        JOIN users u ON u.user_id = p.owner_id
        WHERE p.playlist_id = %s
          AND (p.owner_id = %s OR EXISTS (SELECT 1 FROM playlist_collaborators c
                                          WHERE c.playlist_id = p.playlist_id AND c.user_id = %s))""",
               (playlist_id, session["user_id"], session["user_id"]), one=True)
    if pl is None:
        abort(404)
    return pl


# ---------------------------------------------------------------- auth: signup (INSERT) + login (SELECT)
USERNAME_RE = re.compile(r"^[a-z0-9_]{3,30}$")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        display_name = request.form.get("display_name", "").strip()[:100] or username
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not USERNAME_RE.match(username):
            flash("Username must be 3-30 characters: letters, numbers, underscores.", "error")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        elif query("SELECT 1 FROM users WHERE username = %s", (username,), one=True):
            flash(f"Username '{username}' is already taken.", "error")
        else:
            user_id = execute(
                "INSERT INTO users (username, password_hash, display_name) VALUES (%s, %s, %s)",
                (username, generate_password_hash(password), display_name))
            session.clear()
            session["user_id"], session["username"] = user_id, username
            flash(f"Welcome, {username}!", "ok")
            return redirect(url_for("home"))
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = query("SELECT user_id, username, password_hash FROM users WHERE username = %s",
                     (username,), one=True)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"], session["username"] = user["user_id"], user["username"]
            flash(f"Welcome back, {user['username']}!", "ok")
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("home"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "ok")
    return redirect(url_for("login"))


# ---------------------------------------------------------------- home: playlists + "wrapped" feed
@app.route("/")
@login_required
def home():
    uid = session["user_id"]
    playlists = query("""
        SELECT p.playlist_id, p.name, g.name AS genre, COUNT(pt.track_id) AS n_tracks,
               (p.owner_id <> %s) AS shared
        FROM playlists p
        LEFT JOIN genres g ON g.genre_id = p.genre_id
        LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.playlist_id
        WHERE p.owner_id = %s
           OR p.playlist_id IN (SELECT playlist_id FROM playlist_collaborators WHERE user_id = %s)
        GROUP BY p.playlist_id
        ORDER BY p.created_at DESC""", (uid, uid, uid))

    wrapped = query("""
        SELECT COUNT(*) AS plays, COALESCE(SUM(ms_played), 0) DIV 60000 AS minutes
        FROM listening_history WHERE user_id = %s""", (uid,), one=True)
    top_genres = query("""
        SELECT g.name, SUM(lh.ms_played) DIV 60000 AS minutes
        FROM listening_history lh
        JOIN track_genres tg ON tg.track_id = lh.track_id
        JOIN genres g ON g.genre_id = tg.genre_id
        WHERE lh.user_id = %s
        GROUP BY g.genre_id ORDER BY minutes DESC LIMIT 5""", (uid,))
    top_artists = query("""
        SELECT a.name, COUNT(*) AS plays
        FROM listening_history lh
        JOIN track_artists ta ON ta.track_id = lh.track_id
        JOIN artists a ON a.artist_id = ta.artist_id
        WHERE lh.user_id = %s
        GROUP BY a.artist_id ORDER BY plays DESC LIMIT 5""", (uid,))

    # minutes per (friend, genre); the top genre per friend is picked below
    friend_rows = query("""
        SELECT u.user_id, u.username, u.display_name, g.name AS genre, SUM(lh.ms_played) AS ms
        FROM listening_history lh
        JOIN users u ON u.user_id = lh.user_id
        JOIN track_genres tg ON tg.track_id = lh.track_id
        JOIN genres g ON g.genre_id = tg.genre_id
        WHERE lh.user_id IN (
            SELECT friend_id FROM friendships WHERE user_id = %s AND status = 'accepted'
            UNION
            SELECT user_id FROM friendships WHERE friend_id = %s AND status = 'accepted')
        GROUP BY u.user_id, g.genre_id""", (uid, uid))
    friend_minutes = {r["user_id"]: r["minutes"] for r in query("""
        SELECT user_id, SUM(ms_played) DIV 60000 AS minutes FROM listening_history
        WHERE user_id IN (
            SELECT friend_id FROM friendships WHERE user_id = %s AND status = 'accepted'
            UNION
            SELECT user_id FROM friendships WHERE friend_id = %s AND status = 'accepted')
        GROUP BY user_id""", (uid, uid))}
    best = {}
    for r in friend_rows:
        if r["user_id"] not in best or r["ms"] > best[r["user_id"]]["ms"]:
            best[r["user_id"]] = r
    friend_feed = sorted(
        ({**r, "top_genre": r["genre"], "minutes": friend_minutes.get(uid_, 0)} for uid_, r in best.items()),
        key=lambda r: r["minutes"], reverse=True)[:12]

    friends = query("""
        SELECT u.user_id, u.username, u.display_name FROM users u
        WHERE u.user_id IN (
            SELECT friend_id FROM friendships WHERE user_id = %s AND status = 'accepted'
            UNION
            SELECT user_id FROM friendships WHERE friend_id = %s AND status = 'accepted')
        ORDER BY u.username""", (uid, uid))
    requests_in = query("""
        SELECT u.user_id, u.username, u.display_name FROM friendships f
        JOIN users u ON u.user_id = f.user_id
        WHERE f.friend_id = %s AND f.status = 'pending'""", (uid,))
    genres = query("SELECT genre_id, name FROM genres ORDER BY name")
    return render_template("home.html", playlists=playlists, wrapped=wrapped, top_genres=top_genres,
                           top_artists=top_artists, friends=friends, requests_in=requests_in,
                           genres=genres, languages=available_languages())


# ---------------------------------------------------------------- vibes: prompt -> target, ranking, refinement
_catalog_stats = None


def catalog_stats():
    """{feature: (mean, std)} over the catalog, used to z-score the distance. Cached per process."""
    global _catalog_stats
    if _catalog_stats is None:
        cols = ", ".join(f"AVG({f}) AS {f}_mean, STDDEV_POP({f}) AS {f}_std" for f in vibes.FEATURES)
        row = query(f"SELECT {cols} FROM tracks WHERE tempo > 0", one=True)
        _catalog_stats = {f: (float(row[f"{f}_mean"]), float(row[f"{f}_std"])) for f in vibes.FEATURES}
    return _catalog_stats


def set_target(playlist_id, target):
    cols = ", ".join(f"target_{f} = %s" for f in vibes.FEATURES)
    execute(f"UPDATE playlists SET {cols} WHERE playlist_id = %s",
            [target[f] for f in vibes.FEATURES] + [playlist_id])


def apply_vibe(playlist_id, prompt, genre_id=None):
    """Match a vibe prompt and store its target, era and genre weights on the playlist. Returns the match or None."""
    era = vibes.parse_era(prompt) or (None, None)
    execute("UPDATE playlists SET era_start = %s, era_end = %s WHERE playlist_id = %s", (*era, playlist_id))
    genre_ids = {r["name"]: r["genre_id"] for r in query("SELECT genre_id, name FROM genres")}
    match = vibes.match_vibe(prompt, genre_ids.keys())
    if match is None:
        return None
    summary = ", ".join(f"{name} {share:.0%}" for name, share in match["matches"])
    execute("UPDATE playlists SET vibe_prompt = %s, vibe_matches = %s WHERE playlist_id = %s",
            (prompt[:255], summary[:255], playlist_id))
    set_target(playlist_id, match["target"])

    weights = {genre_ids[name]: w for name, w in match["genres"].items() if name in genre_ids}
    if genre_id:
        weights[int(genre_id)] = max(weights.get(int(genre_id), 0), 1.0)
    execute("DELETE FROM playlist_genres WHERE playlist_id = %s", (playlist_id,))
    if weights:
        execute("INSERT INTO playlist_genres (playlist_id, genre_id, weight) VALUES "
                + ", ".join(["(%s, %s, %s)"] * len(weights)),
                [x for gid, w in weights.items() for x in (playlist_id, gid, w)])
    return match


def ensure_target(pl):
    """Playlists made without a vibe start from the average sound of their genre (or of the whole catalog)."""
    if pl["target_energy"] is not None:
        return pl
    if pl["genre_id"]:
        cols = ", ".join(f"AVG(t.{f}) AS {f}" for f in vibes.FEATURES)
        sets = ", ".join(f"p.target_{f} = a.{f}" for f in vibes.FEATURES)
        execute(f"""UPDATE playlists p
                    JOIN (SELECT {cols} FROM tracks t JOIN track_genres tg ON tg.track_id = t.track_id
                          WHERE tg.genre_id = %s AND t.tempo > 0) a
                    SET {sets} WHERE p.playlist_id = %s""", (pl["genre_id"], pl["playlist_id"]))
        execute("INSERT IGNORE INTO playlist_genres (playlist_id, genre_id, weight) VALUES (%s, %s, 1.0)",
                (pl["playlist_id"], pl["genre_id"]))
    else:
        set_target(pl["playlist_id"], {f: mean for f, (mean, _) in catalog_stats().items()})
    return playlist_or_404(pl["playlist_id"])


_languages = None


def available_languages():
    """Languages present in the catalog, most common first. Cached per process."""
    global _languages
    if _languages is None:
        _languages = [r["language"] for r in query(
            "SELECT language, COUNT(*) AS n FROM tracks WHERE language IS NOT NULL GROUP BY language ORDER BY n DESC")]
    return _languages


def playlist_languages(playlist_id):
    return [r["language"] for r in query(
        "SELECT language FROM playlist_languages WHERE playlist_id = %s ORDER BY language", (playlist_id,))]


def set_languages(playlist_id, languages):
    languages = [lang for lang in languages if lang in available_languages()]
    execute("DELETE FROM playlist_languages WHERE playlist_id = %s", (playlist_id,))
    if languages:
        execute("INSERT INTO playlist_languages (playlist_id, language) VALUES "
                + ", ".join(["(%s, %s)"] * len(languages)),
                [x for lang in languages for x in (playlist_id, lang)])


def years_loaded():
    """True once fetch_release_years.py has filled in at least some release years."""
    return query("SELECT 1 AS x FROM tracks WHERE release_year IS NOT NULL LIMIT 1", one=True) is not None


def playlist_era(pl):
    if pl["era_start"] is None and pl["era_end"] is None:
        return None
    return (pl["era_start"] or 1900, pl["era_end"] or 2100)


def recommend(pl, limit=10, pool=None):
    """Best-matching songs for a playlist. With `pool`, return one random song from the top `pool` instead."""
    target = {f: pl[f"target_{f}"] for f in vibes.FEATURES}
    weights = {r["genre_id"]: r["weight"] for r in query(
        "SELECT genre_id, weight FROM playlist_genres WHERE playlist_id = %s AND weight > 0",
        (pl["playlist_id"],))}
    era = playlist_era(pl) if years_loaded() else None
    rank, params = vibes.rank_sql(target, weights, catalog_stats(), session["user_id"],
                                  pl["playlist_id"], pool or limit * 3, era, playlist_languages(pl["playlist_id"]))
    rows = query(f"""
        SELECT r.score, t.track_id, t.name, t.popularity, t.duration_ms, t.energy, t.danceability,
               t.valence, t.acousticness, t.tempo, t.release_year, al.name AS album,
               {ARTISTS_OF} AS artists, {GENRES_OF} AS genres
        FROM ({rank}) AS r
        JOIN tracks t ON t.track_id = r.track_id
        LEFT JOIN albums al ON al.album_id = t.album_id
        ORDER BY {'RAND() LIMIT 1' if pool else 'r.score'}""", params)
    if pool:
        return rows[0] if rows else None
    seen, picks = set(), []  # the dataset has the same song under several IDs; show each once
    for r in rows:
        key = (r["name"].lower(), r["artists"])
        if key not in seen:
            seen.add(key)
            picks.append(r)
    return picks[:limit]


def add_tracks(playlist_id, track_ids):
    if track_ids:
        execute("INSERT IGNORE INTO playlist_tracks (playlist_id, track_id, added_by) VALUES "
                + ", ".join(["(%s, %s, %s)"] * len(track_ids)),
                [x for tid in track_ids for x in (playlist_id, tid, session["user_id"])])


# ---------------------------------------------------------------- playlists: INSERT / UPDATE / DELETE
@app.route("/playlists", methods=["POST"])
@login_required
def create_playlist():
    name = request.form.get("name", "").strip()[:100]
    genre_id = request.form.get("genre_id") or None
    vibe = request.form.get("vibe", "").strip()
    if not name:
        flash("Playlist needs a name.", "error")
        return redirect(url_for("home"))
    playlist_id = execute("INSERT INTO playlists (owner_id, name, genre_id) VALUES (%s, %s, %s)",
                          (session["user_id"], name, genre_id))
    set_languages(playlist_id, request.form.getlist("languages"))
    msg = f"Created playlist '{name}'."
    if vibe and not apply_vibe(playlist_id, vibe, genre_id):
        msg += " Couldn't read that vibe, so the feed will use the genre instead."
    flash(msg, "ok")
    return redirect(url_for("playlist", playlist_id=playlist_id))


def _form_year(name):
    value = request.form.get(name, "").strip()
    return min(2100, max(1900, int(value))) if value.isdigit() else None


@app.route("/playlists/<int:playlist_id>/target", methods=["POST"])
@login_required
def edit_target(playlist_id):
    """Save the vibe sliders, era and languages a user set by hand."""
    playlist_or_404(playlist_id)
    target = {}
    for f in vibes.FEATURES:
        lo, hi = vibes.BOUNDS[f]
        try:
            target[f] = min(hi, max(lo, float(request.form[f])))
        except (KeyError, ValueError):
            abort(400)
    set_target(playlist_id, target)
    start, end = _form_year("era_start"), _form_year("era_end")
    if start and end and start > end:
        start, end = end, start
    execute("UPDATE playlists SET era_start = %s, era_end = %s WHERE playlist_id = %s", (start, end, playlist_id))
    set_languages(playlist_id, request.form.getlist("languages"))
    flash("Vibe updated.", "ok")
    return redirect(url_for("playlist", playlist_id=playlist_id))


@app.route("/playlists/<int:playlist_id>")
@login_required
def playlist(playlist_id):
    pl = ensure_target(playlist_or_404(playlist_id))
    tracks = query(f"""
        SELECT t.track_id, t.name, al.name AS album, t.popularity, t.duration_ms, t.release_year,
               {ARTISTS_OF} AS artists, u.username AS added_by, pt.added_at
        FROM playlist_tracks pt
        JOIN tracks t ON t.track_id = pt.track_id
        LEFT JOIN albums al ON al.album_id = t.album_id
        LEFT JOIN users u ON u.user_id = pt.added_by
        WHERE pt.playlist_id = %s
        ORDER BY pt.added_at DESC""", (playlist_id,))
    collaborators = query("""
        SELECT u.username FROM playlist_collaborators c JOIN users u ON u.user_id = c.user_id
        WHERE c.playlist_id = %s""", (playlist_id,))
    top_genres = query("""
        SELECT g.name, pg.weight FROM playlist_genres pg JOIN genres g ON g.genre_id = pg.genre_id
        WHERE pg.playlist_id = %s AND pg.weight > 0 ORDER BY pg.weight DESC LIMIT 6""", (playlist_id,))
    genres = query("SELECT genre_id, name FROM genres ORDER BY name")
    return render_template("playlist.html", pl=pl, tracks=tracks, collaborators=collaborators, genres=genres,
                           top_genres=top_genres, suggestions=recommend(pl, limit=10),
                           languages=available_languages(), chosen_languages=playlist_languages(playlist_id),
                           years_loaded=years_loaded())


@app.route("/playlists/<int:playlist_id>/edit", methods=["POST"])
@login_required
def edit_playlist(playlist_id):
    pl = playlist_or_404(playlist_id)
    name = request.form.get("name", "").strip()[:100]
    genre_id = request.form.get("genre_id") or None
    vibe = request.form.get("vibe", "").strip()
    if name:
        execute("UPDATE playlists SET name = %s, genre_id = %s WHERE playlist_id = %s",
                (name, genre_id, playlist_id))
        if vibe and vibe != (pl["vibe_prompt"] or ""):
            apply_vibe(playlist_id, vibe, genre_id)  # new vibe: start the target over
        elif genre_id:
            execute("""INSERT INTO playlist_genres (playlist_id, genre_id, weight) VALUES (%s, %s, 1.0)
                       ON DUPLICATE KEY UPDATE weight = GREATEST(weight, 1.0)""", (playlist_id, genre_id))
        flash("Playlist updated.", "ok")
    return redirect(url_for("playlist", playlist_id=playlist_id))


@app.route("/playlists/<int:playlist_id>/fill", methods=["POST"])
@login_required
def fill_playlist(playlist_id):
    pl = ensure_target(playlist_or_404(playlist_id))
    picks = recommend(pl, limit=10)
    add_tracks(playlist_id, [t["track_id"] for t in picks])
    flash(f"Added {len(picks)} songs that match the vibe.", "ok")
    return redirect(url_for("playlist", playlist_id=playlist_id))


@app.route("/playlists/<int:playlist_id>/delete", methods=["POST"])
@login_required
def delete_playlist(playlist_id):
    pl = playlist_or_404(playlist_id)
    if pl["owner_id"] != session["user_id"]:
        abort(403)
    execute("DELETE FROM playlists WHERE playlist_id = %s", (playlist_id,))
    flash(f"Deleted playlist '{pl['name']}'.", "ok")
    return redirect(url_for("home"))


@app.route("/playlists/<int:playlist_id>/tracks", methods=["POST"])
@login_required
def add_track(playlist_id):
    playlist_or_404(playlist_id)
    try:
        execute("INSERT INTO playlist_tracks (playlist_id, track_id, added_by) VALUES (%s, %s, %s)",
                (playlist_id, request.form["track_id"], session["user_id"]))
        flash("Added to playlist.", "ok")
    except pymysql.err.IntegrityError:
        get_db().rollback()
        flash("That song is already in the playlist.", "error")
    return redirect(request.referrer or url_for("playlist", playlist_id=playlist_id))


@app.route("/playlists/<int:playlist_id>/tracks/<track_id>/delete", methods=["POST"])
@login_required
def remove_track(playlist_id, track_id):
    playlist_or_404(playlist_id)
    execute("DELETE FROM playlist_tracks WHERE playlist_id = %s AND track_id = %s", (playlist_id, track_id))
    flash("Removed from playlist.", "ok")
    return redirect(url_for("playlist", playlist_id=playlist_id))


# ---------------------------------------------------------------- swipe feed: vibe ranking + refinement
@app.route("/playlists/<int:playlist_id>/swipe", methods=["GET", "POST"])
@login_required
def swipe(playlist_id):
    pl = ensure_target(playlist_or_404(playlist_id))
    uid = session["user_id"]
    if request.method == "POST":
        track_id = request.form["track_id"]
        liked = request.form.get("liked") == "1"
        if request.form.get("exploration", "").isdigit():  # the slider's latest value rides along with each swipe
            execute("UPDATE users SET exploration = %s WHERE user_id = %s",
                    (min(100, int(request.form["exploration"])) / 100, uid))
        execute("INSERT INTO swipes (user_id, playlist_id, track_id, liked) VALUES (%s, %s, %s, %s)",
                (uid, playlist_id, track_id, liked))
        if liked:
            execute("INSERT IGNORE INTO playlist_tracks (playlist_id, track_id, added_by) VALUES (%s, %s, %s)",
                    (playlist_id, track_id, uid))
        for sql, params in vibes.refine_sql(playlist_id, track_id, liked):
            execute(sql, params)
        return redirect(url_for("swipe", playlist_id=playlist_id))

    # exploration: how often the next card comes from a much wider pool of near-matches
    exploration = float(query("SELECT exploration FROM users WHERE user_id = %s", (uid,), one=True)["exploration"])
    exploring = random.random() < exploration
    track = recommend(pl, pool=300 if exploring else 20)
    counts = query("""
        SELECT COALESCE(SUM(liked), 0) AS rights, COALESCE(SUM(NOT liked), 0) AS lefts FROM swipes
        WHERE user_id = %s AND playlist_id = %s""", (uid, playlist_id), one=True)
    return render_template("swipe.html", pl=pl, track=track, counts=counts,
                           exploring=exploring, exploration=exploration)


@app.route("/playlists/<int:playlist_id>/finish")
@login_required
def finish_swiping(playlist_id):
    pl = playlist_or_404(playlist_id)
    flash(f"Saved your picks to '{pl['name']}'.", "ok")
    return redirect(url_for("home"))


@app.route("/settings/exploration", methods=["POST"])
@login_required
def set_exploration():
    """Saved as soon as the slider moves (the swipe page posts here in the background), so it sticks between swipes."""
    try:
        value = min(1.0, max(0.0, float(request.form.get("exploration", 30)) / 100))
    except ValueError:
        abort(400)
    execute("UPDATE users SET exploration = %s WHERE user_id = %s", (value, session["user_id"]))
    if request.headers.get("X-Requested-With") == "fetch":
        return "", 204
    return redirect(request.referrer or url_for("home"))


# ---------------------------------------------------------------- search (SELECT ... list results)
@app.route("/search")
@login_required
def search():
    kind = request.args.get("type", "tracks")
    q = request.args.get("q", "").strip()
    genre = request.args.get("genre", "")
    like = f"%{q}%"
    results = []
    uid = session["user_id"]

    if kind == "tracks" and (q or genre):
        results = query(f"""
            SELECT t.track_id, t.name, al.name AS album, t.popularity, t.duration_ms,
                   {ARTISTS_OF} AS artists, {GENRES_OF} AS genres
            FROM tracks t
            LEFT JOIN albums al ON al.album_id = t.album_id
            WHERE (t.name LIKE %s OR t.track_id IN (
                      SELECT ta2.track_id FROM track_artists ta2
                      JOIN artists a2 ON a2.artist_id = ta2.artist_id WHERE a2.name LIKE %s))
              AND (%s = '' OR t.track_id IN (
                      SELECT tg2.track_id FROM track_genres tg2
                      JOIN genres g2 ON g2.genre_id = tg2.genre_id WHERE g2.name = %s))
            ORDER BY t.popularity DESC LIMIT 50""", (like, like, genre, genre))
    elif kind == "users" and q:
        results = query("""
            SELECT u.user_id, u.username, u.display_name, u.created_at,
                   (SELECT COUNT(*) FROM playlists p WHERE p.owner_id = u.user_id) AS n_playlists,
                   (SELECT f.status FROM friendships f
                    WHERE (f.user_id = %s AND f.friend_id = u.user_id)
                       OR (f.friend_id = %s AND f.user_id = u.user_id) LIMIT 1) AS friend_status
            FROM users u
            WHERE (u.username LIKE %s OR u.display_name LIKE %s) AND u.user_id <> %s
            ORDER BY u.username LIMIT 50""", (uid, uid, like, like, uid))
    elif kind == "mine" and q:
        results = query(f"""
            SELECT p.playlist_id, p.name AS playlist, t.track_id, t.name, {ARTISTS_OF} AS artists
            FROM playlists p
            JOIN playlist_tracks pt ON pt.playlist_id = p.playlist_id
            JOIN tracks t ON t.track_id = pt.track_id
            WHERE (p.owner_id = %s OR p.playlist_id IN
                     (SELECT playlist_id FROM playlist_collaborators WHERE user_id = %s))
              AND (p.name LIKE %s OR t.name LIKE %s OR EXISTS (
                      SELECT 1 FROM track_artists ta2 JOIN artists a2 ON a2.artist_id = ta2.artist_id
                      WHERE ta2.track_id = t.track_id AND a2.name LIKE %s))
            ORDER BY p.name, t.name LIMIT 100""", (uid, uid, like, like, like))

    genres = query("SELECT name FROM genres ORDER BY name")
    return render_template("search.html", kind=kind, q=q, genre=genre, results=results,
                           genres=genres, playlists=my_playlists() if kind == "tracks" else [])


# ---------------------------------------------------------------- friends
@app.route("/friends/<int:user_id>/add", methods=["POST"])
@login_required
def add_friend(user_id):
    if user_id == session["user_id"]:
        abort(400)
    try:
        execute("INSERT INTO friendships (user_id, friend_id, status) VALUES (%s, %s, 'pending')",
                (session["user_id"], user_id))
        flash("Friend request sent.", "ok")
    except pymysql.err.IntegrityError:
        get_db().rollback()
        flash("You already sent a request to this user.", "error")
    return redirect(request.referrer or url_for("home"))


@app.route("/friends/<int:user_id>/accept", methods=["POST"])
@login_required
def accept_friend(user_id):
    execute("UPDATE friendships SET status = 'accepted' WHERE user_id = %s AND friend_id = %s",
            (user_id, session["user_id"]))
    flash("Friend request accepted.", "ok")
    return redirect(url_for("home"))


# ---------------------------------------------------------------- database stats (proves "real data")
@app.route("/stats")
def stats():
    tables = [r["name"] for r in query("""
        SELECT table_name AS name FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name""")]
    counts = [(t, query(f"SELECT COUNT(*) AS n FROM `{t}`", one=True)["n"]) for t in tables]
    top_genres = query("""
        SELECT g.name, COUNT(*) AS n FROM track_genres tg JOIN genres g ON g.genre_id = tg.genre_id
        GROUP BY g.genre_id ORDER BY n DESC LIMIT 10""")
    return render_template("stats.html", counts=counts, total=sum(n for _, n in counts), top_genres=top_genres)


@app.template_filter("mmss")
def mmss(ms):
    if not ms:
        return ""
    s = int(ms) // 1000
    return f"{s // 60}:{s % 60:02d}"


if __name__ == "__main__":
    # On the course server: HOST=0.0.0.0 PORT=<your port> python3 app.py
    # app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")),
    #         debug=os.environ.get("FLASK_DEBUG", "1" if os.environ.get("HOST") is None else "0") == "1")
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "5043")),
            debug=os.environ.get("FLASK_DEBUG", "1" if os.environ.get("HOST") is None else "0") == "1")
