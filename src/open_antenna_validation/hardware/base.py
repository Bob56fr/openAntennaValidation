"""Hardware abstraction: the only interface business code may use to reach an SDR."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

import numpy as np
import numpy.typing as npt

IQBlock = npt.NDArray[np.complex64]
NativeParams = dict[str, int | float | str | bool]


class HardwareError(Exception):
    """Hardware failure; the message is in French and shown to the user."""


class DriverUnavailable(HardwareError):
    """A driver dependency is missing (vendor library, system service...)."""

    def __init__(self, message: str, help_url: str | None = None) -> None:
        super().__init__(message)
        self.help_url = help_url


@dataclass(frozen=True)
class DeviceInfo:
    driver: str
    model: str
    serial: str
    details: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GainStep:
    """One sweepable gain setting. Steps are ordered from lowest to highest gain."""

    index: int
    label: str
    native: NativeParams


@dataclass(frozen=True)
class GainState:
    step_index: int
    native: NativeParams
    agc: bool = False


@dataclass(frozen=True)
class Availability:
    available: bool
    message: str = ""
    help_url: str | None = None
    library_version: str | None = None


@dataclass(frozen=True)
class SwitchCommand:
    """An antenna switch as commanded. The effective instant is found later in the stream.

    ``sample`` is the absolute index of the next sample that read_iq() was going to return
    when the command was sent: the switch takes effect at or after it.
    """

    antenna: str
    sample: int
    monotonic_ns: int


def hardware_identity(device: DeviceInfo, antenna: str, gain_mode: str) -> str:
    """Key of a measurement series: any change requires a new qualification."""
    parts = [device.driver, device.model, device.serial]
    if antenna:
        parts.append(f"ant{antenna}")
    parts.append(gain_mode)
    return ":".join(parts)


class SdrDriver(ABC):
    name: ClassVar[str]
    # False for a simulator running faster than real time: its wall clock means nothing.
    realtime: bool = True

    @abstractmethod
    def availability(self) -> Availability:
        """Check dependencies without touching any device."""

    @abstractmethod
    def detect(self) -> list[DeviceInfo]: ...

    @abstractmethod
    def open(self, serial: str | None = None) -> DeviceInfo:
        """Open the device (first one found when ``serial`` is empty) and start streaming."""

    @abstractmethod
    def close(self) -> None:
        """Stop streaming and release the USB device, so another process can use it."""

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    @abstractmethod
    def frequency_range(self) -> tuple[float, float]:
        """Tunable range in Hz."""

    @abstractmethod
    def set_frequency(self, hz: float) -> None: ...

    @abstractmethod
    def frequency(self) -> float: ...

    @abstractmethod
    def set_sample_rate(self, sps: int) -> None: ...

    @abstractmethod
    def sample_rate(self) -> int: ...

    def instant_bandwidth(self) -> float:
        """Usable band without aliasing at the current rate (Hz). Drivers may narrow it."""
        return 0.8 * self.sample_rate()

    @abstractmethod
    def set_ppm(self, ppm: float) -> None: ...

    @abstractmethod
    def set_bias_tee(self, enabled: bool) -> None: ...

    @abstractmethod
    def antennas(self, device: DeviceInfo) -> list[str]:
        """Selectable antenna inputs of ``device``, first one is the default; [] if none."""

    @abstractmethod
    def set_antenna(self, antenna: str) -> SwitchCommand | None:
        """Choose the input (empty: device default); applied live when the device is open.

        Returns the command position in the stream when the device is open, else None.
        """

    def effective_antenna(self, device: DeviceInfo) -> str:
        """Input actually used on ``device``: the chosen one, else the default; '' if none."""
        choices = self.antennas(device)
        if not choices:
            return ""
        chosen = self._chosen_antenna()
        return chosen if chosen in choices else choices[0]

    @abstractmethod
    def _chosen_antenna(self) -> str: ...

    @abstractmethod
    def gain_steps(self) -> list[GainStep]: ...

    @abstractmethod
    def set_gain_step(self, index: int) -> None: ...

    @abstractmethod
    def get_gain_state(self) -> GainState: ...

    @abstractmethod
    def gain_mode(self) -> str:
        """Fixed part of the gain setup (e.g. SDRplay LNA state), part of the identity."""

    @abstractmethod
    def read_iq(self, n: int, timeout_s: float = 2.0) -> IQBlock:
        """Return exactly ``n`` complex64 samples, full scale = 1.0."""

    @abstractmethod
    def sample_counter(self) -> int:
        """Absolute index of the next sample read_iq() will return (since open)."""

    def dropped_samples(self) -> int:
        """Samples lost since open (USB gaps, buffer overruns), as far as the driver can tell."""
        return 0

    @abstractmethod
    def supported_sample_rates(self) -> list[int]: ...

    def overload(self) -> bool:
        """True while the hardware reports an ADC overload (drivers that can tell)."""
        return False

    def dbm_offset_db(self, frequency_hz: float) -> float | None:
        """Nominal dBm = dBFS + offset at the current gain; None when unknown.

        Uncalibrated estimate from the vendor gain tables, for display only:
        measurements and comparisons always use dBFS.
        """
        return None

    def shutdown(self) -> None:
        """Release library resources at process exit."""
        if self.is_open:
            self.close()

    def check_antenna(self, device: DeviceInfo, antenna: str) -> None:
        choices = self.antennas(device)
        if antenna and antenna not in choices:
            available = ", ".join(choices) if choices else "aucune sélection possible"
            raise HardwareError(
                f"Entrée {antenna} indisponible sur {device.model} ({available})."
            )

    def _check_gain_index(self, index: int) -> GainStep:
        steps = self.gain_steps()
        if not 0 <= index < len(steps):
            raise HardwareError(f"Pas de gain {index} hors plage (0 à {len(steps) - 1}).")
        return steps[index]
