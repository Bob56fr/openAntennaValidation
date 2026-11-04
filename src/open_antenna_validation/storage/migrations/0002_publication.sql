-- Publication des campagnes sur le site WordPress : une ligne par campagne publiée.
-- La file d'attente est la base elle-même : sent_until_ms avance quand le site a accepté.
CREATE TABLE publication (
    campaign_id    INTEGER PRIMARY KEY REFERENCES campaigns (id) ON DELETE CASCADE,
    window_ms      INTEGER,                 -- fenêtre publiée (multiple de la fenêtre locale)
    sent_until_ms  INTEGER,                 -- fenêtres publiées acceptées avant cet instant
    meta_hash      TEXT NOT NULL DEFAULT '',
    rows_sent      INTEGER NOT NULL DEFAULT 0,
    bytes          INTEGER,                 -- taille du paquet sur le site
    state          TEXT NOT NULL DEFAULT 'pending',  -- pending, partial, complete, too_large, error
    message        TEXT NOT NULL DEFAULT '',
    last_ms        INTEGER
);
