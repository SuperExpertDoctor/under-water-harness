from dataclasses import asdict, dataclass, field
import os


@dataclass(frozen=True)
class Config:
    width: float = 4000.0
    height: float = 4000.0
    cell: float = 100.0
    speed: float = 4.0
    radius: float = 60.0
    sensor_range: float = 350.0
    range_capacity: float = 18000.0
    exit_reserve: float = 1200.0
    separation: float = 40.0
    dt: float = 0.2
    simulation_speed: float = 2.0
    seed: int = 42
    model: str = field(default_factory=lambda: os.environ.get("LONGCAT_MODEL", "LongCat-2.0"))

    def public(self):
        return {"simulation": asdict(self), "permissions": {"assisted_risk_limit": 0.55},
                "retention": {"events": 5000, "frames_per_episode": 7200, "episodes": 8}}
