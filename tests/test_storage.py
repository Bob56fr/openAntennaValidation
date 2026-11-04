from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from open_antenna_validation.storage import Database

INSERT_CHANNEL = "INSERT INTO campaign_channels (campaign_id, idx, color_slot) VALUES (1, 0, ?)"
SPEC_TABLES = {
    "app_state", "events", "hardware", "antennas", "chains", "chain_elements",
    "qualification_runs", "hardware_profiles", "campaigns", "campaign_channels", "segments",
    "signals", "measurements", "aggregates", "pair_aggregates", "activity", "tracks", "decodes",
    "known_signals",
}


def test_migrate_creates_all_spec_tables_and_is_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    applied = db.migrate()
    assert applied[0] == "0001_initial.sql"
    assert db.migrate() == []
    assert db.schema_version() == len(applied)
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert SPEC_TABLES | {"schema_version"} <= tables
    db.close()


def test_campaign_channels_are_unique_per_campaign(db: Database) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO campaigns (uuid, reception_mode, measure_mode, low_hz, high_hz, state,"
            " created_ms, n_channels) VALUES ('c1', 'simultaneous', 'auto', 7e6, 7.2e6,"
            " 'preparation', 1, 2)"
        )
        conn.execute(INSERT_CHANNEL, (1,))
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute(INSERT_CHANNEL, (2,))


def test_state_round_trip(db: Database) -> None:
    assert db.get_state("antenna") is None
    db.set_state("antenna", "A")
    db.set_state("antenna", "B")
    assert db.get_state("antenna") == "B"


def test_wal_and_full_sync(db: Database) -> None:
    assert db.query("PRAGMA journal_mode")[0][0] == "wal"
    assert db.query("PRAGMA synchronous")[0][0] == 2  # FULL


def test_log_event(db: Database) -> None:
    db.log_event("alert", {"texte": "écart"})
    row = db.query("SELECT ts_ms, type, detail_json FROM events")[0]
    assert row["type"] == "alert"
    assert json.loads(row["detail_json"]) == {"texte": "écart"}
    assert row["ts_ms"] > 1_700_000_000_000


def test_transaction_rolls_back_on_error(db: Database) -> None:
    try:
        with db.transaction() as conn:
            conn.execute("INSERT INTO events (ts_ms, type) VALUES (1, 'x')")
            raise RuntimeError
    except RuntimeError:
        pass
    assert db.query("SELECT COUNT(*) FROM events")[0][0] == 0
