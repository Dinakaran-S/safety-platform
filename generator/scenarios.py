"""
Library of scripted compound-risk scenarios, replayable on demand, plus a
stress-test generator that fires hundreds of single-factor anomalies with
zero corroborating context to demonstrate the false-positive invariant live.
"""
from dataclasses import dataclass

from generator.context_generator import OperationalContextGenerator, PermitType, MaintenanceType
from generator.sensor_stream import SensorStreamGenerator


@dataclass
class ScenarioStep:
    description: str


@dataclass
class Scenario:
    scenario_id: str
    name: str
    description: str
    zone_id: str
    setup_fn: object  # callable(stream_gen, ctx_gen, start_hour)


def _scenario_fatal_pattern(stream_gen: SensorStreamGenerator, ctx_gen: OperationalContextGenerator, start_hour: float):
    """Re-creates the INC-088 pattern: hot work permit active in a confined
    space zone, gas anomaly building, shift handover approaching."""
    zone_id = "Z3"
    ctx_gen.issue_permit(zone_id, PermitType.HOT_WORK, at_hour=start_hour - 0.1, duration_hours=2.0)
    ctx_gen.issue_permit(zone_id, PermitType.CONFINED_SPACE_ENTRY, at_hour=start_hour - 0.05, duration_hours=1.5)
    gas_sensor = next(s for s in __import__("generator.plant_model", fromlist=["ZONE_BY_ID"]).ZONE_BY_ID[zone_id].sensors
                       if "gas_ch4" in s.sensor_id)
    stream_gen.inject_anomaly(gas_sensor.sensor_id, magnitude_std=6.5, duration_ticks=20, tag="scenario_fatal_pattern")


def _scenario_breakdown_vibration(stream_gen: SensorStreamGenerator, ctx_gen: OperationalContextGenerator, start_hour: float):
    """Compressor house: breakdown maintenance + vibration anomaly."""
    zone_id = "Z1"
    ctx_gen.start_maintenance(zone_id, MaintenanceType.BREAKDOWN, at_hour=start_hour - 0.2, duration_hours=2.0)
    from generator.plant_model import ZONE_BY_ID
    vib_sensor = next(s for s in ZONE_BY_ID[zone_id].sensors if "vibration" in s.sensor_id)
    stream_gen.inject_anomaly(vib_sensor.sensor_id, magnitude_std=5.5, duration_ticks=15, tag="scenario_breakdown_vibration")


def _scenario_excavation_pressure(stream_gen: SensorStreamGenerator, ctx_gen: OperationalContextGenerator, start_hour: float):
    """Pipe rack: excavation permit near a pressurized line + pressure transient."""
    zone_id = "Z5"
    ctx_gen.issue_permit(zone_id, PermitType.EXCAVATION, at_hour=start_hour - 0.15, duration_hours=2.5)
    from generator.plant_model import ZONE_BY_ID
    pressure_sensor = next(s for s in ZONE_BY_ID[zone_id].sensors if "pressure" in s.sensor_id)
    stream_gen.inject_anomaly(pressure_sensor.sensor_id, magnitude_std=5.0, duration_ticks=15, tag="scenario_excavation_pressure")


def _scenario_reactor_combo(stream_gen: SensorStreamGenerator, ctx_gen: OperationalContextGenerator, start_hour: float):
    """Reactor B: hot work + pressure/temperature co-anomaly (mirrors NM-175)."""
    zone_id = "Z6"
    ctx_gen.issue_permit(zone_id, PermitType.HOT_WORK, at_hour=start_hour - 0.1, duration_hours=2.0)
    from generator.plant_model import ZONE_BY_ID
    pressure_sensor = next(s for s in ZONE_BY_ID[zone_id].sensors if "pressure" in s.sensor_id)
    temp_sensor = next(s for s in ZONE_BY_ID[zone_id].sensors if "temperature" in s.sensor_id)
    stream_gen.inject_anomaly(pressure_sensor.sensor_id, magnitude_std=5.5, duration_ticks=15, tag="scenario_reactor_combo")
    stream_gen.inject_anomaly(temp_sensor.sensor_id, magnitude_std=4.5, duration_ticks=15, tag="scenario_reactor_combo")


SCENARIOS: dict[str, Scenario] = {
    "fatal_pattern": Scenario(
        "fatal_pattern", "Entrapped Gas + Hot Work + Handover (INC-088 pattern)",
        "Recreates the historical fatal-incident pattern: hot work and confined-space-entry "
        "permits active simultaneously in the confined vessel bay while a gas reading rises, "
        "timed near a shift handover.",
        "Z3", _scenario_fatal_pattern,
    ),
    "breakdown_vibration": Scenario(
        "breakdown_vibration", "Breakdown Maintenance + Vibration Spike",
        "Unplanned breakdown maintenance on the compressor coincides with a vibration anomaly.",
        "Z1", _scenario_breakdown_vibration,
    ),
    "excavation_pressure": Scenario(
        "excavation_pressure", "Excavation Near Live Line + Pressure Transient",
        "An excavation permit near the pipe rack coincides with a pressure transient on the manifold.",
        "Z5", _scenario_excavation_pressure,
    ),
    "reactor_combo": Scenario(
        "reactor_combo", "Hot Work + Reactor Pressure/Temperature Co-Anomaly",
        "Hot work permit active on Reactor Unit B while pressure and temperature both trend anomalously.",
        "Z6", _scenario_reactor_combo,
    ),
}


def stress_test_setup(stream_gen: SensorStreamGenerator, ctx_gen: OperationalContextGenerator, n_spikes: int = 50, seed: int = 0):
    """Fires `n_spikes` single-factor anomalies across random sensors with NO
    corroborating context, scheduled at staggered ticks. Returns nothing --
    mutates stream_gen's anomaly injection schedule via repeated calls at
    runtime (the API layer calls inject per-tick; see backend/orchestrator.py)."""
    import random
    from generator.plant_model import ALL_SENSORS

    rng = random.Random(seed)
    schedule = []
    for i in range(n_spikes):
        sensor = rng.choice(ALL_SENSORS)
        magnitude = rng.uniform(3.0, 9.0)
        # stagger over at most 15 ticks (was i*2 = 100 ticks = 60s for 50 spikes)
        tick_offset = (i * 15) // max(n_spikes - 1, 1)
        schedule.append({"tick_offset": tick_offset, "sensor_id": sensor.sensor_id, "magnitude": magnitude})
    return schedule
