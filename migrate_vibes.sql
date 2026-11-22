-- One-time upgrade for a database loaded before the vibe feature (keeps all existing data).
-- Run once:   mysql ikeating < migrate_vibes.sql
-- (A fresh  python3 load_data.py  already includes these changes.)

ALTER TABLE playlists
    ADD COLUMN vibe_prompt             VARCHAR(255),
    ADD COLUMN vibe_matches            VARCHAR(255),
    ADD COLUMN target_energy           FLOAT,
    ADD COLUMN target_valence          FLOAT,
    ADD COLUMN target_danceability     FLOAT,
    ADD COLUMN target_acousticness     FLOAT,
    ADD COLUMN target_instrumentalness FLOAT,
    ADD COLUMN target_tempo            FLOAT;

CREATE TABLE IF NOT EXISTS playlist_genres (
    playlist_id INT   NOT NULL,
    genre_id    INT   NOT NULL,
    weight      FLOAT NOT NULL,
    PRIMARY KEY (playlist_id, genre_id),
    FOREIGN KEY (playlist_id) REFERENCES playlists(playlist_id) ON DELETE CASCADE,
    FOREIGN KEY (genre_id)    REFERENCES genres(genre_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
