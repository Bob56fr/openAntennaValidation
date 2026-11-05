-- Modes numériques (P10, décisions D22) : colonnes qui manquaient aux tables de 0001.
-- Une ligne de decodes = un message d'un créneau sur une voie : décodé sur cette voie, ou
-- seulement mesuré (décodé sur une autre voie, mesuré aux mêmes tons et aux mêmes instants).
ALTER TABLE decodes ADD COLUMN decoded INTEGER NOT NULL DEFAULT 1;
ALTER TABLE decodes ADD COLUMN dt_s REAL;
ALTER TABLE decodes ADD COLUMN level_dbfs REAL;     -- mesure guidée, brute (dBFS du SDR)
ALTER TABLE decodes ADD COLUMN floor_dbfs REAL;     -- bruit d'un ton (largeur : écart des tons)
ALTER TABLE decodes ADD COLUMN guided TEXT NOT NULL DEFAULT '';  -- tones (réencodé), seen (tons lus), '' (non mesuré)
ALTER TABLE decodes ADD COLUMN flags TEXT NOT NULL DEFAULT '';   -- a1..a7 (a priori), ? (douteux), overlap
ALTER TABLE decodes ADD COLUMN segment_id INTEGER REFERENCES segments (id);
CREATE INDEX decodes_track ON decodes (track_id);

ALTER TABLE tracks ADD COLUMN digital_mode TEXT NOT NULL DEFAULT '';
ALTER TABLE tracks ADD COLUMN country TEXT NOT NULL DEFAULT '';
ALTER TABLE tracks ADD COLUMN approximate INTEGER NOT NULL DEFAULT 0;   -- position du pays (cty.dat)
ALTER TABLE tracks ADD COLUMN takeoff_deg REAL;                          -- angle de départ estimé
ALTER TABLE tracks ADD COLUMN locator_changed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tracks ADD COLUMN closed INTEGER NOT NULL DEFAULT 0;         -- 1 : piste terminée
ALTER TABLE tracks ADD COLUMN n_measures INTEGER NOT NULL DEFAULT 0;     -- créneaux mesurés
ALTER TABLE tracks ADD COLUMN qso_end INTEGER NOT NULL DEFAULT 0;        -- RR73 / 73 reçu
CREATE INDEX tracks_campaign ON tracks (campaign_id, state);
