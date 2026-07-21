"""
Continuous multi-sensor stream generator.

Key feature: baselines are NOT flat. Each sensor's "normal" distribution shifts
with time-of-day (shift) and whether a shift-handover / maintenance window is
active, so Agent 1 must learn shift-aware baselines rather than fixed thresholds.
"""
import math
import random
import time
from dataclasses import dataclass
from enum import Enum

from generator.plant_model import ALL_SENSORS, SensorSpec, SensorType


class Shift(str, Enum):
    DAY = "day"      # 06:00-14:00
    EVENING = "evening"  # 14:00-22:00
    NIGHT = "night"  # 22:00-06:00


def shift_for_hour(hour: float) -> Shift:
    if 6 <= hour < 14:
        return Shift.DAY
    if 14 <= hour < 22:
        return Shift.EVENING
    return Shift.NIGHT


def is_handover_window(hour: float) -> bool:
    """+-20 min around each shift boundary (06:00, 14:00, 22:00)."""
    for boundary in (6.0, 14.0, 22.0, 30.0):  # 30.0 = wraparound for 06:00 next day
        if abs((hour % 24) - (boundary % 24)) <= (20 / 60):
            return True
    return False


# Shift multipliers: night shift tends to run slightly hotter/noisier (fewer staff,
# less proactive tuning); handover windows add transient noise on most sensors.
_SHIFT_MEAN_MULT = {Shift.DAY: 1.00, Shift.EVENING: 1.02, Shift.NIGHT: 1.05}
_SHIFT_STD_MULT = {Shift.DAY: 1.00, Shift.EVENING: 1.10, Shift.NIGHT: 1.25}
_HANDOVER_STD_MULT = 1.6


@dataclass
class Reading:
    timestamp: float       # unix seconds
    sim_hour: float         # 0-24 simulated hour-of-day, for readability
    sensor_id: str
    zone_id: str
    sensor_type: str
    value: float
    unit: str
    shift: str
    handover: bool
    injected_anomaly: bool = False
    anomaly_tag: str | None = None


class SensorStreamGenerator:
    """
    Produces one Reading per sensor per call to `tick()`.
    Time is simulated: each tick advances `sim_seconds_per_tick` of plant time,
    independent of wall-clock, so demos can run fast or be scrubbed.
    """

    def __init__(self, start_sim_hour: float = 6.0, sim_seconds_per_tick: float = 5.0, seed: int | None = None):
        self.sim_hour = start_sim_hour
        self.sim_seconds_per_tick = sim_seconds_per_tick
        self.rng = random.Random(seed)
        # active injected anomalies: sensor_id -> dict(remaining_ticks, magnitude, tag)
        self._active_anomalies: dict[str, dict] = {}
        self.tick_count = 0

    def inject_anomaly(self, sensor_id: str, magnitude_std: float = 6.0, duration_ticks: int = 12, tag: str = "manual"):
        """Force a sensor to read `magnitude_std` standard deviations above baseline
        for `duration_ticks` ticks. Used by scripted scenarios and stress tests."""
        self._active_anomalies[sensor_id] = {
            "remaining": duration_ticks,
            "magnitude": magnitude_std,
            "tag": tag,
        }

    def _advance_clock(self):
        self.sim_hour = (self.sim_hour + self.sim_seconds_per_tick / 3600.0) % 24.0
        self.tick_count += 1

    def _sample(self, spec: SensorSpec) -> Reading:
        shift = shift_for_hour(self.sim_hour)
        handover = is_handover_window(self.sim_hour)

        mean = spec.mean * _SHIFT_MEAN_MULT[shift]
        std = spec.std * _SHIFT_STD_MULT[shift]
        if handover:
            std *= _HANDOVER_STD_MULT

        # slow sinusoidal drift component for added realism (e.g. diurnal temperature)
        drift = 0.0
        if spec.sensor_type == SensorType.TEMPERATURE:
            drift = 2.0 * math.sin(2 * math.pi * (self.sim_hour / 24.0))

        value = self.rng.gauss(mean + drift, std)
        injected = False
        tag = None

        anomaly = self._active_anomalies.get(spec.sensor_id)
        if anomaly and anomaly["remaining"] > 0:
            value += anomaly["magnitude"] * spec.std
            injected = True
            tag = anomaly["tag"]
            anomaly["remaining"] -= 1
            if anomaly["remaining"] <= 0:
                del self._active_anomalies[spec.sensor_id]

        value = max(0.0, value)

        return Reading(
            timestamp=time.time(),
            sim_hour=round(self.sim_hour, 3),
            sensor_id=spec.sensor_id,
            zone_id=spec.zone_id,
            sensor_type=spec.sensor_type.value,
            value=round(value, 4),
            unit=spec.unit,
            shift=shift.value,
            handover=handover,
            injected_anomaly=injected,
            anomaly_tag=tag,
        )

    def tick(self) -> list[Reading]:
        self._advance_clock()
        return [self._sample(spec) for spec in ALL_SENSORS]


if __name__ == "__main__":
    gen = SensorStreamGenerator(start_sim_hour=13.9, seed=42)  # near handover
    gen.inject_anomaly("Z3-gas_h2s-0", magnitude_std=8, duration_ticks=3, tag="test_spike")
    for _ in range(5):
        readings = gen.tick()
        anomalous = [r for r in readings if r.injected_anomaly]
        print(f"hour={readings[0].sim_hour} shift={readings[0].shift} handover={readings[0].handover} anomalies={[ (r.sensor_id, r.value) for r in anomalous]}")
