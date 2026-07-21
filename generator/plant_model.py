"""
Plant layout definition: 9 zones with 2D coordinates, sensor inventory per zone,
and inherent zone risk classes (used by Agent 2 for context multipliers).
"""
from dataclasses import dataclass, field
from enum import Enum


class ZoneClass(str, Enum):
    CONFINED_SPACE = "confined_space"
    PROCESS_UNIT = "process_unit"
    STORAGE = "storage"
    UTILITY = "utility"
    CONTROL_ROOM = "control_room"


class SensorType(str, Enum):
    GAS_H2S = "gas_h2s"
    GAS_CH4 = "gas_ch4"
    PRESSURE = "pressure"
    TEMPERATURE = "temperature"
    VIBRATION = "vibration"


@dataclass
class SensorSpec:
    sensor_id: str
    sensor_type: SensorType
    zone_id: str
    # baseline distribution parameters (units vary by type)
    mean: float
    std: float
    unit: str


@dataclass
class ZoneSpec:
    zone_id: str
    name: str
    zone_class: ZoneClass
    x: float
    y: float
    base_risk_weight: float  # inherent hazard weight 0-1
    sensors: list = field(default_factory=list)


# 9-zone plant layout (2D coordinate grid, arbitrary plant-units)
ZONES: list[ZoneSpec] = [
    ZoneSpec("Z1", "Compressor House", ZoneClass.PROCESS_UNIT, 10, 80, 0.7),
    ZoneSpec("Z2", "Crude Storage Tank Farm", ZoneClass.STORAGE, 30, 80, 0.6),
    ZoneSpec("Z3", "Confined Vessel Bay", ZoneClass.CONFINED_SPACE, 50, 80, 0.9),
    ZoneSpec("Z4", "Reactor Unit A", ZoneClass.PROCESS_UNIT, 10, 50, 0.8),
    ZoneSpec("Z5", "Pipe Rack / Manifold", ZoneClass.UTILITY, 30, 50, 0.5),
    ZoneSpec("Z6", "Reactor Unit B", ZoneClass.PROCESS_UNIT, 50, 50, 0.8),
    ZoneSpec("Z7", "Cooling Water Plant", ZoneClass.UTILITY, 10, 20, 0.3),
    ZoneSpec("Z8", "Effluent Treatment", ZoneClass.UTILITY, 30, 20, 0.4),
    ZoneSpec("Z9", "Main Control Room", ZoneClass.CONTROL_ROOM, 50, 20, 0.2),
]

# Per-zone-class baseline sensor parameters: (mean, std, unit)
_BASELINES = {
    SensorType.GAS_H2S: (2.0, 0.6, "ppm"),
    SensorType.GAS_CH4: (8.0, 2.0, "%LEL"),
    SensorType.PRESSURE: (4.5, 0.3, "bar"),
    SensorType.TEMPERATURE: (45.0, 3.0, "C"),
    SensorType.VIBRATION: (1.2, 0.2, "mm/s"),
}

_SENSOR_PLAN = {
    ZoneClass.CONFINED_SPACE: [SensorType.GAS_H2S, SensorType.GAS_CH4, SensorType.TEMPERATURE],
    ZoneClass.PROCESS_UNIT: [SensorType.PRESSURE, SensorType.TEMPERATURE, SensorType.VIBRATION, SensorType.GAS_CH4],
    ZoneClass.STORAGE: [SensorType.GAS_CH4, SensorType.TEMPERATURE, SensorType.PRESSURE],
    ZoneClass.UTILITY: [SensorType.PRESSURE, SensorType.TEMPERATURE, SensorType.VIBRATION],
    ZoneClass.CONTROL_ROOM: [SensorType.TEMPERATURE],
}


def build_plant() -> list[ZoneSpec]:
    """Instantiate sensors for every zone according to its class's sensor plan."""
    for zone in ZONES:
        zone.sensors = []
        for i, stype in enumerate(_SENSOR_PLAN[zone.zone_class]):
            mean, std, unit = _BASELINES[stype]
            sid = f"{zone.zone_id}-{stype.value}-{i}"
            zone.sensors.append(SensorSpec(sid, stype, zone.zone_id, mean, std, unit))
    return ZONES


PLANT = build_plant()
ZONE_BY_ID = {z.zone_id: z for z in PLANT}
ALL_SENSORS = [s for z in PLANT for s in z.sensors]
SENSOR_BY_ID = {s.sensor_id: s for s in ALL_SENSORS}

if __name__ == "__main__":
    for z in PLANT:
        print(z.zone_id, z.name, z.zone_class.value, [s.sensor_id for s in z.sensors])
