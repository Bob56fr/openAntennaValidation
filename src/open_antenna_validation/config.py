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
DriverName = Literal["sdrplay", "rtlsdr", "airspyhf", "simulated"]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class StationConfig(_Section):
    id: str = ""
    name: str = "Station openAntennaValidation"
    callsign: str = ""
    # Maidenhead locator, 4 or 6 characters (6 kept locally, truncated for WordPress).
    locator: str = Field(default="", pattern=r"^$|^[A-Ra-r]{2}[0-9]{2}([A-Xa-x]{2})?$")
    # IANA time zone of the station: local hours of the result pages, also on the site.
    timezone: str = Field(default="Europe/Paris", pattern=r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$")


class SdrplayConfig(_Section):
    # Fixed for a whole measurement series: the sweepable steps are the IF gain reductions.
    lna_state: int = Field(default=0, ge=0, le=27)
    # Empty: search the standard install locations.
    library_path: str = ""


class RtlsdrConfig(_Section):
    # librtlsdr of the RTL-SDR Blog branch (HF upconverter of the V4). Empty: standard places.
    library_path: str = ""


class AirspyhfConfig(_Section):
    # Preamplifier (+6 dB, compensated in digital by the receiver): fixed for a series.
    lna: bool = False
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
    serial: str = "SIM0001"
    # This SDR's antenna compared with the air (signals, noise), and a gain error of the
    # receiver itself (what the calibration of the simultaneous mode removes).
    signal_offset_db: float = 0.0
    noise_offset_db: float = 0.0
    gain_error_db: float = 0.0
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
    # Decodable FT8, FT4 and WSPR stations (digital/sim_stations.json) on these dial
    # frequencies (20 m: away from the analogue signals around 7.1 MHz), seen from this
    # locator. Directional gain (dB, cosine of the azimuth
    # difference): antenna B in switched mode, this SDR in simultaneous mode.
    digital_stations: bool = True
    digital_dials_hz: dict[str, float] = Field(default_factory=lambda: {
        "ft8": 14_074_000.0, "ft4": 14_080_000.0, "wspr": 14_095_600.0})
    station_locator: str = "JN18"
    beam_db: float = 0.0
    antenna_b_beam_db: float = 4.0
    beam_azimuth_deg: float = 270.0


class SdrSlot(_Section):
    """Another SDR of the station (simultaneous mode: one SDR per antenna)."""

    serial: str
    # Driver of this SDR; empty: the same as the main SDR (mixed models are allowed).
    driver: Literal["", "sdrplay", "rtlsdr", "airspyhf", "simulated"] = ""
    # RSP2: input used by this SDR (empty: the device default).
    antenna: Antenna = ""
    # Simulated driver only: how this SDR sees the air (see SimulatedConfig).
    sim_signal_offset_db: float = 0.0
    sim_noise_offset_db: float = 0.0
    sim_gain_error_db: float = 0.0
    sim_beam_db: float = 0.0


class HardwareConfig(_Section):
    driver: DriverName = "sdrplay"
    # Empty: first device detected by the driver.
    serial: str = ""
    # Wanted IQ rate: each SDR takes its lowest supported rate at or above it (else its
    # highest), so one value fits every model of the station.
    sample_rate: int = Field(default=250_000, gt=0)
    # RSP2: A or B; RSPdx: A, B or C. Empty: the device default.
    antenna: Antenna = ""
    ppm: float = Field(default=0.0, ge=-200.0, le=200.0)
    bias_tee: bool = False
    # Gain step chosen before a campaign. None: driver default.
    gain_step: int | None = Field(default=None, ge=0)
    sdrplay: SdrplayConfig = Field(default_factory=SdrplayConfig)
    rtlsdr: RtlsdrConfig = Field(default_factory=RtlsdrConfig)
    airspyhf: AirspyhfConfig = Field(default_factory=AirspyhfConfig)
    simulated: SimulatedConfig = Field(default_factory=SimulatedConfig)
    # The other SDRs of the station, for the simultaneous mode (up to 9 more).
    others: list[SdrSlot] = Field(default_factory=list, max_length=9)

    def for_slot(self, slot: SdrSlot) -> HardwareConfig:
        """Settings of the worker that drives ``slot``: same driver and gain, its own serial."""
        sim = self.simulated.model_copy(update={
            "serial": slot.serial, "signal_offset_db": slot.sim_signal_offset_db,
            "noise_offset_db": slot.sim_noise_offset_db,
            "gain_error_db": slot.sim_gain_error_db, "beam_db": slot.sim_beam_db})
        return self.model_copy(update={"driver": slot.driver or self.driver,
                                       "serial": slot.serial, "antenna": slot.antenna,
                                       "simulated": sim, "others": []})


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
    # Simultaneous mode: largest spread between the SDRs for pairing voices (SSB, AM/FM).
    align_tolerance_ms: float = Field(default=5.0, gt=0.0)
    # Test before launch: duration, and load of a worker (share of one core) or of the
    # station (share of all cores): usable below ok, possible after acceptance below max.
    load_test_s: float = Field(default=30.0, ge=2.0, le=600.0)
    load_ok: float = Field(default=0.7, gt=0.0, le=1.0)
    load_max: float = Field(default=0.85, gt=0.0, le=1.0)


class DemoConfig(_Section):
    """Demonstration campaigns, shown while no real campaign exists (decision D9)."""

    enabled: bool = True


class WordPressConfig(_Section):
    """Publication of the campaigns on the WordPress site (plugin open-antenna-validation)."""

    enabled: bool = False
    url: str = ""
    # Token of this station, created by its operator in the WordPress administration.
    token: str = ""
    interval_s: float = Field(default=300.0, ge=30.0)
    timeout_s: float = Field(default=30.0, ge=5.0)
    retry_max_s: float = Field(default=3600.0, ge=60.0)
    # Locator published: 4 characters (about 100 km) by default, never more than 6.
    locator_chars: Literal[0, 2, 4, 6] = 4
    # Windows per request (uncompressed JSON of about 30 bytes each).
    batch_rows: int = Field(default=20_000, ge=100, le=50_000)
    # Share of the site's package limit a campaign may use when its window is chosen.
    package_fill: float = Field(default=0.8, gt=0.0, le=1.0)


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


DigitalModeName = Literal["ft8", "ft4", "wspr"]


class DigitalSubband(_Section):
    band: str
    mode: DigitalModeName
    dial_hz: float = Field(gt=0)  # USB dial frequency: audio 0 Hz


def _default_subbands() -> list[DigitalSubband]:
    # WSJT-X default frequency list (Models/FrequencyList.cpp), every IARU region. None on
    # 60 m (national allocations), CB, 45 m and 85 m (cahier des charges, section 10).
    return [DigitalSubband(band=b, mode=m, dial_hz=f) for b, m, f in (
        ("80 m", "ft8", 3_573_000),
        ("80 m", "ft4", 3_575_000),
        ("80 m", "wspr", 3_568_600),
        ("40 m", "ft8", 7_074_000),
        ("40 m", "ft4", 7_047_500),
        ("40 m", "wspr", 7_038_600),
        ("30 m", "ft8", 10_136_000),
        ("30 m", "ft4", 10_140_000),
        ("30 m", "wspr", 10_138_700),
        ("20 m", "ft8", 14_074_000),
        ("20 m", "ft4", 14_080_000),
        ("20 m", "wspr", 14_095_600),
        ("17 m", "ft8", 18_100_000),
        ("17 m", "ft4", 18_104_000),
        ("17 m", "wspr", 18_104_600),
        ("15 m", "ft8", 21_074_000),
        ("15 m", "ft4", 21_140_000),
        ("15 m", "wspr", 21_094_600),
        ("12 m", "ft8", 24_915_000),
        ("12 m", "ft4", 24_919_000),
        ("12 m", "wspr", 24_924_600),
        ("10 m", "ft8", 28_074_000),
        ("10 m", "ft4", 28_180_000),
        ("10 m", "wspr", 28_124_600)
    )]


def _per_mode(ft8: float, ft4: float, wspr: float) -> dict[str, float]:
    return {"ft8": ft8, "ft4": ft4, "wspr": wspr}


def _default_ground_km() -> dict[str, float]:
    # Ground wave range by band, ambiguous with NVIS (cahier des charges, section 10).
    return {"80 m": 80.0, "40 m": 60.0, "30 m": 50.0, "20 m": 40.0, "17 m": 35.0,
            "15 m": 35.0, "12 m": 30.0, "10 m": 30.0}


class DigitalConfig(_Section):
    """Decoding of FT8, FT4 and WSPR by the WSJT-X programs (decision D22)."""

    # Directory of the WSJT-X programs (jt9, wsprd…); empty: the usual places.
    wsjtx_path: str = ""
    decode_depth: int = Field(default=3, ge=1, le=3)
    subbands: list[DigitalSubband] = Field(default_factory=_default_subbands)
    # Tracks: same station if the audio frequency stays within the tolerance…
    freq_tolerance_hz: dict[str, float] = Field(default_factory=lambda: _per_mode(10.0, 20.0, 5.0))
    # …closed after N slots without a decode, validated with at least K measured slots.
    silence_slots: dict[str, int] = Field(
        default_factory=lambda: {"ft8": 8, "ft4": 16, "wspr": 15})
    min_measures: dict[str, int] = Field(default_factory=lambda: {"ft8": 3, "ft4": 3, "wspr": 2})
    overlap_hz: float = Field(default=50.0, gt=0)  # two signals closer than this overlap
    # Propagation classes by distance (km), and F2 layer height for the take-off angle.
    ground_wave_km: dict[str, float] = Field(default_factory=_default_ground_km)
    ground_wave_default_km: float = Field(default=50.0, ge=0)
    nvis_km: float = Field(default=400.0, gt=0)
    skip_km: float = Field(default=1000.0, gt=0)
    one_hop_km: float = Field(default=3800.0, gt=0)
    f2_height_km: float = Field(default=300.0, gt=0)
    # Clock: warning beyond this offset (median DT of the decodes, or the NTP server).
    clock_tolerance_s: float = Field(default=0.5, gt=0)
    ntp_server: str = "pool.ntp.org"
    ntp_interval_s: float = Field(default=3600.0, ge=0)  # 0: no NTP check


class Config(_Section):
    station: StationConfig = Field(default_factory=StationConfig)
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    receiver: ReceiverConfig = Field(default_factory=ReceiverConfig)
    spectrum: SpectrumConfig = Field(default_factory=SpectrumConfig)
    bands: list[Band] = Field(default_factory=_default_bands)
    qualification: QualificationConfig = Field(default_factory=QualificationConfig)
    campaign: CampaignConfig = Field(default_factory=CampaignConfig)
    demo: DemoConfig = Field(default_factory=DemoConfig)
    digital: DigitalConfig = Field(default_factory=DigitalConfig)
    wordpress: WordPressConfig = Field(default_factory=WordPressConfig)
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
