"""Hardware layer. Nothing outside this package knows a specific SDR."""

from open_antenna_validation.config import HardwareConfig
from open_antenna_validation.hardware.base import (
    Availability,
    DeviceInfo,
    DriverUnavailable,
    GainState,
    GainStep,
    HardwareError,
    IQBlock,
    SdrDriver,
    SwitchCommand,
    hardware_identity,
)
from open_antenna_validation.hardware.sdrplay import SdrplayDriver
from open_antenna_validation.hardware.simulated import SimulatedDriver


def create_driver(config: HardwareConfig) -> SdrDriver:
    driver: SdrDriver
    if config.driver == "simulated":
        driver = SimulatedDriver(config.simulated)
    else:
        driver = SdrplayDriver(config.sdrplay, sample_rate=config.sample_rate)
    driver.set_antenna(config.antenna)
    if config.gain_step is not None and config.gain_step < len(driver.gain_steps()):
        driver.set_gain_step(config.gain_step)
    return driver


__all__ = [
    "Availability",
    "DeviceInfo",
    "DriverUnavailable",
    "GainState",
    "GainStep",
    "HardwareError",
    "IQBlock",
    "SdrDriver",
    "SwitchCommand",
    "create_driver",
    "hardware_identity",
]
