-- Schéma complet (cahier des charges, section 14), y compris les tables des phases ultérieures
-- (qualification, campagnes, modes numériques), pour éviter toute refonte.
-- Horodatages : entiers en millisecondes UTC. Mesures brutes jamais modifiées.

CREATE TABLE app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE events (
    id          INTEGER PRIMARY KEY,
    ts_ms       INTEGER NOT NULL,
    type        TEXT NOT NULL,            -- antenna_switch, gain_change, hardware_change, alert, error...
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX events_ts ON events (ts_ms);
CREATE INDEX events_type_ts ON events (type, ts_ms);

-- Identité matérielle : un SDR, une entrée, un mode de gain.
CREATE TABLE hardware (
    id            INTEGER PRIMARY KEY,
    identity      TEXT NOT NULL UNIQUE,   -- driver:model:serial[:antX]:gain_mode
    driver        TEXT NOT NULL,
    model         TEXT NOT NULL,
    serial        TEXT NOT NULL,
    antenna       TEXT NOT NULL DEFAULT '',
    gain_mode     TEXT NOT NULL,
    first_seen_ms INTEGER NOT NULL,
    last_seen_ms  INTEGER NOT NULL
);

-- Catalogue d'antennes de l'opérateur, réutilisé d'une campagne à l'autre.
CREATE TABLE antennas (
    id               INTEGER PRIMARY KEY,
    uuid             TEXT NOT NULL UNIQUE,
    type             TEXT NOT NULL,       -- liste fermée : dipole, inverted_v, vertical_quarter...
    name             TEXT NOT NULL,
    model            TEXT NOT NULL DEFAULT '',
    polarization     TEXT NOT NULL DEFAULT '',   -- horizontal, vertical, mixed
    height_m         REAL,
    height_note      TEXT NOT NULL DEFAULT '',
    azimuth_deg      REAL,                -- axe principal, degrés vrais (antennes directives)
    orientation_note TEXT NOT NULL DEFAULT '',
    bands_note       TEXT NOT NULL DEFAULT '',
    photo_path       TEXT NOT NULL DEFAULT '',
    created_ms       INTEGER NOT NULL,
    archived         INTEGER NOT NULL DEFAULT 0
);

-- Chaîne de réception d'une antenne, de l'antenne au SDR.
CREATE TABLE chains (
    id         INTEGER PRIMARY KEY,
    antenna_id INTEGER NOT NULL REFERENCES antennas (id),
    note       TEXT NOT NULL DEFAULT '',
    created_ms INTEGER NOT NULL
);
CREATE TABLE chain_elements (
    id         INTEGER PRIMARY KEY,
    chain_id   INTEGER NOT NULL REFERENCES chains (id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    kind       TEXT NOT NULL,             -- coax, balun, choke, arrester, filter, relay, preamp, other
    label      TEXT NOT NULL DEFAULT '',
    cable_type TEXT NOT NULL DEFAULT '',
    length_m   REAL,
    db_json    TEXT NOT NULL DEFAULT '{}',  -- perte (positive) ou gain (négatif) saisi par bande
    UNIQUE (chain_id, position)
);

-- Qualification : banc matériel ou campagne A/A.
CREATE TABLE qualification_runs (
    id              INTEGER PRIMARY KEY,
    uuid            TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL,        -- bench, aa
    reception_mode  TEXT NOT NULL,        -- switched, simultaneous
    band            TEXT NOT NULL DEFAULT '',
    center_hz       REAL NOT NULL,
    sample_rate     INTEGER NOT NULL,
    gain_json       TEXT NOT NULL DEFAULT '{}',
    switch_rate_hz  REAL,
    hardware_json   TEXT NOT NULL DEFAULT '[]',  -- identités des voies
    started_ms      INTEGER NOT NULL,
    ended_ms        INTEGER,
    status          TEXT NOT NULL,        -- running, done, failed, aborted
    results_json    TEXT NOT NULL DEFAULT '{}',
    verdicts_json   TEXT NOT NULL DEFAULT '{}',
    accepted_ms     INTEGER
);
CREATE TABLE hardware_profiles (
    id                 INTEGER PRIMARY KEY,
    qualification_id   INTEGER NOT NULL REFERENCES qualification_runs (id),
    identities_json    TEXT NOT NULL,     -- identités matérielles couvertes
    band               TEXT NOT NULL DEFAULT '',
    gain_json          TEXT NOT NULL DEFAULT '{}',
    switch_rate_hz     REAL,
    blanking_ms        REAL,
    useful_fraction    REAL,
    isolation_db       REAL,
    own_floor_dbfs     REAL,
    correction_json    TEXT NOT NULL DEFAULT '{}',  -- correction par voie et par fréquence
    margins_json       TEXT NOT NULL DEFAULT '{}',  -- marge et verdict par classe de signal
    created_ms         INTEGER NOT NULL,
    valid_until_ms     INTEGER,
    active             INTEGER NOT NULL DEFAULT 1
);

-- Campagnes de mesure.
CREATE TABLE campaigns (
    id                 INTEGER PRIMARY KEY,
    uuid               TEXT NOT NULL UNIQUE,
    title              TEXT NOT NULL DEFAULT '',
    callsign           TEXT NOT NULL DEFAULT '',
    reception_mode     TEXT NOT NULL,     -- switched, simultaneous
    measure_mode       TEXT NOT NULL,     -- fixed, auto
    low_hz             REAL NOT NULL,
    high_hz            REAL NOT NULL,
    band               TEXT NOT NULL DEFAULT '',
    state              TEXT NOT NULL,     -- preparation, running, interrupted, done, abandoned
    created_ms         INTEGER NOT NULL,
    start_ms           INTEGER,
    planned_end_ms     INTEGER,
    end_ms             INTEGER,
    n_channels         INTEGER NOT NULL,
    reference_channel  INTEGER NOT NULL DEFAULT 0,
    profile_id         INTEGER REFERENCES hardware_profiles (id),
    visibility         TEXT NOT NULL DEFAULT 'private',  -- public, unlisted, private, none
    params_json        TEXT NOT NULL DEFAULT '{}',
    load_test_json     TEXT NOT NULL DEFAULT '{}',
    volume_json        TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE campaign_channels (
    id          INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,          -- 0 à 9 ; A = 0
    antenna_id  INTEGER REFERENCES antennas (id),
    chain_id    INTEGER REFERENCES chains (id),
    hardware_id INTEGER REFERENCES hardware (id),
    input       TEXT NOT NULL DEFAULT '',  -- entrée du SDR en mode commuté
    color_slot  INTEGER NOT NULL,
    UNIQUE (campaign_id, idx)
);
CREATE TABLE segments (
    id          INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    start_ms    INTEGER NOT NULL,
    end_ms      INTEGER,
    reason      TEXT NOT NULL DEFAULT '',
    config_json TEXT NOT NULL DEFAULT '{}',  -- antennes, chaînes, gain, azimuts, distances
    UNIQUE (campaign_id, idx)
);
CREATE TABLE signals (
    id            INTEGER PRIMARY KEY,
    campaign_id   INTEGER NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    freq_hz       REAL NOT NULL,
    class         TEXT NOT NULL,           -- carrier, digital, ssb, am_fm, noise
    width_hz      REAL NOT NULL,
    noise_freq_hz REAL,
    source        TEXT NOT NULL,           -- fixed, auto, track
    valid_from_ms INTEGER NOT NULL,
    valid_to_ms   INTEGER
);
CREATE INDEX signals_campaign ON signals (campaign_id);

-- Mesures brutes. quality : champ de bits des codes qualité (OK = 0).
CREATE TABLE measurements (
    id          INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    segment_id  INTEGER REFERENCES segments (id),
    signal_id   INTEGER NOT NULL REFERENCES signals (id),
    channel_idx INTEGER NOT NULL,
    ts_ms       INTEGER NOT NULL,
    rssi_dbfs   REAL,
    floor_dbfs  REAL,
    snr_db      REAL,
    quality     INTEGER NOT NULL DEFAULT 0,
    track_id    INTEGER
);
CREATE INDEX measurements_campaign_ts ON measurements (campaign_id, ts_ms);

-- Agrégats par fenêtre, par signal et par voie ; écart à la voie de référence.
CREATE TABLE aggregates (
    id              INTEGER PRIMARY KEY,
    campaign_id     INTEGER NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    signal_id       INTEGER NOT NULL REFERENCES signals (id),
    channel_idx     INTEGER NOT NULL,
    window_start_ms INTEGER NOT NULL,
    window_ms       INTEGER NOT NULL,
    n               INTEGER NOT NULL,
    rssi_med REAL, rssi_p10 REAL, rssi_p90 REAL, rssi_min REAL, rssi_max REAL, rssi_sd REAL,
    floor_med REAL, floor_p10 REAL, floor_p90 REAL,
    snr_med REAL, snr_p10 REAL, snr_p90 REAL,
    dref_med REAL, dref_p10 REAL, dref_p90 REAL, dref_sd REAL,
    sent_wp         INTEGER NOT NULL DEFAULT 0,
    UNIQUE (campaign_id, signal_id, channel_idx, window_start_ms)
);
CREATE TABLE pair_aggregates (
    id              INTEGER PRIMARY KEY,
    campaign_id     INTEGER NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    signal_id       INTEGER NOT NULL REFERENCES signals (id),
    channel_a       INTEGER NOT NULL,
    channel_b       INTEGER NOT NULL,
    window_start_ms INTEGER NOT NULL,
    window_ms       INTEGER NOT NULL,
    metric          TEXT NOT NULL,         -- rssi, floor, snr
    n               INTEGER NOT NULL,
    med REAL, p10 REAL, p90 REAL, sd REAL,
    UNIQUE (campaign_id, signal_id, channel_a, channel_b, window_start_ms, metric)
);

-- Périodes ACTIVE, CALME, INTERROMPUE.
CREATE TABLE activity (
    id          INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    state       TEXT NOT NULL,             -- active, calm, interrupted
    start_ms    INTEGER NOT NULL,
    end_ms      INTEGER,
    reason      TEXT NOT NULL DEFAULT ''
);

-- Modes numériques (P10).
CREATE TABLE tracks (
    id                INTEGER PRIMARY KEY,
    campaign_id       INTEGER NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    callsign          TEXT NOT NULL,
    locator           TEXT NOT NULL DEFAULT '',
    distance_km       REAL,
    azimuth_deg       REAL,
    propagation_class TEXT NOT NULL DEFAULT '',  -- ground, nvis, skip, dx1, dxn
    state             TEXT NOT NULL,             -- candidate, tracking, validated, rejected
    reject_reason     TEXT NOT NULL DEFAULT '',
    audio_hz          REAL,
    parity            INTEGER,
    first_ms          INTEGER NOT NULL,
    last_ms           INTEGER NOT NULL
);
CREATE TABLE decodes (
    id          INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    track_id    INTEGER REFERENCES tracks (id),
    channel_idx INTEGER NOT NULL,
    slot_ms     INTEGER NOT NULL,
    digital_mode TEXT NOT NULL,            -- FT8, FT4, WSPR
    callsign    TEXT NOT NULL DEFAULT '',
    locator     TEXT NOT NULL DEFAULT '',
    message     TEXT NOT NULL,
    snr_db      REAL,
    audio_hz    REAL,
    power_dbm   REAL
);
CREATE INDEX decodes_campaign_slot ON decodes (campaign_id, slot_ms);

-- Signaux connus localisés (bonus).
CREATE TABLE known_signals (
    id        INTEGER PRIMARY KEY,
    freq_hz   REAL NOT NULL,
    name      TEXT NOT NULL,
    latitude  REAL,
    longitude REAL,
    locator   TEXT NOT NULL DEFAULT '',
    note      TEXT NOT NULL DEFAULT ''
);
