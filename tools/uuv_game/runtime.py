import copy
import hashlib
import json
import math
import random
import threading
import time
import uuid
from functools import wraps
from contextlib import contextmanager

from .config import Config
from .store import Store, ReceiptLedger
from .algorithms.motion import integrate
from .algorithms.planning import plan_path, path_safe
from .algorithms.coverage import plan_search
from .algorithms.allocation import allocate_tasks
from .algorithms.tracking import tracking_control, follow_path, tracking_plan
from .algorithms.partition import partition_regions
from .algorithms.control import choose_controls
from .mission_planning import search_bundle, explicit_regions
from .lifecycle import prepare_exits, replacement_pose, apply_replacements
from .handover import prepare_handover, finish_handover
from .sensing import observe, sensor_mode
from .information import information_fields
from . import adversary as enemy


CHECKPOINT_FIELDS = tuple(("episode sim_time frame_id revision policy_version mode status uuvs targets obstacles active results plans contacts observations events cursor scan_times intents vessels tasks messages agent_jobs agent last_periodic sensor_enabled contact_mapping contact_counter last_observation_time regions standing_policy metrics region_revision observation_cursor adversary").split())
ALGORITHM_IDS = {"compute_task_allocation": "slot_assignment", "plan_path": "dubins_hybrid",
                 "plan_search": "strip_coverage", "plan_tracking": "distance_band", "partition_search_area": "connected_partition"}


class MissionError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code = code
        self.status = status


def identifier(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def synchronized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return locked


class MissionRuntime:
    def __init__(self, path, config=None):
        self.config = config or Config()
        self.store = Store(path)
        self.lock = threading.RLock()
        self.rng = random.Random(self.config.seed)
        self.last_heartbeat = 0.0
        self._initial()
        saved = self.store.load()
        if saved:
            for key, value in saved.items():
                if key == "receipts":
                    self.receipts.update(value)
                elif key != "rng":
                    setattr(self, key, value)
            state = saved.get("rng")
            if state:
                self.rng.setstate((state[0], tuple(state[1]), state[2]))
            self.agent["status"] = "offline"
            self.adversary.update(job=None, parameters=None, status="offline", last_heartbeat=0.0, last_started_wall=0.0)
            # Preserve the human mission and primary target, remove the old scene/AIS bypass.
            self.targets = [next((target for target in self.targets if target["id"] == "TARGET-1"), self.targets[0])] if self.targets else [{"id": "TARGET-1", "pose": [900.0, 700.0, .5], "speed": 2.5}]
            for target in self.targets:
                target["ais_enabled"] = False
            self.vessels = []
            removed_contacts = {contact for target, contact in self.contact_mapping.items() if target != self.targets[0]["id"]}
            retired_members = [member for member, action in self.active.items()
                if action.get("kind") == "track" and action.get("contact_id") in removed_contacts]
            for member in retired_members:
                self.active.pop(member)
            retired_plans = [plan for plan in self.plans.values() if plan.get("kind") == "track"
                and plan.get("contact_id") in removed_contacts and plan["status"] in ("active", "pending_approval", "approved")]
            for plan in retired_plans:
                plan.update(status="superseded", active_members=[], migration_reason="single_target_scene_locked")
            if retired_members or retired_plans:
                self.revision += 1
                self.event("single_target_checkpoint_migrated", {"retired_tracking_actions": len(retired_members),
                    "retired_tracking_plans": len(retired_plans), "reason": "single_target_scene_locked"})
            self.contact_mapping = {target: contact for target, contact in self.contact_mapping.items() if target == self.targets[0]["id"]}
            self.contacts = {key: value for key, value in self.contacts.items() if key not in removed_contacts}
            self.observations = [sample for sample in self.observations if sample.get("contact_id") not in removed_contacts and sample.get("source") != "ais"]
            for contact in self.contacts.values():
                contact["samples"] = [sample for sample in contact["samples"] if sample.get("source") != "ais"]
            self.contact_counter = max(self.contact_counter, max((int(key.split("-")[-1]) for key in self.contacts if key.startswith("CONTACT-") and key.split("-")[-1].isdigit()), default=0))
            self.agent["model"] = self.config.model
            for job in self.agent_jobs:
                if job["status"] == "running":
                    job["status"] = "failed"
                    job["error"] = "worker_restarted"
            if "handoff_attempts" not in self.metrics:
                complete_history = len(self.events) == self.cursor and all(
                    event["id"] == index+1 and event["episode_id"] == self.episode for index, event in enumerate(self.events))
                self.metrics["handoff_attempts"] = sum(event["type"] == "tracking_handoff_started" for event in self.events) if complete_history else None
            self.event("runtime_restored", {})
            if any("generation" not in u for u in self.uuvs):
                for u in self.uuvs:
                    u.update(generation=1, remaining_range_m=self.config.range_capacity, capabilities=["active", "passive"])
                self.active = {}
                self.contacts = {}
                self.observations = []
                for plan in self.plans.values():
                    if plan["status"] in ("active", "pending_approval"):
                        plan["status"] = "superseded"
                self.status = "paused"
                self.event("v2_checkpoint_migrated", {"reason": "new_observation_and_energy_contract_requires_new_plans"})

    def _initial(self):
        self.episode = identifier("mission")
        self.sim_time = 0.0
        self.frame_id = 0
        self.revision = 0
        self.policy_version = 0
        self.mode = "assisted"
        self.status = "ready"
        self.uuvs = [{"id": f"UUV-{i+1}", "pose": [400.0+2600*(i//4), 400.0+(i%4)*1000, 0.0], "trail": [], "curvature": 0.0,
            "generation": 1, "remaining_range_m": self.config.range_capacity, "capabilities": ["active", "passive"]} for i in range(8)]
        self.targets = [{"id": "TARGET-1", "pose": [900.0, 700.0, 0.5], "speed": 2.5}]
        self.adversary = enemy.initial_state()
        self.obstacles = [{"x": 2500.0, "y": 2300.0, "radius": 140.0}]
        self.active = {}
        self.results = {}
        self.plans = {}
        self.receipts = ReceiptLedger(self.store)
        self.contacts = {}
        self.observations = []
        self.events = []
        self.cursor = 0
        self.scan_times = [[-1.0]*40 for _ in range(40)]
        self.intents = []
        self.vessels = []
        self.tasks = []
        self.messages = []
        self.agent_jobs = []
        self.agent = {"status": "offline", "model": self.config.model, "cycle": 0, "error": None}
        self.last_periodic = 0.0
        self.sensor_enabled = True
        self.contact_mapping = {}
        self.contact_counter = 0
        self.observation_cursor = 0
        self.last_observation_time = -1.0
        self.regions = []
        self.region_revision = 0
        self.standing_policy = {"enabled": False, "energy_rotation": False, "local_repair": False, "lost_reacquire": False}
        self.metrics = {"effective_tracking_seconds": 0.0, "lost_seconds": 0.0, "handoff_count": 0, "handoff_attempts": 0, "rotation_count": 0}

    @synchronized
    def save(self):
        terminal = [p for p in self.plans.values() if p["status"] not in ("active", "pending_approval")]
        for plan in terminal[:-32]:
            self.store.archive_plan(plan)
            self.plans.pop(plan["plan_id"])
        self.store.save({**{key: getattr(self, key) for key in CHECKPOINT_FIELDS}, "rng": self.rng.getstate()})

    @contextmanager
    def transaction(self):
        with self.lock:
            before = copy.deepcopy({key: getattr(self, key) for key in CHECKPOINT_FIELDS})
            rng_state = self.rng.getstate()
            try:
                with self.store.transaction():
                    yield
            except BaseException:
                for key, value in before.items():
                    setattr(self, key, value)
                self.rng.setstate(rng_state)
                raise

    def close(self):
        with self.lock:
            self.save()
            self.store.close()

    @synchronized
    def event(self, kind, data):
        self.cursor += 1
        event = {"id": self.cursor, "episode_id": self.episode, "time": self.sim_time/60, "type": kind, "data": data}
        self.events.append(event)
        self.events = self.events[-5000:]
        return event

    def check_episode(self, episode):
        if episode != self.episode:
            raise MissionError("stale_episode")

    @synchronized
    def start(self):
        if self.status == "stopped":
            raise MissionError("reset_required_after_stop")
        self.status = "running"
        self.event("simulation_started", {})
        self.save()

    @synchronized
    def pause(self, reason="human_pause"):
        if self.status == "stopped":
            raise MissionError("mission_stopped")
        self.status = "safety_paused" if reason != "human_pause" else "paused"
        self.event(reason, {})
        self.save()

    @synchronized
    def stop(self):
        self.status = "stopped"
        self.adversary.update(job=None, parameters=None, status="idle")
        self.revision += 1
        self.active = {}
        for plan in self.plans.values():
            if plan["status"] in ("active", "pending_approval"):
                plan["status"] = "cancelled"
        self.cancel_agent()
        self.event("mission_stopped", {})
        self.save()

    @synchronized
    def reset(self):
        for plan in self.plans.values():
            self.store.archive_plan({**plan, "status": "cancelled" if plan["status"] in ("active", "pending_approval") else plan["status"]})
        self._initial()
        self.rng = random.Random(self.config.seed)
        self.event("environment_reset", {})
        self.save()

    @synchronized
    def set_mode(self, mode):
        if mode not in ("request", "assisted", "full"):
            raise MissionError("invalid_mode", 422)
        self.mode = mode
        self.policy_version += 1
        for plan in self.plans.values():
            if plan["status"] == "pending_approval":
                plan["status"] = "expired"
        self.event("permission_changed", {"mode": mode})
        self.save()

    @synchronized
    def mission_state(self):
        return copy.deepcopy({"episode_id": self.episode, "snapshot_id": self.frame_id,
            "mission_revision": self.revision, "sim_time_s": self.sim_time, "autonomy_mode": self.mode,
            "runtime_status": self.status, "uuvs": [{"id": u["id"], "pose": u["pose"], "generation": u["generation"],
                "remaining_range_m": u["remaining_range_m"], "capabilities": u["capabilities"],
                "sensor_mode": sensor_mode(self.active.get(u["id"], {})),
                "phase": self.active.get(u["id"], {}).get("phase", "idle"), "task": self.active.get(u["id"], {}).get("kind", "idle")} for u in self.uuvs],
            "contacts": list(self.contacts.values()), "bounds": [0, 0, 4000, 4000], "obstacles": self.obstacles,
            "active_plans": [self.summary(p) for p in self.plans.values() if p["status"] in ("active", "pending_approval")],
            "intents": [{**intent, "bbox": [intent["bbox"][0]*100, 4000-intent["bbox"][3]*100,
                intent["bbox"][2]*100, 4000-intent["bbox"][1]*100]} for intent in self.intents],
            "search_regions": [{k: v for k, v in r.items() if k not in ("cells", "scan_cells")} for r in self.regions], "standing_policy": self.standing_policy,
            "metrics": self.metrics, "region_revision": self.region_revision,
            "constraints": {"max_team_size": 3, "min_turn_radius_m": 60, "speed_mps": 4}})

    @staticmethod
    def summary(result):
        summary = {key: value for key, value in result.items() if key not in ("routes", "points", "start_poses", "coverage_repair")}
        if "regions" in summary:
            summary["regions"] = [{k: v for k, v in r.items() if k not in ("cells", "scan_cells")} for r in summary["regions"]]
        return summary

    def _members(self, data, maximum=3):
        members = data.get("members", ["UUV-1", "UUV-2", "UUV-3"])
        if not isinstance(members, list) or not 1 <= len(members) <= maximum or not all(isinstance(member, str) for member in members) or len(set(members)) != len(members):
            raise MissionError("team_size_or_duplicate", 422)
        selected = [copy.deepcopy(u) for u in self.uuvs if u["id"] in members]
        if len(selected) != len(members):
            raise MissionError("unknown_uuv", 422)
        return selected

    def calculate(self, name, data):
        with self.lock:
            if data.get("episode_id"):
                self.check_episode(data["episode_id"])
            if data.get("mission_revision", self.revision) != self.revision:
                raise MissionError("stale_mission")
            if data.get("algorithm_id", "default") not in ("default", ALGORITHM_IDS.get(name)):
                raise MissionError("unknown_algorithm", 422)
            if name == "plan_search" and "bbox" in data and "members" not in data:
                raise MissionError("bbox_requires_explicit_members", 422)
            episode, revision = self.episode, self.revision
            automatic = name in ("compute_task_allocation", "partition_search_area", "plan_search", "plan_tracking") and "members" not in data
            global_coverage = automatic and (name in ("partition_search_area", "plan_search") or
                (name == "compute_task_allocation" and "tasks" not in data and not data.get("contact_id")))
            members = copy.deepcopy([u for u in self.uuvs if self.active.get(u["id"], {}).get("kind") != "exit"]) if automatic else self._members(data, 8 if name in ("plan_search", "partition_search_area", "compute_task_allocation") else 3)
            if automatic and name != "plan_tracking":
                members = [u for u in members if self.active.get(u["id"], {}).get("kind") != "track"]
            if global_coverage:
                members = [u for u in members if "active" in u["capabilities"]
                    and self.active.get(u["id"], {}).get("kind", "idle") in ("idle", "search", "reacquire")
                    and u["remaining_range_m"] > min(u["pose"][0], u["pose"][1], 4000-u["pose"][0], 4000-u["pose"][1])+self.config.exit_reserve+500]
            if name == "plan_tracking":
                members = [u for u in members if self.active.get(u["id"], {}).get("kind") != "track" or self.active[u["id"]].get("contact_id") == data.get("contact_id")]
                for boat in members:
                    region = next((r for r in self.regions if r["owner"] == boat["id"]), None)
                    boat["search_workload"] = sum(self.scan_times[c][r] < 0 for c, r in region["cells"])/max(1, len(region["cells"])) if region else 0
            obstacles = copy.deepcopy(self.obstacles)
            ownership_obstacles = copy.deepcopy(obstacles)
            selected_ids = {u["id"] for u in members}
            obstacles += [{"x": u["pose"][0], "y": u["pose"][1], "radius": self.config.separation+12}
                          for u in self.uuvs if u["id"] not in selected_ids and u["id"] not in self.active]
            contacts = copy.deepcopy(self.contacts)
            snapshot = self.frame_id
            assignments = {u["id"]: self.active.get(u["id"], {}).get("plan_id") for u in members}
            scan_times = copy.deepcopy(self.scan_times)
            regions = copy.deepcopy(self.regions)
            all_boats = copy.deepcopy(self.uuvs)
            old_actions = copy.deepcopy(self.active)
        bbox = data.get("bbox", [250, 250, 3750, 3750])
        if name == "compute_task_allocation" and data.get("contact_id"):
            contact = contacts.get(data["contact_id"])
            if not contact or contact["state"] in ("tentative", "lost"):
                raise MissionError("confirmed_contact_required", 422)
            result = tracking_plan(members, contact, obstacles)
            chosen = result.get("members", [])
            members = [boat for boat in members if boat["id"] in chosen]
            result.pop("routes", None)
            result.update(kind="allocation", algorithm="cooperative-geometry-allocation-v2", contact_id=data["contact_id"],
                teams=[{"id": f"candidate-{data['contact_id']}", "task_id": data["contact_id"], "members": chosen}] if chosen else [])
        elif name == "partition_search_area" or (name == "compute_task_allocation" and "tasks" not in data):
            result = partition_regions(members, scan_times, ownership_obstacles if global_coverage else obstacles, regions)
            result["algorithm"] = "connected-workload-assignment-v2"
            result["teams"] = [{"id": r["id"], "task_id": r["id"], "members": [r["owner"]], "bbox": r["bbox_m"]} for r in result["regions"]]
        elif name == "compute_task_allocation":
            tasks = data.get("tasks", [{"id": f"SEARCH-{i+1}", "center": [2000, 800+i*1200], "size": size, "priority": 5,
                "bbox": [600, 250+i*1200, 3750, 1350+i*1200]} for i, size in enumerate([3, 3, 2])])
            result = allocate_tasks(members, tasks)
            task_domains = {task["id"]: task["bbox"] for task in tasks if "bbox" in task}
            for team in result["teams"]:
                if team["task_id"] in task_domains:
                    team["bbox"] = copy.deepcopy(task_domains[team["task_id"]])
        elif name == "plan_path":
            if len(members) != 1:
                raise MissionError("path_requires_one_uuv", 422)
            result = plan_path(members[0]["pose"], data.get("goal", [1800, 1000, 0]), obstacles=obstacles)
            result["routes"] = {members[0]["id"]: result.get("points", [])}
            result["kind"] = "path"
        elif name == "plan_search":
            if "standing_policy" in data and not isinstance(data["standing_policy"], bool):
                raise MissionError("boolean_required", 422)
            result = search_bundle(members, scan_times, ownership_obstacles, regions,
                allow_partial=data.get("standing_policy", automatic) or self.standing_policy["energy_rotation"],
                route_obstacles=obstacles) if automatic else plan_search(members, bbox, obstacles=obstacles)
            result["kind"] = "reacquire" if data.get("mode") == "reacquire" else "search"
            if not automatic:
                result["bbox"] = bbox
                result["fleet_plan"] = len(members) > 3
                result["regions"] = explicit_regions(members, bbox, ownership_obstacles, regions)
                result["partial_region_update"] = True
                if result["regions"] is None:
                    result.update(status="infeasible", regions=[], diagnostics={"reason": "explicit_region_empty_or_disconnected_due_to_existing_ownership"})
            result["standing_policy"] = bool(data.get("standing_policy", automatic))
        elif name == "plan_tracking":
            contact_id = data.get("contact_id")
            contact = contacts.get(contact_id)
            if not contact or contact["state"] in ("tentative", "lost"):
                raise MissionError("confirmed_contact_required", 422)
            result = tracking_plan(members, contact, obstacles)
            chosen = result.get("members", [])
            members = [u for u in members if u["id"] in chosen]
            result.update(kind="track", contact_id=contact_id, bbox=[0, 0, 4000, 4000])
            if result["status"] == "succeeded" and self.standing_policy["enabled"]:
                remaining = [u for u in all_boats if u["id"] not in chosen and old_actions.get(u["id"], {}).get("kind") in ("search", "reacquire")]
                repair = search_bundle(remaining, scan_times, obstacles, regions, allow_partial=self.standing_policy["energy_rotation"])
                result["coverage_repair"] = repair
                if repair["status"] != "succeeded":
                    result.update(status=repair["status"], diagnostics={"reason": "coverage_repair_failed", "details": repair["diagnostics"]})
                result["repair_assignments"] = {u["id"]: old_actions[u["id"]]["plan_id"] for u in remaining}
                result["repair_generations"] = {u["id"]: u["generation"] for u in remaining}
        else:
            raise MissionError("unknown_calculation", 404)
        if global_coverage and not members:
            result.update(status="infeasible", regions=[])
            result["diagnostics"]["reason"] = "no_eligible_coverage_vehicles"
        result.update({"result_id": identifier("result"), "episode_id": episode, "mission_revision": revision,
            "snapshot_id": snapshot, "members": [u["id"] for u in members], "created_at_s": self.sim_time,
            "start_poses": {u["id"]: u["pose"] for u in members}, "assignments": assignments,
            "generations": {u["id"]: u["generation"] for u in members}})
        affected = set(result["members"]) | set(result.get("repair_assignments", {}))
        if result.get("fleet_plan") and not result.get("partial_region_update"):
            affected.update(region["owner"] for region in regions)
        result["region_signatures"] = {member: self.region_signature(next((r for r in regions if r["owner"] == member), None)) for member in affected}
        points = [point for route in result.get("routes", {}).values() for point in route]
        result["execution_domain"] = [0, 0, 4000, 4000] if result.get("kind") == "track" or result.get("fleet_plan") else [max(0, min(p[0] for p in points)-100), max(0, min(p[1] for p in points)-100),
            min(4000, max(p[0] for p in points)+100), min(4000, max(p[1] for p in points)+100)] if points else [0, 0, 4000, 4000]
        result["domain_policy"] = "route_envelope_including_transit" if points else "map_wide_tracking"
        with self.lock:
            self.check_episode(episode)
            self.results[result["result_id"]] = result
            while len(self.results) > 100:
                self.results.pop(next(iter(self.results)))
            self.event("tool_result", {"tool": name, "result_id": result["result_id"], "status": result["status"]})
            self.save()
        return self.summary(result)

    @synchronized
    def evaluate(self, result_id):
        result = self.results.get(result_id)
        if not result:
            raise MissionError("result_not_found", 404)
        return self._assessment(result)

    def _assessment(self, result):
        errors = []
        if result["episode_id"] != self.episode:
            errors.append("stale_mission")
        if result["status"] not in ("succeeded", "pending_approval"):
            errors.append("calculation_not_successful")
        members = result["members"]
        if any(self.region_signature(next((r for r in self.regions if r["owner"] == member), None)) != signature for member, signature in result.get("region_signatures", {}).items()):
            errors.append("region_assignment_changed")
        if any(u["generation"] != result.get("generations", {}).get(u["id"], u["generation"]) for u in self.uuvs if u["id"] in members):
            errors.append("generation_changed")
        if any(self.active.get(member, {}).get("plan_id") != result.get("assignments", {}).get(member) for member in members):
            errors.append("member_assignment_changed")
        if not 1 <= len(members) <= (8 if result.get("fleet_plan") else 3) or len(set(members)) != len(members):
            errors.append("invalid_team")
        for u in self.uuvs:
            if u["id"] in result.get("repair_assignments", {}) and (self.active.get(u["id"], {}).get("plan_id") != result["repair_assignments"][u["id"]] or u["generation"] != result["repair_generations"][u["id"]]):
                errors.append("coverage_assignment_changed")
        if result.get("kind") not in ("search", "reacquire", "track", "path"):
            errors.append("not_executable")
        if result.get("requires_energy_rotation") and not (result.get("standing_policy") or self.standing_policy["energy_rotation"]):
            errors.append("energy_rotation_authorization_required")
        if result.get("partial_region_update"):
            occupied = {tuple(cell) for region in self.regions if region["owner"] not in members for cell in region["cells"]}
            if any(tuple(cell) in occupied for region in result.get("regions", []) for cell in region["cells"]):
                errors.append("region_ownership_changed")
        for member, points in result.get("routes", {}).items():
            cycle = result.get("cycle_start_indices", {}).get(member, 0)
            if not points or not path_safe(points, self.obstacles, result.get("execution_domain", (0, 0, 4000, 4000))):
                errors.append("unsafe_route")
            elif result.get("kind") in ("search", "reacquire") and (not 0 <= cycle < len(points) or math.dist(points[cycle][:2], points[-1][:2]) > 1 or abs(math.remainder(points[cycle][2]-points[-1][2], 2*math.pi)) > .05):
                errors.append("closed_continuation_required")
        if result.get("kind") == "path":
            errors.append("terminal_continuation_required_use_search")
        for u in self.uuvs:
            if u["id"] in members and math.dist(u["pose"][:2], result["start_poses"][u["id"]][:2]) > 200:
                errors.append("start_pose_changed_replan")
            if u["id"] in members:
                x, y = u["pose"][:2]
                if u["remaining_range_m"] < min(x, y, 4000-x, 4000-y)+self.config.exit_reserve+500:
                    errors.append("insufficient_energy")
                capability = "passive" if result.get("kind") == "track" else "active"
                if capability not in u["capabilities"]:
                    errors.append("missing_sensor_capability")
        if result.get("kind") == "track" and self.contacts.get(result.get("contact_id"), {}).get("state") in (None, "lost"):
            errors.append("contact_lost")
        if result.get("kind") == "track":
            existing = {member for member, action in self.active.items() if action["kind"] == "track" and action.get("contact_id") == result.get("contact_id")}
            if len(existing | set(members)) > 3:
                errors.append("tracking_handover_team_limit")
            passive_established = self.contacts.get(result.get("contact_id"), {}).get("state") == "tracking" and sum(
                self.active[member].get("phase") == "tracking" and sensor_mode(self.active[member]) == "passive" for member in existing-set(members)) >= 2
            if result.get("acquisition_mode") == "active_until_cooperative_geometry" and not passive_established and sum(
                    boat["id"] in members and "active" in boat["capabilities"] for boat in self.uuvs) < 2:
                errors.append("active_acquisition_requires_two_active_sensors")
        energy_risk = max((1-u["remaining_range_m"]/self.config.range_capacity for u in self.uuvs if u["id"] in members), default=0)
        uncertainty = self.contacts.get(result.get("contact_id"), {}).get("uncertainty_m", 0)
        risk = min(1, (.6 if result.get("kind") in ("track", "reacquire") else .2) + .2*energy_risk + min(.2, uncertainty/1000))
        return {"result_id": result["result_id"], "valid": not errors, "errors": sorted(set(errors)), "risk": risk,
            "requires_approval": self.mode == "request" or (self.mode == "assisted" and risk > 0.55)}

    @staticmethod
    def region_signature(region):
        if region is None:
            return None
        return hashlib.sha256(json.dumps([region["id"], sorted(region["cells"])]).encode()).hexdigest()

    def submit(self, result_id, command_id, episode):
        with self.transaction():
            self.check_episode(episode)
            digest = hashlib.sha256(json.dumps([result_id, episode]).encode()).hexdigest()
            if command_id in self.receipts:
                prior = self.receipts[command_id]
                if prior["digest"] != digest:
                    raise MissionError("idempotency_conflict")
                return copy.deepcopy(prior["response"])
            if self.status == "stopped":
                raise MissionError("mission_stopped")
            if sum(p["status"] == "pending_approval" for p in self.plans.values()) >= 32:
                raise MissionError("approval_queue_full", 429)
            assessment = self.evaluate(result_id)
            if not assessment["valid"]:
                raise MissionError(";".join(assessment["errors"]))
            result = copy.deepcopy(self.results[result_id])
            plan = {**result, "plan_id": identifier("plan"), "status": "pending_approval" if assessment["requires_approval"] else "approved",
                "risk": assessment["risk"], "policy_version": self.policy_version, "expires_at_s": self.sim_time+600,
                "fallback": "repeat_approved_closed_route" if result["kind"] in ("search", "reacquire") else "authorized_active_reacquisition" if self.standing_policy["lost_reacquire"] else "protective_pause",
                "approval_timeout_behavior": "expire_pending_only_continue_existing_authorized_tasks",
                "reason": "human_required" if self.mode == "request" else "risk_threshold"}
            self.plans[plan["plan_id"]] = plan
            if plan["status"] == "approved":
                self._activate(plan)
            else:
                self.event("approval_requested", {"plan_id": plan["plan_id"], "members": plan["members"], "risk": plan["risk"]})
            response = self.summary(plan)
            self.receipts[command_id] = {"digest": digest, "response": copy.deepcopy(response)}
            self.save()
            return response

    @synchronized
    def _activate(self, plan):
        passive_established = self.contacts.get(plan.get("contact_id"), {}).get("state") == "tracking" and sum(
            action.get("kind") == "track" and action.get("contact_id") == plan.get("contact_id")
            and action.get("phase") == "tracking" and sensor_mode(action) == "passive"
            for member, action in self.active.items() if member not in plan["members"]) >= 2
        for old in self.plans.values():
            if old["plan_id"] != plan["plan_id"] and old["status"] == "active" and set(old["members"]) & set(plan["members"]):
                remaining = [member for member in old["members"] if member not in plan["members"] and self.active.get(member, {}).get("plan_id") == old["plan_id"]]
                old["active_members"] = remaining
                if not remaining:
                    old["status"] = "superseded"
        for index, member in enumerate(plan["members"]):
            self.active[member] = {"plan_id": plan["plan_id"], "kind": plan["kind"], "contact_id": plan.get("contact_id"),
                "points": plan.get("routes", {}).get(member, []), "index": 0, "slot": index,
                "cycle_start_index": plan.get("cycle_start_indices", {}).get(member, 0),
                "phase": "transit" if plan["kind"] == "track" else "scanning", "generation": next(u["generation"] for u in self.uuvs if u["id"] == member)}
            self.active[member]["execution_domain"] = plan["execution_domain"]
            if plan["kind"] == "track" and plan.get("acquisition_mode") == "active_until_cooperative_geometry":
                active_capable = "active" in next(boat["capabilities"] for boat in self.uuvs if boat["id"] == member)
                self.active[member]["acquisition_mode"] = "passive" if passive_established or not active_capable else "active"
        if plan.get("standing_policy"):
            self.standing_policy = {"enabled": True, "energy_rotation": True, "local_repair": True, "lost_reacquire": True, "plan_id": plan["plan_id"]}
        if plan.get("regions"):
            unchanged = [r for r in self.regions if r["owner"] not in plan["members"]] if plan.get("partial_region_update") else []
            self.regions = unchanged + copy.deepcopy(plan["regions"])
            self.region_revision += 1
        elif plan["kind"] in ("search", "reacquire"):
            self.regions = [r for r in self.regions if r["owner"] not in plan["members"]]
            owned = {tuple(cell) for r in self.regions for cell in r["cells"]}
            x0, y0, x1, y1 = plan["bbox"]
            width = (x1-x0)/len(plan["members"])
            for index, member in enumerate(plan["members"]):
                bounds = [x0+index*width, y0, x0+(index+1)*width, y1]
                cells = [[c, r] for c in range(40) for r in range(40) if (c, r) not in owned
                         and bounds[0] <= c*100+50 < bounds[2] and y0 <= 3950-r*100 < y1
                         and path_safe([[c*100+50, 3950-r*100, 0]], self.obstacles, (0, 0, 4000, 4000), margin=80)]
                self.regions.append({"id": f"region-{member}", "owner": member, "bbox_m": bounds, "cells": cells})
                owned.update(map(tuple, cells))
            self.region_revision += 1
        elif plan["kind"] == "track":
            self.regions = [r for r in self.regions if r["owner"] not in plan["members"]]
            repair = plan.get("coverage_repair")
            if repair:
                self.regions = copy.deepcopy(repair["regions"])
                for member, points in repair["routes"].items():
                    self.active[member].update(points=points, index=0, execution_domain=[0, 0, 4000, 4000], cycle_start_index=repair.get("cycle_start_indices", {}).get(member, 0))
                self.region_revision += 1
                self.event("coverage_repartitioned", {"regions": len(self.regions), "reason": "tracking_assignment"})
        plan["status"] = "active"
        plan["active_members"] = list(plan["members"])
        self.revision += 1
        self.event("mission_assignment_committed", {"plan_id": plan["plan_id"], "members": plan["members"]})

    @synchronized
    def decide(self, plan_id, approve):
        plan = self.plans.get(plan_id)
        if not plan:
            raise MissionError("plan_not_found", 404)
        if plan["status"] != "pending_approval":
            raise MissionError("approval_not_pending")
        if plan["policy_version"] != self.policy_version or self.sim_time > plan["expires_at_s"]:
            plan["status"] = "expired"
            self.save()
            raise MissionError("approval_expired")
        if approve:
            assessment = self._assessment(plan)
            if not assessment["valid"]:
                plan["status"] = "expired"
                self.save()
                raise MissionError(";".join(assessment["errors"]))
            self._activate(plan)
        else:
            plan["status"] = "rejected"
        self.event("approval_decided", {"plan_id": plan_id, "status": plan["status"]})
        self.save()
        return self.summary(plan)

    @synchronized
    def queue_agent(self, text, source="human", delivery="followUp", annotation=None):
        if self.status == "stopped":
            raise MissionError("mission_stopped")
        priority = source in ("human", "feedback")
        material_sources = ("target_found", "target_lost", "energy_exit")
        if not priority:
            queued = next((job for job in self.agent_jobs if job["status"] == "queued"
                and job["source"] not in ("human", "feedback")
                and (job["source"] in material_sources) == (source in material_sources)), None)
            if queued:
                with self.transaction():
                    queued.update(text=text[:4000], source=source, updated_at_s=self.sim_time,
                        coalesced_count=queued.get("coalesced_count", 0)+1)
                    self.event("agent_wakeup_coalesced", {"run_id": queued["run_id"], "source": source,
                        "text": queued["text"], "coalesced_count": queued["coalesced_count"]})
                    self.save()
                return queued
        running = next((j for j in self.agent_jobs if j["status"] == "running"), None)
        if sum(j["status"] in ("queued", "running") for j in self.agent_jobs) >= 12:
            if not priority:
                self.event("agent_wakeup_deferred", {"source": source, "reason": "queue_full"})
                return None
            if not (source == "human" and running):
                raise MissionError("agent_queue_full", 429)
        if source == "human" and running:
            if len(running.get("feedback", []))+len(running.get("accepted_feedback", [])) >= 20:
                raise MissionError("feedback_queue_full", 429)
            running.setdefault("feedback", []).append({"id": identifier("feedback"), "text": text[:4000], "delivery": delivery})
            job = running
        else:
            job = {"run_id": identifier("run"), "episode_id": self.episode, "status": "queued", "text": text[:4000], "source": source, "feedback": []}
            self.agent_jobs.append(job)
        self.agent_jobs = self.agent_jobs[-100:]
        if source == "human":
            self.messages.append({"id": identifier("message"), "role": "user", "text": text[:4000], "time": self.sim_time,
                "status": "queued" if running else "completed", "run_id": job["run_id"], "delivery": delivery, "annotation": annotation,
                "feedback_id": running["feedback"][-1]["id"] if running else None})
            self.messages = self.messages[-100:]
        self.event("agent_queued", {"run_id": job["run_id"], "source": source})
        self.save()
        return job

    @synchronized
    def cancel_agent(self, run_id=None):
        cancelled = set()
        for job in self.agent_jobs:
            if (not run_id or job["run_id"] == run_id) and job["status"] in ("queued", "running"):
                job["status"] = "cancelled"
                job["feedback"] = []
                job["accepted_feedback"] = []
                cancelled.add(job["run_id"])
        for message in self.messages:
            if message.get("run_id") in cancelled and message.get("status") in ("queued", "streaming"):
                message["status"] = "cancelled"
        self.agent["status"] = "idle"
        self.save()

    def _observe(self):
        observe(self)

    @synchronized
    def adversary_observation(self):
        target = self.targets[0]
        return copy.deepcopy({"episode_id": self.episode, "sim_time_s": self.sim_time,
            "own_pose": target["pose"], "own_speed_mps": target["speed"],
            "sensor_range_m": self.config.enemy_sensor_range,
            "bounds": [0, 0, self.config.width, self.config.height], "obstacles": self.obstacles,
            "detections": self.adversary["detections"], "history": self.adversary["history"]})

    @synchronized
    def tick(self):
        if self.status != "running":
            return
        enemy_job = self.adversary["job"]
        if enemy_job and enemy_job["status"] == "running" and enemy_job["lease_deadline"] <= time.monotonic():
            enemy_job["status"] = "failed"
            self.adversary.update(parameters=None, status="degraded")
        if self.frame_id % 25 == 0:
            prepare_handover(self)
            if not prepare_exits(self):
                return
        next_poses = {}
        controls = {}
        for u in self.uuvs:
            action = self.active.get(u["id"])
            if not action:
                next_poses[u["id"]] = u["pose"]
                continue
            if action.get("generation", u["generation"]) != u["generation"]:
                self.pause("safety_stale_generation")
                return
            pose = u["pose"]
            if action["kind"] == "track":
                estimate = self.contacts.get(action.get("contact_id"))
                if not estimate or (estimate["state"] == "lost" and not self.standing_policy["lost_reacquire"]):
                    self.pause("safety_tracking_contact_lost")
                    return
                if estimate["state"] == "lost":
                    action["phase"] = "reacquiring"
                    action["acquisition_mode"] = "active"
                elif action.get("phase") == "reacquiring":
                    action["phase"] = "acquiring"
                teammates = [b for b in sorted(self.uuvs, key=lambda b: (self.active.get(b["id"], {}).get("slot", 0), b["id"]))
                    if self.active.get(b["id"], {}).get("kind") == "track" and self.active[b["id"]].get("contact_id") == action["contact_id"]
                    and (action.get("phase") == "transit" or self.active[b["id"]].get("phase") != "transit")]
                slot = next(i for i, b in enumerate(teammates) if b["id"] == u["id"])
                preferred = tracking_control(pose, estimate, slot=slot, team=[b["pose"] for b in teammates] if len(teammates) >= 2 else None)
                if action.get("phase") == "transit" and action.get("points"):
                    points = action["points"]
                    nearest = min(range(action["index"], min(len(points), action["index"]+50)), key=lambda n: math.dist(pose[:2], points[n][:2]))
                    action["index"] = nearest
                    if nearest >= len(points)-5 or (math.dist(pose[:2], points[-1][:2]) < 35 and abs(math.remainder(pose[2]-points[-1][2], 2*math.pi)) < .8):
                        action["phase"] = "acquiring"
                        self.event("tracking_position_reached", {"uuv_id": u["id"], "contact_id": action["contact_id"]})
                    else:
                        preferred = follow_path(pose, points[nearest:nearest+60])
            elif action.get("points"):
                points = action["points"]
                index = action["index"]
                end = min(len(points), index+50)
                nearest = min(range(index, end), key=lambda n: math.dist(pose[:2], points[n][:2]))
                action["index"] = nearest
                if nearest >= len(points)-3:
                    if action["kind"] == "exit":
                        preferred = 0
                    elif math.dist(points[action.get("cycle_start_index", 0)][:2], points[-1][:2]) > 1:
                        self.pause("safety_no_authorized_continuation")
                        return
                    else:
                        action["index"] = action.get("cycle_start_index", 0)
                        preferred = follow_path(pose, points[action["index"]:action["index"]+60])
                        self.event("search_complete", {"uav_id": u["id"], "plan_id": action["plan_id"]})
                        self.queue_agent("Search segment complete; plan revisit if needed.", "search_complete")
                else:
                    preferred = follow_path(pose, points[nearest:nearest+60])
            else:
                self.pause("safety_no_authorized_continuation")
                return
            controls[u["id"]] = {"preferred": preferred, "execution_domain": action.get("execution_domain", [0, 0, 4000, 4000])}
        control_diagnostics = {}
        selected = choose_controls(self.uuvs, controls, self.contacts, self.obstacles, separation=self.config.separation, diagnostics=control_diagnostics)
        if selected is None:
            self.event("control_infeasible", {"algorithm": "joint-dubins-beam", "controls": controls, "diagnostics": control_diagnostics,
                "boats": [{"id": u["id"], "pose": u["pose"], "curvature": u["curvature"]} for u in self.uuvs]})
            self.pause("safety_infeasible")
            return
        for u in self.uuvs:
            if u["id"] in selected:
                u["curvature"] = selected[u["id"]]
                next_poses[u["id"]] = integrate(u["pose"], 4, selected[u["id"]], self.config.dt)
        # A common tick validates all proposed movements before committing any pose.
        for index, first in enumerate(self.uuvs):
            for second in self.uuvs[index+1:]:
                if math.dist(next_poses[first["id"]][:2], next_poses[second["id"]][:2]) < 40:
                    self.pause("safety_infeasible")
                    return
        target_poses = {}
        for target in self.targets:
            enemy.sample(self.adversary, target["pose"], self.uuvs, self.obstacles,
                         self.config.enemy_sensor_range, self.sim_time, self.config.seed+self.frame_id)
            pose, speed, curvature = enemy.control(target["pose"], self.adversary["detections"],
                self.adversary["parameters"], self.obstacles, self.config, self.sim_time)
            target_poses[target["id"]] = pose
            target.update(speed=speed, curvature=curvature)
        # Truth is used only by this simulator interlock, never to choose a control.
        if any(math.dist(pose[:2], target_pose[:2]) < self.config.separation for pose in next_poses.values() for target_pose in target_poses.values()):
            self.pause("safety_target_collision")
            return
        replacements = {}
        for u in self.uuvs:
            x, y = next_poses[u["id"]][:2]
            if not 0 <= x <= 4000 or not 0 <= y <= 4000:
                if self.active.get(u["id"], {}).get("kind") != "exit":
                    self.pause("safety_boundary_violation")
                    return
                replacement = replacement_pose(self, u, {**next_poses, **replacements})
                if replacement is None or any(math.dist(replacement[:2], p[:2]) < 150 for p in target_poses.values()):
                    self.pause("safety_entry_blocked")
                    return
                replacements[u["id"]] = replacement
        before_turnover = copy.deepcopy(self.uuvs) if replacements else None
        for u in self.uuvs:
            u["remaining_range_m"] = max(0, u["remaining_range_m"]-math.dist(u["pose"][:2], next_poses[u["id"]][:2]))
            u["pose"] = next_poses[u["id"]]
        if not apply_replacements(self, replacements):
            self.uuvs = before_turnover
            self.save()
            return
        for target in self.targets:
            target["pose"] = target_poses[target["id"]]
        self.sim_time = round(self.sim_time+self.config.dt, 6)
        self.frame_id += 1
        if self.frame_id % 5 == 0:
            self._observe()
            finish_handover(self)
            for u in self.uuvs:
                u["trail"] = (u["trail"]+[u["pose"][:2]])[-300:]
        for vessel in self.vessels:
            target = next((target for target in self.targets if target["id"] == vessel["scenario_entity_id"]), None)
            if target:
                contact = self.contacts.get(self.contact_mapping.get(target["id"]), {})
                vessel.update(position=self.cells(target["pose"]), heading_deg=-math.degrees(target["pose"][2]),
                    surveillance_stage={"tentative": "detected", "confirmed": "probing", "tracking": "tracking"}.get(contact.get("state"), "undetected"))
        for plan in self.plans.values():
            if plan["status"] == "pending_approval" and self.sim_time > plan["expires_at_s"]:
                plan["status"] = "expired"
                self.event("approval_expired", {"plan_id": plan["plan_id"]})
        for intent in self.intents:
            if intent["lifecycle"] == "active" and self.sim_time/60 >= intent["expires_at_min"]:
                intent["lifecycle"] = "expired"
        if self.sim_time-self.last_periodic >= 30:
            self.last_periodic = self.sim_time
            self.queue_agent("Periodic review: preserve valid plans, act only on material changes.", "periodic")

    def cells(self, pose):
        return [pose[0]/100-.5, (4000-pose[1])/100-.5]

    @synchronized
    def frame(self):
        searchable = [(c, r) for c in range(40) for r in range(40) if not any(
            math.hypot(max(c*100, min(o["x"], (c+1)*100))-o["x"], max(3900-r*100, min(o["y"], 4000-r*100))-o["y"]) <= o["radius"]+8
            for o in self.obstacles)]
        information = information_fields(self.scan_times, self.contacts, searchable, self.sim_time, self.config)
        count = sum(self.scan_times[c][r] >= 0 for c, r in searchable)
        coverage = 100*count/max(1, len(searchable))
        recent = sum(self.scan_times[c][r] >= 0 and 0 <= self.sim_time-self.scan_times[c][r] <= 1800 for c, r in searchable)
        boats = []
        for u in self.uuvs:
            action = self.active.get(u["id"], {})
            kind = action.get("kind", "idle")
            mode = "track" if kind == "track" else "coverage" if kind in ("search", "reacquire", "path") else "idle"
            points = action.get("points", [])
            region = next((r for r in self.regions if r["owner"] == u["id"]), None)
            boats.append({"id": u["id"], "position": self.cells(u["pose"]), "heading_rad": -u["pose"][2],
                "generation": u["generation"], "remaining_range_m": u["remaining_range_m"],
                "energy_pct": 100*u["remaining_range_m"]/self.config.range_capacity,
                "speed_mps": 4 if action and self.status == "running" else 0,
                "heading_deg": math.degrees(u["pose"][2]) % 360,
                "sensor_mode": sensor_mode(action), "task_phase": action.get("phase", kind),
                "effective_tracking": kind == "track" and action.get("phase") == "tracking" and sensor_mode(action) == "passive"
                    and self.contacts.get(action.get("contact_id"), {}).get("state") == "tracking"
                    and u["id"] in self.contacts.get(action.get("contact_id"), {}).get("observers", []),
                "trail": [self.cells(p) for p in u["trail"]], "status": action.get("phase", "acquiring") if kind == "track" else "searching" if mode == "coverage" else "transit" if kind in ("loiter", "exit") else "idle",
                "operation_mode": mode, "operational_status": "available", "control_owner": "system",
                "assigned_region_id": region["id"] if region else None, "team_id": action.get("plan_id"), "target_group_id": action.get("contact_id"),
                "task_visual": {"task_type": mode, "phase": kind, "route_source": "python", "route_status": "active" if action else "cleared"},
                "planned_path": [self.cells(p) for p in points[action.get("index", 0)::10]][:400], "sensor_radius_cells": 3.5})
        contacts = []
        for c in self.contacts.values():
            contacts.append({**c, "estimated_position": self.cells([c["x"], c["y"]]), "vessel_class": c.get("vessel_class", "underwater"),
                "estimated_velocity": [c["vx"]/100, -c["vy"]/100],
                "samples": [{**s, **({"position": self.cells([s["x"], s["y"]])} if "x" in s else {})} for s in c["samples"]]})
        plans = [self.summary(p) for p in self.plans.values()]
        seconds = int(self.sim_time)
        return {"schema_version": "mission-frame/v2", "visual_schema_version": "mission-visual/v1", "episode_id": self.episode,
            "frame_id": self.frame_id, "sim_time_min": self.sim_time/60, "timestamp": f"{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}",
            "cycle": self.agent["cycle"], "runtime_status": self.status, "mission_revision": self.revision, "autonomy_mode": self.mode,
            "agent_status": copy.deepcopy(self.agent), "event_cursor": self.cursor, "information_source": "backend", "information_version": self.frame_id,
            "task_area": {"width_km": 4, "height_km": 4, "cell_size_km": .1}, **information,
            "value_matrix": copy.deepcopy(information["target_info_matrix"]), "searchable_cells": len(searchable), "coverage_pct": coverage,
            "coverage_metrics": {"schema_version": "persistent-coverage/v1", "status": "ok", "as_of_min": self.sim_time/60,
                "fixed_searchable_area_km2": len(searchable)*.01, "cumulative_pct": coverage, "unseen_pct": 100-coverage,
                "windows": [{"minutes": minutes, "window_complete": self.sim_time >= minutes*60,
                    "coverage_pct": 100*sum(0 <= self.scan_times[c][r] and self.sim_time-self.scan_times[c][r] <= minutes*60 for c, r in searchable)/max(1, len(searchable)),
                    "covered_area_km2": sum(0 <= self.scan_times[c][r] and self.sim_time-self.scan_times[c][r] <= minutes*60 for c, r in searchable)*.01} for minutes in (30, 60, 120)]},
            "uavs": boats, "contacts": contacts, "teams": [{"id": p["plan_id"], "members": [u for u in p.get("active_members", p["members"]) if self.active.get(u, {}).get("kind") == "track"], "task": p["kind"]} for p in plans if p["status"] == "active" and p["kind"] == "track"] +
                [{"id": r["id"], "members": [r["owner"]], "task": "search"} for r in self.regions],
            "plans": plans, "pending_approvals": [p for p in plans if p["status"] == "pending_approval"],
            "search_regions": [{"id": r["id"], "bbox": [r["bbox_m"][0]/100, (4000-r["bbox_m"][3])/100, r["bbox_m"][2]/100, (4000-r["bbox_m"][1])/100],
                "cells": r["cells"], "assigned_uav_id": r["owner"], "revision": self.region_revision, "priority": "medium",
                "completion_pct": 100*sum(self.scan_times[c][row] >= 0 for c, row in r["cells"])/max(1, len(r["cells"]))} for r in self.regions],
            "mission_metrics": {**self.metrics, "search_boats": sum(a["kind"] in ("search", "reacquire") for a in self.active.values()),
                "search_regions": len(self.regions), "coverage_pct": coverage, "unscanned_cells": len(searchable)-count,
                "recent_coverage_pct": 100*recent/max(1, len(searchable)), "revisit_timeliness_pct": 100*recent/count if count else None,
                "handoff_success_rate": 100*self.metrics["handoff_count"]/self.metrics["handoff_attempts"] if self.metrics["handoff_attempts"] else None},
            "standing_policy": copy.deepcopy(self.standing_policy),
            "bearing_lines": [{"from": self.cells(s["observer_pose"]),
                "to": self.cells([s["observer_pose"][0]+350*math.cos(s["bearing_rad"]), s["observer_pose"][1]+350*math.sin(s["bearing_rad"])]),
                "observer_id": s["observer_id"], "contact_id": s["contact_id"]} for s in self.observations if s.get("mode") == "passive" and self.sim_time-s["time_s"] < 2],
            "track_regions": [], "events": self.events[-120:], "messages": copy.deepcopy(self.messages[-30:]), "intents": copy.deepcopy(self.intents), "intent_statuses": [],
            "scenario_vessels": [], "ships": [], "markers": [], "vessel_mutation_allowed": False,
            "adversary_status": {"status": self.adversary["status"] if time.monotonic()-self.adversary["last_heartbeat"] < 30 else "offline", "model": self.config.model, "cycle": self.adversary["cycle"]},
            "obstacles": [{"id": f"obstacle-{i}", "vertices": [self.cells([o["x"]+o["radius"]*math.cos(a*math.pi/8), o["y"]+o["radius"]*math.sin(a*math.pi/8)]) for a in range(16)]} for i, o in enumerate(self.obstacles)]}
