from dataclasses import asdict, dataclass, field
from copy import deepcopy
import json
import math
import os
from pathlib import Path


_SETTINGS = json.loads((Path(__file__).resolve().parents[2] / "configs" / "uuv_game.json").read_text(encoding="utf-8"))
_SIMULATION = _SETTINGS["simulation"]


def algorithm_settings(section):
    return deepcopy(_SETTINGS["algorithms"][section])


@dataclass(frozen=True)
class Config:
    fleet_size: int = _SIMULATION["fleet_size"]
    width: float = _SIMULATION["width"]
    height: float = _SIMULATION["height"]
    cell: float = _SIMULATION["cell"]
    speed: float = _SIMULATION["speed"]
    radius: float = _SIMULATION["radius"]
    sensor_range: float = _SIMULATION["sensor_range"]
    side_scan_inner_range: float = _SIMULATION["side_scan_inner_range"]
    side_scan_half_angle_deg: float = _SIMULATION["side_scan_half_angle_deg"]
    forward_active_half_angle_deg: float = _SIMULATION["forward_active_half_angle_deg"]
    forward_passive_half_angle_deg: float = _SIMULATION["forward_passive_half_angle_deg"]
    coverage_window_min: int = _SIMULATION["coverage_window_min"]
    enemy_sensor_range: float = _SIMULATION["enemy_sensor_range"]
    enemy_max_speed: float = _SIMULATION["enemy_max_speed"]
    enemy_turn_radius: float = _SIMULATION["enemy_turn_radius"]
    range_capacity: float = _SIMULATION["range_capacity"]
    exit_reserve: float = _SIMULATION["exit_reserve"]
    separation: float = _SIMULATION["separation"]
    dt: float = _SIMULATION["dt"]
    simulation_speed: float = _SIMULATION["simulation_speed"]
    scan_half_life_s: float = _SIMULATION["scan_half_life_s"]
    target_half_life_s: float = _SIMULATION["target_half_life_s"]
    seed: int = _SIMULATION["seed"]
    model: str = field(default_factory=lambda: os.environ.get("LONGCAT_MODEL", _SIMULATION["model"]))

    def __post_init__(self):
        for name in ("scan_half_life_s", "target_half_life_s", "sensor_range", "side_scan_inner_range",
                     "side_scan_half_angle_deg", "forward_active_half_angle_deg", "forward_passive_half_angle_deg", "coverage_window_min"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.side_scan_inner_range >= self.sensor_range or self.side_scan_half_angle_deg >= 90 or self.forward_active_half_angle_deg >= 90 or self.forward_passive_half_angle_deg > 180:
            raise ValueError("invalid sonar geometry")

    def public(self):
        runtime = algorithm_settings("runtime")
        return {"simulation": asdict(self), "permissions": {"assisted_risk_limit": runtime["risk_approval_threshold"]},
                "retention": {"events": runtime["max_events"], "frames_per_episode": runtime["frames_per_episode"], "episodes": runtime["max_episodes"]}}
