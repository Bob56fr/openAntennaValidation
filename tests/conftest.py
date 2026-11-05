from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from open_antenna_validation.config import CONFIG_FILENAME, Config, save_config
from open_antenna_validation.service import Station
from open_antenna_validation.storage import Database
from open_antenna_validation.web.auth import hash_password

PASSWORD = "mot-de-passe-test"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def station_home(tmp_path: Path) -> Path:
    config = Config()
    config.station.id = "test-station"
    config.hardware.driver = "simulated"
    config.hardware.simulated.realtime = False
    config.digital.ntp_interval_s = 0.0  # no network in the tests
    config.spectrum.fps = 10.0
    config.web.session_secret = "secret-de-test"
    config.web.password_hash = hash_password(PASSWORD)
    save_config(config, tmp_path / CONFIG_FILENAME)
    return tmp_path


@pytest.fixture
def station(station_home: Path) -> Iterator[Station]:
    s = Station.from_home(station_home, inline=True)
    yield s
    s.shutdown()
    s.db.close()
