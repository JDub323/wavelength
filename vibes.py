"""Vibe matching: turn a free-text prompt ("a nighttime drive") into a target point in
Spotify audio-feature space, rank tracks by distance to it in SQL, and nudge the target
as the user swipes.

How a prompt becomes a target:
  1. Each ANCHOR below is a hand-written vibe with target audio features and genre weights.
  2. The prompt and every anchor description are embedded with a small sentence-embedding
     model (fastembed, BAAI/bge-small-en-v1.5, runs on CPU).
  3. The closest anchors by cosine similarity are blended (softmax weights) into one target.
If fastembed isn't installed, a keyword-overlap fallback picks the anchors instead.
"""
import math
import os
import re

# feature -> weight in the distance (bigger = matters more). Distances are computed on
# z-scores, so tempo (bpm) and the 0-1 features are on the same scale.
FEATURES = {
    "energy": 1.0,
    "valence": 1.0,
    "danceability": 0.7,
    "acousticness": 0.7,
    "instrumentalness": 0.5,
    "tempo": 0.4,
}
BOUNDS = {f: (0.0, 1.0) for f in FEATURES} | {"tempo": (40.0, 220.0)}

GENRE_BONUS = 1.5       # score bonus for a genre with weight 1.0
POPULARITY_BONUS = 1.2  # score bonus for popularity 100
MIN_POPULARITY = 15     # skip obscure duplicates/uploads

LIKE_STEP = 0.20        # right swipe: move target 20% of the way toward the song
DISLIKE_STEP = 0.05     # left swipe: move target 5% away from the song
GENRE_LIKE_STEP = 0.10
GENRE_DISLIKE_STEP = 0.05

TOP_ANCHORS = 3
TEMPERATURE = 0.03      # lower = trust the single best anchor more

# (name, description used for embedding, target features, genre weights)
ANCHORS = [
    ("late night drive", "late night drive, cruising at night, driving after dark, city lights, midnight highway",
     dict(energy=.55, valence=.35, danceability=.60, acousticness=.15, instrumentalness=.20, tempo=110),
     {"synth-pop": .8, "electronic": .5, "trip-hop": .5, "indie": .4, "chill": .4, "r-n-b": .3}),
    ("road trip", "road trip with friends, sunny highway, singing along in the car, summer drive",
     dict(energy=.75, valence=.70, danceability=.60, acousticness=.20, instrumentalness=.02, tempo=120),
     {"rock": .6, "pop": .6, "indie-pop": .5, "country": .4, "alt-rock": .4}),
    ("workout", "workout, gym, lifting weights, running, high intensity training, pump up",
     dict(energy=.90, valence=.55, danceability=.70, acousticness=.05, instrumentalness=.05, tempo=130),
     {"edm": .7, "hip-hop": .7, "dance": .5, "hard-rock": .4, "electro": .4}),
    ("party", "party, dance floor, club night, pregame, celebration, going out",
     dict(energy=.85, valence=.75, danceability=.80, acousticness=.08, instrumentalness=.05, tempo=124),
     {"party": .8, "dance": .7, "edm": .6, "pop": .5, "house": .5, "reggaeton": .4}),
    ("studying", "studying, focus, concentration, homework, reading, deep work",
     dict(energy=.30, valence=.35, danceability=.45, acousticness=.65, instrumentalness=.70, tempo=100),
     {"study": .9, "ambient": .6, "piano": .6, "classical": .5, "chill": .4}),
    ("sleep", "falling asleep, bedtime, calm, relaxing, lullaby, quiet night",
     dict(energy=.12, valence=.20, danceability=.30, acousticness=.85, instrumentalness=.80, tempo=80),
     {"sleep": .9, "ambient": .7, "new-age": .6, "piano": .5, "classical": .4}),
    ("heartbreak", "heartbreak, breakup, crying, sad, lonely, missing someone",
     dict(energy=.35, valence=.18, danceability=.45, acousticness=.55, instrumentalness=.02, tempo=105),
     {"sad": .9, "singer-songwriter": .5, "indie": .5, "acoustic": .4, "emo": .3}),
    ("happy", "happy, sunny day, good mood, feeling great, upbeat, cheerful",
     dict(energy=.72, valence=.85, danceability=.70, acousticness=.25, instrumentalness=.02, tempo=120),
     {"happy": .9, "pop": .6, "indie-pop": .5, "power-pop": .4, "funk": .3}),
    ("chill hangout", "chill, relaxing, laid back, lazy sunday, hanging out, lo-fi",
     dict(energy=.40, valence=.50, danceability=.62, acousticness=.50, instrumentalness=.30, tempo=95),
     {"chill": .9, "r-n-b": .4, "trip-hop": .4, "jazz": .3, "indie": .3}),
    ("romantic", "romantic dinner, date night, love songs, slow dance, candlelight",
     dict(energy=.40, valence=.50, danceability=.55, acousticness=.50, instrumentalness=.03, tempo=95),
     {"romance": .8, "soul": .6, "r-n-b": .6, "jazz": .5}),
    ("coffee shop", "coffee shop, cafe, cozy morning, acoustic guitar, afternoon reading",
     dict(energy=.33, valence=.45, danceability=.52, acousticness=.78, instrumentalness=.10, tempo=105),
     {"acoustic": .8, "folk": .6, "singer-songwriter": .6, "jazz": .4, "indie": .4}),
    ("rainy day", "rainy day, gloomy, cloudy, melancholy, staying inside watching the rain",
     dict(energy=.30, valence=.28, danceability=.45, acousticness=.60, instrumentalness=.25, tempo=100),
     {"indie": .5, "piano": .5, "sad": .4, "ambient": .4, "trip-hop": .4, "acoustic": .4}),
    ("rage", "angry, rage, aggressive, mosh pit, heavy, intense, screaming",
     dict(energy=.95, valence=.25, danceability=.45, acousticness=.02, instrumentalness=.10, tempo=140),
     {"metal": .8, "heavy-metal": .6, "metalcore": .6, "hardcore": .5, "punk": .4, "death-metal": .3}),
    ("summer beach", "summer, beach, pool party, vacation, tropical, sunshine",
     dict(energy=.75, valence=.80, danceability=.75, acousticness=.20, instrumentalness=.03, tempo=115),
     {"reggae": .5, "latin": .5, "afrobeat": .5, "pop": .5, "reggaeton": .4, "dancehall": .4}),
    ("throwback", "throwback, oldies, classic hits, retro, nostalgia, the 70s and 80s",
     dict(energy=.65, valence=.70, danceability=.65, acousticness=.25, instrumentalness=.02, tempo=120),
     {"disco": .6, "rock-n-roll": .6, "funk": .5, "soul": .5, "synth-pop": .3, "rockabilly": .3}),
    ("jazz lounge", "jazz bar, lounge, smoky, sophisticated, cocktails, saxophone",
     dict(energy=.35, valence=.50, danceability=.55, acousticness=.65, instrumentalness=.40, tempo=110),
     {"jazz": .9, "blues": .5, "soul": .4}),
    ("country roads", "country back roads, pickup truck, small town, bonfire, farm",
     dict(energy=.65, valence=.65, danceability=.58, acousticness=.35, instrumentalness=.01, tempo=120),
     {"country": .9, "honky-tonk": .5, "bluegrass": .4, "folk": .3}),
    ("hype", "hype, rap, hip hop, confident, swagger, heavy bass, trap",
     dict(energy=.75, valence=.50, danceability=.80, acousticness=.10, instrumentalness=.02, tempo=120),
     {"hip-hop": .9, "r-n-b": .3, "dancehall": .2}),
    ("meditation", "meditation, yoga, mindfulness, breathing, spa, peaceful",
     dict(energy=.15, valence=.25, danceability=.30, acousticness=.80, instrumentalness=.85, tempo=75),
     {"new-age": .8, "ambient": .8, "sleep": .5, "classical": .3}),
    ("epic", "epic, cinematic, movie soundtrack, adventure, triumphant, orchestral",
     dict(energy=.60, valence=.30, danceability=.30, acousticness=.40, instrumentalness=.80, tempo=120),
     {"classical": .5, "pop-film": .4, "show-tunes": .3, "ambient": .3, "opera": .2}),
    ("rave", "rave, techno, warehouse, dancing all night, electronic beats, festival",
     dict(energy=.90, valence=.40, danceability=.72, acousticness=.02, instrumentalness=.70, tempo=128),
     {"techno": .8, "trance": .6, "house": .6, "minimal-techno": .5, "drum-and-bass": .4, "hardstyle": .3}),
    ("indie rock", "indie rock, alternative, garage band, college radio, electric guitar",
     dict(energy=.70, valence=.45, danceability=.50, acousticness=.15, instrumentalness=.10, tempo=125),
     {"indie": .7, "alt-rock": .7, "alternative": .6, "garage": .4, "grunge": .3}),
    ("getting ready", "getting ready, cleaning the house, morning energy, motivation, getting things done",
     dict(energy=.78, valence=.75, danceability=.72, acousticness=.15, instrumentalness=.02, tempo=118),
     {"pop": .7, "dance": .5, "funk": .4, "disco": .4, "indie-pop": .4}),
    ("coding", "coding, gaming, programming, night owl, hacking, cyberpunk",
     dict(energy=.65, valence=.35, danceability=.55, acousticness=.05, instrumentalness=.70, tempo=125),
     {"electronic": .7, "idm": .5, "synth-pop": .4, "edm": .4, "drum-and-bass": .4, "industrial": .3}),
    ("sunday morning", "sunday morning, brunch, slow morning, sunshine through the window, soft",
     dict(energy=.38, valence=.62, danceability=.58, acousticness=.65, instrumentalness=.05, tempo=105),
     {"soul": .5, "acoustic": .5, "jazz": .4, "folk": .4, "singer-songwriter": .4, "indie-pop": .3}),
    ("dark", "dark, moody, mysterious, haunting, gothic, eerie",
     dict(energy=.55, valence=.15, danceability=.45, acousticness=.15, instrumentalness=.40, tempo=115),
     {"goth": .7, "industrial": .5, "trip-hop": .5, "alternative": .3, "black-metal": .2}),
    ("latin dance", "salsa, latin dance, bachata, reggaeton, fiesta",
     dict(energy=.78, valence=.75, danceability=.80, acousticness=.20, instrumentalness=.01, tempo=110),
     {"latin": .7, "salsa": .7, "reggaeton": .6, "latino": .6, "samba": .3}),
    ("golden hour", "sunset, golden hour, warm evening, winding down, dusk",
     dict(energy=.50, valence=.55, danceability=.58, acousticness=.40, instrumentalness=.15, tempo=105),
     {"chill": .6, "indie-pop": .5, "soul": .3, "acoustic": .3, "synth-pop": .3}),
]


# ---------------------------------------------------------------- prompt -> target
_model = None
_anchor_vecs = None


def _embed(texts):
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding("BAAI/bge-small-en-v1.5",
                               cache_dir=os.path.expanduser("~/.cache/fastembed"))
    return [list(v) for v in _model.embed(texts)]


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def _embedding_scores(prompt):
    global _anchor_vecs
    if _anchor_vecs is None:
        _anchor_vecs = _embed([f"{name}: {desc}" for name, desc, _, _ in ANCHORS])
    p = _embed([prompt])[0]
    return [_cosine(p, v) for v in _anchor_vecs]


def _words(text):
    return {w.rstrip("s") for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2}


def _keyword_scores(prompt):
    pw = _words(prompt)
    return [len(pw & _words(f"{name} {desc}")) for name, desc, _, _ in ANCHORS]


def match_vibe(prompt, genre_names=()):
    """Return {"target": {feature: value}, "genres": {genre: weight}, "matches": [(anchor, share)], "method": str}."""
    try:
        scores, method, temp = _embedding_scores(prompt), "embedding", TEMPERATURE
    except ImportError:
        scores, method, temp = _keyword_scores(prompt), "keywords", 0.5
        if max(scores) == 0:
            return None

    top = sorted(range(len(ANCHORS)), key=lambda i: scores[i], reverse=True)[:TOP_ANCHORS]
    best = scores[top[0]]
    raw = [math.exp((scores[i] - best) / temp) for i in top]
    shares = [r / sum(raw) for r in raw]

    target = {f: sum(s * ANCHORS[i][2][f] for i, s in zip(top, shares)) for f in FEATURES}
    genres = {}
    for i, s in zip(top, shares):
        for g, w in ANCHORS[i][3].items():
            genres[g] = genres.get(g, 0) + s * w
    # a genre named outright in the prompt ("k-pop workout") counts fully
    text = " " + re.sub(r"[^a-z0-9-]+", " ", prompt.lower()) + " "
    for g in genre_names:
        if f" {g} " in text or f" {g.replace('-', ' ')} " in text:
            genres[g] = max(genres.get(g, 0), 1.0)
    return {
        "target": target,
        "genres": {g: round(w, 3) for g, w in genres.items() if w >= 0.05},
        "matches": [(ANCHORS[i][0], round(s, 2)) for i, s in zip(top, shares) if s >= 0.05],
        "method": method,
    }


# ---------------------------------------------------------------- era: "90s", "early 2000s", "classic", "2015"
_DECADE_WORDS = {"fifties": 1950, "sixties": 1960, "seventies": 1970, "eighties": 1980, "nineties": 1990}
_ERA_WORDS = [  # checked in order; first hit wins
    (r"\by2k\b|\bnoughties\b", (1998, 2004)),
    (r"\boldies\b", (1950, 1979)),
    (r"\bclassic(?!al)\b|\bclassics\b", (1960, 1995)),
    (r"\bvintage\b|\bretro\b", (1960, 1989)),
    (r"\bold school\b", (1970, 1999)),
    (r"\bthrowbacks?\b", (1980, 2012)),
    (r"\brecent\b|\blatest\b|\bmodern\b|\bcurrent\b|\bnew (?:music|songs|releases|hits)\b", (2019, 2100)),
]


def parse_era(text):
    """Release-year range implied by a prompt, or None. '90s' -> (1990, 1999), 'early 2000s' -> (2000, 2003)."""
    t = text.lower()
    m = re.search(r"\b((?:19|20)\d{2})\s*(?:-|–|to)\s*((?:19|20)\d{2})\b", t)
    if m:
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        return a, b
    decade = None
    m = re.search(r"\b((?:19|20)\d)0\s*'?s\b", t) or re.search(r"(?:^|[\s(])'?(\d)0\s*'?s\b", t)
    if m:
        d = m.group(1)
        decade = int(d) * 10 if len(d) == 3 else (1900 if int(d) >= 3 else 2000) + int(d) * 10
    else:
        for word, start in _DECADE_WORDS.items():
            if re.search(rf"\b{word}\b", t):
                decade = start
    if decade is not None:
        if re.search(r"\bearly\b", t):
            return decade, decade + 3
        if re.search(r"\bmid\b", t):
            return decade + 3, decade + 6
        if re.search(r"\blate\b", t):
            return decade + 6, decade + 9
        return decade, decade + 9
    m = re.search(r"\b(19[5-9]\d|20[0-2]\d)\b", t)
    if m:
        return int(m.group(1)), int(m.group(1))
    for pattern, span in _ERA_WORDS:
        if re.search(pattern, t):
            return span
    return None


# ---------------------------------------------------------------- language (inferred: the dataset has none)
_GENRE_LANGUAGE = {
    "k-pop": "Korean", "j-pop": "Japanese", "j-rock": "Japanese", "j-idol": "Japanese", "j-dance": "Japanese",
    "anime": "Japanese", "mandopop": "Chinese", "cantopop": "Chinese",
    "latin": "Spanish", "latino": "Spanish", "reggaeton": "Spanish", "salsa": "Spanish", "spanish": "Spanish",
    "tango": "Spanish", "brazil": "Portuguese", "mpb": "Portuguese", "sertanejo": "Portuguese",
    "pagode": "Portuguese", "samba": "Portuguese", "forro": "Portuguese", "french": "French",
    "german": "German", "swedish": "Swedish", "turkish": "Turkish", "iranian": "Persian", "indian": "Hindi",
    "malay": "Malay",
}
_SCRIPTS = [  # (regex over the title, language); checked before genre because the title is direct evidence
    (r"[가-힣ᄀ-ᇿ]", "Korean"),
    (r"[぀-ヿ]", "Japanese"),
    (r"[一-鿿]", "han"),       # Han characters: Japanese if a j- genre, otherwise Chinese
    (r"[Ѐ-ӿ]", "Russian"),
    (r"[؀-ۿ]", "arabic"),    # Arabic script: Persian if iranian, otherwise Arabic
    (r"[ऀ-෿]", "Hindi"),     # Devanagari and other Indic scripts
]


def infer_language(title, genres, instrumentalness):
    """Best guess at a track's language from its title's script, its genres and how instrumental it is."""
    genres = set(genres)
    for pattern, lang in _SCRIPTS:
        if re.search(pattern, title or ""):
            if lang == "han":
                return "Japanese" if genres & {"j-pop", "j-rock", "j-idol", "j-dance", "anime"} else "Chinese"
            if lang == "arabic":
                return "Persian" if "iranian" in genres else "Arabic"
            return lang
    for g in sorted(genres):
        if g in _GENRE_LANGUAGE:
            return _GENRE_LANGUAGE[g]
    if (instrumentalness or 0) >= 0.6:
        return "Instrumental"
    return "English"


# ---------------------------------------------------------------- SQL: ranking + refinement
def rank_sql(target, genre_weights, stats, user_id, playlist_id, limit, era=None, languages=()):
    """SQL + params ranking tracks by closeness to `target` (lower score = better match).

    target:        {feature: value}
    genre_weights: {genre_id: weight}
    stats:         {feature: (mean, std)} over the catalog, for z-scoring
    era:           (first_year, last_year) to keep, or None
    languages:     language names to keep (empty = any)
    Excludes songs (by title) already in the playlist or already swiped for it.
    """
    # weighted squared distance on z-scores: sum of w * ((x - target) / std)^2
    dist_terms, params = [], []
    for f, w in FEATURES.items():
        dist_terms.append(f"(t.{f} - %s) * (t.{f} - %s) * %s")
        params += [target[f], target[f], w / stats[f][1] ** 2]
    score = " + ".join(dist_terms) + " - %s * t.popularity / 100"
    params.append(POPULARITY_BONUS)

    # genre bonus: the best weight among a track's genres, from one grouped join over just those genres
    genre_join = ""
    if genre_weights:
        score += " - %s * COALESCE(gm.weight, 0)"
        params.append(GENRE_BONUS)
        cases = " ".join("WHEN %s THEN %s" for _ in genre_weights)
        marks = ", ".join("%s" for _ in genre_weights)
        genre_join = f"""
        LEFT JOIN (SELECT tg.track_id, MAX(CASE tg.genre_id {cases} ELSE 0 END) AS weight
                   FROM track_genres tg WHERE tg.genre_id IN ({marks})
                   GROUP BY tg.track_id) gm ON gm.track_id = t.track_id"""
        for gid, w in genre_weights.items():
            params += [gid, w]
        params += list(genre_weights)

    filters = ""
    params.append(MIN_POPULARITY)
    if era:
        filters += " AND t.release_year BETWEEN %s AND %s"
        params += [era[0], era[1]]
    if languages:
        filters += f" AND t.language IN ({', '.join('%s' for _ in languages)})"
        params += list(languages)

    sql = f"""
        SELECT t.track_id, ({score}) AS score
        FROM tracks t{genre_join}
        WHERE t.tempo > 0 AND t.speechiness < 0.66 AND t.popularity >= %s{filters}
          AND t.name NOT IN (
              SELECT t2.name FROM playlist_tracks pt JOIN tracks t2 ON t2.track_id = pt.track_id
              WHERE pt.playlist_id = %s
              UNION
              SELECT t3.name FROM swipes s JOIN tracks t3 ON t3.track_id = s.track_id
              WHERE s.user_id = %s AND s.playlist_id = %s)
        ORDER BY score
        LIMIT %s"""
    params += [playlist_id, user_id, playlist_id, limit]
    return sql, params


def refine_sql(playlist_id, track_id, liked):
    """Statements (sql, params) that move the playlist's target toward (like) or away from (dislike) a song."""
    step = LIKE_STEP if liked else -DISLIKE_STEP
    sets, params = [], []
    for f in FEATURES:
        lo, hi = BOUNDS[f]
        sets.append(f"p.target_{f} = LEAST(%s, GREATEST(%s, p.target_{f} + %s * (t.{f} - p.target_{f})))")
        params += [hi, lo, step]
    stmts = [(f"UPDATE playlists p JOIN tracks t ON t.track_id = %s SET {', '.join(sets)} "
              f"WHERE p.playlist_id = %s AND p.target_energy IS NOT NULL",
              [track_id] + params + [playlist_id])]
    if liked:
        stmts.append(("""INSERT INTO playlist_genres (playlist_id, genre_id, weight)
                         SELECT %s, tg.genre_id, %s FROM track_genres tg WHERE tg.track_id = %s
                         ON DUPLICATE KEY UPDATE weight = LEAST(1.5, weight + %s)""",
                      [playlist_id, GENRE_LIKE_STEP, track_id, GENRE_LIKE_STEP]))
    else:
        stmts.append(("""UPDATE playlist_genres pg JOIN track_genres tg ON tg.genre_id = pg.genre_id
                         SET pg.weight = GREATEST(0, pg.weight - %s)
                         WHERE pg.playlist_id = %s AND tg.track_id = %s""",
                      [GENRE_DISLIKE_STEP, playlist_id, track_id]))
    return stmts
