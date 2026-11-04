"""Configuration: TOML file on disk, validated by pydantic models.

Defaults live in the models below. ``config/default.toml`` is the documented
reference copy and a test keeps both in sync.
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

CONFIG_FILENAME = "config.toml"
HOME_ENV = "OAV_HOME"
APP_DIR = "open-antenna-validation"

# Antenna port; empty: the device default.
Antenna = Literal["", "A", "B", "C"]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class StationConfig(_Section):
    id: str = ""
    name: str = "Station openAntennaValidation"
    callsign: str = ""
    # Maidenhead locator, 4 or 6 characters (6 kept locally, truncated for WordPress).
    locator: str = Field(default="", pattern=r"^$|^[A-Ra-r]{2}[0-9]{2}([A-Xa-x]{2})?$")


class SdrplayConfig(_Section):
    # Fixed for a whole measurement series: the sweepable steps are the IF gain reductions.
    lna_state: int = Field(default=0, ge=0, le=27)
    # Empty: search the standard install locations.
    library_path: str = ""


class SimulatedSignal(_Section):
    """One synthetic HF signal of the simulated driver."""

    offset_hz: float
    kind: Literal["carrier", "ssb", "ft8"]
    dbfs: float = Field(le=0.0)


def _default_simulated_signals() -> list[SimulatedSignal]:
    return [
        SimulatedSignal(offset_hz=-62_000.0, kind="carrier", dbfs=-62.0),
        SimulatedSignal(offset_hz=-26_000.0, kind="ft8", dbfs=-72.0),
        SimulatedSignal(offset_hz=-10_000.0, kind="ssb", dbfs=-58.0),
        SimulatedSignal(offset_hz=45_000.0, kind="ssb", dbfs=-66.0),
    ]


class SimulatedConfig(_Section):
    noise_dbfs: float = -95.0
    # The simulated signals sit at this frequency plus their offset, whatever the tuning.
    air_center_hz: float = 7_100_000.0
    seed: int = 1
    # Pace read_iq() like a real receiver; tests turn it off.
    realtime: bool = True
    signals: list[SimulatedSignal] = Field(default_factory=_default_simulated_signals)
    # Antenna B compared with antenna A, on signals and on noise.
    antenna_b_signal_db: float = -3.0
    antenna_b_noise_db: float = 1.0
    # Slow fading common to both antennas (dB peak), period in seconds.
    fading_db: float = Field(default=6.0, ge=0.0)
    fading_period_s: float = Field(default=20.0, gt=0.0)
    # Antenna switch as a real receiver would show it: delay, glitch, settling time.
    switch_latency_ms: float = Field(default=1.4, ge=0.0)
    switch_glitch_db: float = Field(default=6.0, ge=0.0)
    switch_settle_ms: float = Field(default=1.6, ge=0.0)


class HardwareConfig(_Section):
    driver: Literal["sdrplay", "simulated"] = "sdrplay"
    # Empty: first device detected by the driver.
    serial: str = ""
    # Delivered IQ rate; must be one of the driver's supported rates.
    sample_rate: int = Field(default=250_000, gt=0)
    # RSP2: A or B; RSPdx: A, B or C. Empty: the device default.
    antenna: Antenna = ""
    ppm: float = Field(default=0.0, ge=-200.0, le=200.0)
    bias_tee: bool = False
    # Gain step chosen before a campaign. None: driver default.
    gain_step: int | None = Field(default=None, ge=0)
    sdrplay: SdrplayConfig = Field(default_factory=SdrplayConfig)
    simulated: SimulatedConfig = Field(default_factory=SimulatedConfig)


class Band(_Section):
    name: str
    low_hz: float = Field(gt=0.0)
    high_hz: float = Field(gt=0.0)
    note: str = ""

    @model_validator(mode="after")
    def _ordered(self) -> Band:
        if self.high_hz <= self.low_hz:
            raise ValueError(f"bande {self.name} : la limite haute doit dépasser la limite basse")
        return self


def _default_bands() -> list[Band]:
    # IARU Region 1 by default, plus CB, 45 m and 85 m (cahier des charges, section 3).
    return [
        Band(name="80 m", low_hz=3_500_000, high_hz=3_800_000),
        Band(name="85 m", low_hz=3_400_000, high_hz=3_500_000, note="3,450 MHz ± 50 kHz"),
        Band(name="60 m", low_hz=5_351_500, high_hz=5_366_500, note="Variable selon les pays"),
        Band(name="45 m", low_hz=6_610_000, high_hz=6_710_000, note="6,660 MHz ± 50 kHz"),
        Band(name="40 m", low_hz=7_000_000, high_hz=7_200_000),
        Band(name="30 m", low_hz=10_100_000, high_hz=10_150_000),
        Band(name="20 m", low_hz=14_000_000, high_hz=14_350_000),
        Band(name="17 m", low_hz=18_068_000, high_hz=18_168_000),
        Band(name="15 m", low_hz=21_000_000, high_hz=21_450_000),
        Band(name="12 m", low_hz=24_890_000, high_hz=24_990_000),
        Band(name="CB 11 m", low_hz=26_000_000, high_hz=28_000_000, note="CB et bandes voisines"),
        Band(name="10 m", low_hz=28_000_000, high_hz=29_700_000),
    ]


class ReceiverConfig(_Section):
    """What the receiver listens to outside a campaign (dashboard)."""

    center_frequency_hz: float = Field(default=7_100_000.0, ge=1_000_000.0, le=30_000_000.0)
    # The SDR is tuned this far away from the centre so that the DC spike falls outside
    # the measured signals; the display recentres digitally.
    dc_offset_hz: float = Field(default=0.0, ge=0.0, le=200_000.0)


class SpectrumConfig(_Section):
    fft_size: Literal[1024, 2048, 4096, 8192] = 4096
    fps: float = Field(default=5.0, ge=1.0, le=10.0)
    block_ms: int = Field(default=100, ge=20, le=500)
    # Points sent to the page per spectrum (the FFT is decimated by max-hold).
    display_points: int = Field(default=1024, ge=256, le=4096)
    # ADC clipping: share of samples above clip_threshold of full scale.
    clip_threshold: float = Field(default=0.99, gt=0.0, le=1.0)
    clip_alert_ratio: float = Field(default=0.001, gt=0.0)


def _default_class_rates() -> dict[str, float]:
    # Switching rate each signal class needs: a voice changes faster than a carrier.
    return {"carrier": 2.0, "digital": 2.0, "ssb": 20.0}


class QualificationConfig(_Section):
    """Switching bench (cahier des charges, section 6) and verdict thresholds."""

    switches: int = Field(default=400, ge=20, le=5000)
    dwell_ms: float = Field(default=10.0, ge=2.0, le=200.0)
    levels_seconds: float = Field(default=4.0, ge=1.0, le=60.0)
    subbands: int = Field(default=16, ge=4, le=64)
    # Settling: back within this tolerance of the final level (never below 3 x the noise).
    tolerance_db: float = Field(default=0.2, gt=0.0)
    # Smallest level step between inputs that makes the switch visible in the stream.
    step_min_db: float = Field(default=6.0, gt=0.0)
    blanking_margin_ms: float = Field(default=0.2, ge=0.0)
    attenuator_db: float = Field(default=10.0, gt=0.0, le=40.0)
    # Default fast switching rate of a campaign, used for the useful time of the profile.
    switch_rate_hz: float = Field(default=25.0, gt=0.0, le=200.0)
    class_rate_hz: dict[str, float] = Field(default_factory=_default_class_rates)
    # Useful time at the rate a class needs: usable above ok, degraded above min.
    useful_ok: float = Field(default=0.7, gt=0.0, le=1.0)
    useful_min: float = Field(default=0.4, gt=0.0, le=1.0)
    validity_days: int = Field(default=180, ge=1)


def _default_class_widths() -> dict[str, float]:
    return {"carrier": 200.0, "digital": 100.0, "ssb": 2_700.0, "am_fm": 8_000.0}


def _default_windows() -> list[tuple[float, float]]:
    # (campaign duration up to, in hours ; aggregation window, in seconds)
    return [(1.0, 10.0), (24.0, 60.0), (168.0, 300.0), (1e9, 900.0)]


class CampaignConfig(_Section):
    """Campaign engine (cahier des charges, sections 8 and 9)."""

    # One value per signal and channel every period (at most), raw measurements kept.
    period_s: float = Field(default=10.0, ge=1.0, le=60.0)
    class_width_hz: dict[str, float] = Field(default_factory=_default_class_widths)
    # A signal is present when it stands this far above its local floor.
    presence_db: float = Field(default=6.0, gt=0.0)
    # Band calm when no signal is present for this long.
    calm_after_s: float = Field(default=300.0, ge=10.0)
    # Blanking after a switch when no hardware profile exists (conservative).
    default_blanking_ms: float = Field(default=5.0, ge=0.0, le=50.0)
    windows: list[tuple[float, float]] = Field(default_factory=_default_windows)
    max_duration_days: float = Field(default=31.0, gt=0.0)
    # Automatic band mode: survey, detection, re-evaluation (section 8).
    survey_s: float = Field(default=60.0, ge=5.0, le=600.0)
    detect_db: float = Field(default=8.0, gt=0.0)
    max_signals: int = Field(default=8, ge=1, le=20)
    reevaluate_s: float = Field(default=3600.0, ge=10.0)
    # Band calm: re-survey this often (automatic mode) and measure at this slower period.
    calm_probe_s: float = Field(default=300.0, ge=10.0)
    calm_period_s: float = Field(default=60.0, ge=1.0, le=600.0)


class DemoConfig(_Section):
    """Demonstration campaigns, shown while no real campaign exists (decision D9)."""

    enabled: bool = True


class WebConfig(_Section):
    host: str = "0.0.0.0"
    port: int = Field(default=8660, ge=1, le=65535)
    password_hash: str = ""
    session_secret: str = ""
    session_max_age_hours: int = Field(default=12, ge=1, le=24 * 30)
    # Set when the page is served over HTTPS (reverse proxy), adds the Secure cookie flag.
    https_only: bool = False


class StorageConfig(_Section):
    # Relative paths are resolved against the station home directory.
    database: str = "oav.sqlite3"
    # Raw measurements are kept for the campaign duration plus this many days.
    raw_retention_days: int = Field(default=90, ge=1)


class LoggingConfig(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    file: str = "logs/oav.log"
    max_bytes: int = Field(default=5_000_000, ge=10_000)
    backup_count: int = Field(default=5, ge=1)


class Config(_Section):
    station: StationConfig = Field(default_factory=StationConfig)
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    receiver: ReceiverConfig = Field(default_factory=ReceiverConfig)
    spectrum: SpectrumConfig = Field(default_factory=SpectrumConfig)
    bands: list[Band] = Field(default_factory=_default_bands)
    qualification: QualificationConfig = Field(default_factory=QualificationConfig)
    campaign: CampaignConfig = Field(default_factory=CampaignConfig)
    demo: DemoConfig = Field(default_factory=DemoConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


class ConfigError(Exception):
    """Invalid or unreadable configuration file (message in French, for the user)."""


def default_home() -> Path:
    env = os.environ.get(HOME_ENV)
    if env:
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / APP_DIR
    return Path.home() / ".local" / "share" / APP_DIR


def resolve_path(home: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else home / path


def load_config(path: Path) -> Config:
    """Load ``path``; a missing file yields the defaults."""
    if not path.exists():
        return Config()
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Lecture impossible de {path} : {exc}") from exc
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Configuration invalide dans {path} :\n{exc}") from exc


def save_config(config: Config, path: Path) -> None:
    """Write only non-default values, atomically (a power cut never truncates the file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as f:
        tomli_w.dump(config.model_dump(mode="json", exclude_none=True, exclude_defaults=True), f)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def band_of(config: Config, low_hz: float, high_hz: float) -> Band | None:
    """The configured band that fully contains [low_hz, high_hz], if any."""
    for band in config.bands:
        if band.low_hz <= low_hz and high_hz <= band.high_hz:
            return band
    return None
