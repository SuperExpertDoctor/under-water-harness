from dataclasses import asdict, dataclass, field
import math
import os


@dataclass(frozen=True)
class Config:
    width: float = 4000.0
    height: float = 4000.0
    cell: float = 100.0
    speed: float = 4.0
    radius: float = 60.0
    sensor_range: float = 350.0
    enemy_sensor_range: float = 700.0
    enemy_max_speed: float = 3.0
    enemy_turn_radius: float = 80.0
    range_capacity: float = 18000.0
    exit_reserve: float = 1200.0
    separation: float = 40.0
    dt: float = 0.2
    simulation_speed: float = 2.0
    scan_half_life_s: float = 180.0
    target_half_life_s: float = 60.0
    seed: int = 42
    model: str = field(default_factory=lambda: os.environ.get("LONGCAT_MODEL", "LongCat-2.0"))

    def __post_init__(self):
        for name in ("scan_half_life_s", "target_half_life_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")

    def public(self):
        return {"simulation": asdict(self), "permissions": {"assisted_risk_limit": 0.55},
                "retention": {"events": 5000, "frames_per_episode": 7200, "episodes": 8}}
