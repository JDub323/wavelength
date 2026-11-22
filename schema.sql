-- Song Tinder schema (MySQL 5.7+ / 8.x). 13 relations.
-- Spotify track IDs are case-sensitive, so every track_id column uses ascii_bin.

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS listening_history;
DROP TABLE IF EXISTS swipes;
DROP TABLE IF EXISTS friendships;
DROP TABLE IF EXISTS playlist_collaborators;
DROP TABLE IF EXISTS playlist_tracks;
DROP TABLE IF EXISTS playlists;
DROP TABLE IF EXISTS track_genres;
DROP TABLE IF EXISTS track_artists;
DROP TABLE IF EXISTS tracks;
DROP TABLE IF EXISTS albums;
DROP TABLE IF EXISTS artists;
DROP TABLE IF EXISTS genres;
DROP TABLE IF EXISTS users;
SET FOREIGN_KEY_CHECKS = 1;

-- ---------- App users ----------
CREATE TABLE users (
    user_id       INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(30)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,          -- salted hash, never the raw password
    display_name  VARCHAR(100),
    exploration   DECIMAL(3,2) NOT NULL DEFAULT 0.30 CHECK (exploration BETWEEN 0 AND 1),
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- Music catalog (Kaggle: maharshipandya/-spotify-tracks-dataset) ----------
CREATE TABLE genres (
    genre_id INT PRIMARY KEY,
    name     VARCHAR(50) NOT NULL UNIQUE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE artists (
    artist_id INT PRIMARY KEY,
    name      VARCHAR(512) NOT NULL,
    INDEX idx_artists_name (name(100))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE albums (
    album_id  INT PRIMARY KEY,
    name      VARCHAR(512) NOT NULL,
    artist_id INT,                                  -- primary artist
    FOREIGN KEY (artist_id) REFERENCES artists(artist_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE tracks (
    track_id         CHAR(22) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,  -- Spotify track ID
    name             VARCHAR(512) NOT NULL,
    album_id         INT,
    popularity       TINYINT UNSIGNED,
    duration_ms      INT,
    explicit         BOOLEAN,
    danceability     FLOAT,
    energy           FLOAT,
    musical_key      TINYINT,
    loudness         FLOAT,
    mode             TINYINT,
    speechiness      FLOAT,
    acousticness     FLOAT,
    instrumentalness FLOAT,
    liveness         FLOAT,
    valence          FLOAT,
    tempo            FLOAT,
    time_signature   TINYINT,
    FOREIGN KEY (album_id) REFERENCES albums(album_id),
    INDEX idx_tracks_name (name(100)),
    INDEX idx_tracks_popularity (popularity)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE track_artists (
    track_id  CHAR(22) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    artist_id INT NOT NULL,
    PRIMARY KEY (track_id, artist_id),
    INDEX idx_track_artists_a (artist_id),
    FOREIGN KEY (track_id)  REFERENCES tracks(track_id)   ON DELETE CASCADE,
    FOREIGN KEY (artist_id) REFERENCES artists(artist_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- the dataset lists a track once per genre it appears in, so genres are many-to-many
CREATE TABLE track_genres (
    track_id CHAR(22) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    genre_id INT NOT NULL,
    PRIMARY KEY (track_id, genre_id),
    INDEX idx_track_genres_g (genre_id),
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
    FOREIGN KEY (genre_id) REFERENCES genres(genre_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- User-generated data ----------
CREATE TABLE playlists (
    playlist_id INT AUTO_INCREMENT PRIMARY KEY,
    owner_id    INT NOT NULL,
    name        VARCHAR(100) NOT NULL,
    genre_id    INT,                                -- the genre the swipe feed draws from
    description VARCHAR(255),
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (owner_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (genre_id) REFERENCES genres(genre_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE playlist_tracks (
    playlist_id INT NOT NULL,
    track_id    CHAR(22) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    added_by    INT,
    added_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (playlist_id, track_id),
    FOREIGN KEY (playlist_id) REFERENCES playlists(playlist_id) ON DELETE CASCADE,
    FOREIGN KEY (track_id)    REFERENCES tracks(track_id),
    FOREIGN KEY (added_by)    REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE playlist_collaborators (
    playlist_id INT NOT NULL,
    user_id     INT NOT NULL,
    PRIMARY KEY (playlist_id, user_id),
    FOREIGN KEY (playlist_id) REFERENCES playlists(playlist_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)     REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE friendships (
    user_id    INT NOT NULL,                        -- requester
    friend_id  INT NOT NULL,                        -- recipient
    status     ENUM('pending', 'accepted') NOT NULL DEFAULT 'pending',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, friend_id),
    INDEX idx_friendships_rev (friend_id),   -- (user_id <> friend_id is enforced by the app: MySQL
                                             --  forbids CHECKs on columns with ON DELETE CASCADE)
    FOREIGN KEY (user_id)   REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (friend_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE swipes (
    swipe_id    INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NOT NULL,
    playlist_id INT,
    track_id    CHAR(22) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    liked       BOOLEAN NOT NULL,                   -- TRUE = swiped right
    swiped_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_swipes_user_pl (user_id, playlist_id),
    FOREIGN KEY (user_id)     REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (playlist_id) REFERENCES playlists(playlist_id) ON DELETE CASCADE,
    FOREIGN KEY (track_id)    REFERENCES tracks(track_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE listening_history (
    listen_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id   INT NOT NULL,
    track_id  CHAR(22) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    played_at DATETIME NOT NULL,
    ms_played INT NOT NULL,
    INDEX idx_history_user (user_id, played_at),
    FOREIGN KEY (user_id)  REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (track_id) REFERENCES tracks(track_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
