"""SQLite access.

A single connection per process, guarded by a lock: measurements are written in
batches (one transaction per window), which keeps transactions simple. WAL and
``synchronous=FULL`` make committed data survive a power cut.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from open_antenna_validation.timeutil import now_ms

_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class StorageError(Exception):
    pass


def _migrations() -> list[tuple[int, str, str]]:
    folder = resources.files("open_antenna_validation.storage") / "migrations"
    found = []
    for entry in folder.iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort()
    versions = [v for v, _, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise StorageError(f"Numérotation des migrations incohérente : {versions}")
    return found


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit mode: transactions are explicit (see transaction()).
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def schema_version(self) -> int:
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            if not exists:
                return 0
            row = self._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            return int(row[0] or 0)

    def migrate(self) -> list[str]:
        """Apply pending migrations, each one atomically. Returns the names applied."""
        applied = []
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_ms INTEGER NOT NULL)"
            )
            current = self.schema_version()
            for version, name, sql in _migrations():
                if version <= current:
                    continue
                script = (
                    "BEGIN;\n"
                    f"{sql}\n"
                    "INSERT INTO schema_version (version, name, applied_ms) "
                    f"VALUES ({version}, '{name}', {now_ms()});\n"
                    "COMMIT;"
                )
                try:
                    self._conn.executescript(script)
                except sqlite3.Error as exc:
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    raise StorageError(f"Échec de la migration {name} : {exc}") from exc
                applied.append(name)
        return applied

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def get_state(self, key: str) -> str | None:
        rows = self.query("SELECT value FROM app_state WHERE key = ?", (key,))
        return str(rows[0]["value"]) if rows else None

    def set_state(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO app_state (key, value) VALUES (?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def log_event(self, event_type: str, detail: dict[str, Any] | None = None) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO events (ts_ms, type, detail_json) VALUES (?, ?, ?)",
                (now_ms(), event_type, json.dumps(detail or {}, ensure_ascii=False)),
            )
