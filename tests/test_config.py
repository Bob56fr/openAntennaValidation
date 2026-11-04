from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from open_antenna_validation.config import (
    Config,
    ConfigError,
    band_of,
    load_config,
    save_config,
)

REPO = Path(__file__).resolve().parents[1]


def test_default_toml_matches_model_defaults() -> None:
    with (REPO / "config" / "default.toml").open("rb") as f:
        documented = tomllib.load(f)
    assert documented == Config().model_dump(mode="json", exclude_none=True)


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert load_config(tmp_path / "absent.toml") == Config()


def test_round_trip(tmp_path: Path) -> None:
    config = Config()
    config.station.callsign = "F4ABC"
    config.station.locator = "JN18eu"
    config.hardware.sdrplay.lna_state = 4
    config.receiver.center_frequency_hz = 6_660_000.0
    path = tmp_path / "config.toml"
    save_config(config, path)
    assert load_config(path) == config


def test_partial_file_keeps_other_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[web]\nport = 9000\n", encoding="utf-8")
    config = load_config(path)
    assert config.web.port == 9000
    assert config.storage.raw_retention_days == 90
    assert len(config.bands) == 12


def test_saved_file_holds_only_overrides(tmp_path: Path) -> None:
    config = Config()
    config.hardware.antenna = "B"
    path = tmp_path / "config.toml"
    save_config(config, path)
    with path.open("rb") as f:
        data = tomllib.load(f)
    assert data == {"hardware": {"antenna": "B"}}


def test_band_plan_defaults() -> None:
    config = Config()
    forty = band_of(config, 7_000_000, 7_200_000)
    assert forty is not None and forty.name == "40 m"
    free_45 = band_of(config, 6_650_000, 6_670_000)
    assert free_45 is not None and free_45.name == "45 m"
    assert band_of(config, 7_150_000, 7_300_000) is None


@pytest.mark.parametrize(
    "content",
    [
        '[station]\nlocator = "JN1"\n',
        "[receiver]\ncenter_frequency_hz = 145000000.0\n",
        "[hardware]\ndriver = \"airspy\"\n",
        "[web]\nprot = 8080\n",  # typo: unknown keys are rejected
        '[[bands]]\nname = "x"\nlow_hz = 7200000.0\nhigh_hz = 7000000.0\n',
        "not toml = = =",
    ],
)
def test_invalid_files_raise_config_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)
