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

from .config import Config, algorithm_settings
from .store import Store, ReceiptLedger
from .algorithms.motion import integrate
from .algorithms.planning import plan_path, path_safe
from .algorithms.coverage import plan_search
from .algorithms.allocation import allocate_tasks
from .algorithms.tracking import acquisition_control, tracking_control, follow_path, tracking_plan
from .algorithms.partition import partition_regions
from .algorithms.control import choose_controls
from .mission_planning import search_bundle, explicit_regions
from .lifecycle import prepare_exits, replacement_pose, apply_replacements, repair_search, navigation_pose, exit_route
from .handover import prepare_handover, finish_handover
from .sensing import observe, sensor_mode, sensor_roles
from .information import information_fields, target_evidence_field
from .plugins import PLUGIN_SPECS, frame_activity as plugin_frame_activity
from . import adversary as enemy


CHECKPOINT_FIELDS = tuple(("episode sim_time frame_id revision policy_version mode session_grants status uuvs fleet_entry targets obstacles active results plans contacts observations obstacle_observations events cursor scan_times intents vessels tasks messages agent_jobs agent last_periodic sensor_enabled contact_mapping contact_counter last_observation_time regions standing_policy metrics region_revision observation_cursor adversary").split())
ALGORITHM_IDS = {"compute_task_allocation": "slot_assignment", "plan_path": "dubins_hybrid",
                 "plan_search": "strip_coverage", "plan_tracking": "distance_band", "partition_search_area": "connected_partition"}
_RUNTIME = algorithm_settings("runtime")
_LIFECYCLE = algorithm_settings("lifecycle")
_PARTITION = algorithm_settings("partition")
_SCENE = algorithm_settings("scene")
_CBS = algorithm_settings("cbs")
_CONTROL = algorithm_settings("control")
_ASSIGNMENT = algorithm_settings("assignment")
_MISSION = algorithm_settings("mission_planning")
_ENEMY = algorithm_settings("adversary")
_OBS = algorithm_settings("observations")


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
        # Tick stages run through the plugin registry: each stage belongs to
        # the control plugins that own it, and the loop skips a stage only
        # when every owning plugin is disabled (core plugins cannot be
        # disabled, so today every stage always runs).
        self.plugin_states = {spec["id"]: True for spec in PLUGIN_SPECS}
        self._tick_stages = (
            (("coop-tracking",), self._stage_provisional_leases),
            (("reacquire", "region-partition"), self._stage_contact_repairs),
            (("coop-tracking",), self._stage_handover_prepare),
            (("energy-lifecycle",), self._stage_exit_prepare),
            (("coop-tracking", "path-planning", "coverage-search", "reacquire",
              "energy-lifecycle", "uuv-control"), self._stage_motion),
            (("sensor-fusion", "coop-tracking"), self._stage_observations),
            (None, self._stage_scene),
            (("task-allocation",), self._stage_plan_lifecycle),
            (("region-partition", "coverage-search"), self._stage_coverage_review),
        )
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
            if "fleet_entry" not in saved:
                self.fleet_entry = None
            self.adversary.setdefault("maneuver_history", [])
            if self.adversary.get("parameters"):
                self.clear_target_maneuver("目标控制进程重启，恢复默认控制")
            self.adversary.update(job=None, parameters=None, status="offline", last_heartbeat=0.0, last_started_wall=0.0)
            # Preserve the human mission and primary target, remove the old scene/AIS bypass.
            self.targets = [next((target for target in self.targets if target["id"] == _SCENE["target"]["id"]), self.targets[0])] if self.targets else [copy.deepcopy(_SCENE["target"])]
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
        self.session_grants = []
        self.status = "ready"
        # All boats enter through one point at the middle of the left boundary.
        # They cannot literally share a coordinate: the joint controller
        # requires pairwise clearance of separation+padding, so they pack at
        # the tightest legal spacing along the boundary.
        side = "left"
        center = self.config.height/2
        self.fleet_entry = {"position": [0, center], "count": self.config.fleet_size, "side": side}
        self.uuvs = []
        for i in range(self.config.fleet_size):
            y = center+(i-(self.config.fleet_size-1)/2)*_SCENE["fleet_entry_min_separation_m"]
            self.uuvs.append({"id": f"UUV-{i+1}", "pose": [0, y, 0], "trail": [], "curvature": 0.0,
                "generation": 1, "remaining_range_m": self.config.range_capacity, "capabilities": ["active", "passive"]})
        self.targets = [copy.deepcopy(_SCENE["target"])]
        self.adversary = enemy.initial_state()
        self.obstacles = copy.deepcopy(_SCENE["obstacles"])
        self.active = {}
        self.results = {}
        self.plans = {}
        self.receipts = ReceiptLedger(self.store)
        self.contacts = {}
        self.observations = []
        self.obstacle_observations = []
        self.events = []
        self.cursor = 0
        self.scan_times = [[-1.0]*int(self.config.height/self.config.cell) for _ in range(int(self.config.width/self.config.cell))]
        self.intents = []
        self.vessels = []
        self.tasks = []
        self.messages = []
        self.agent_jobs = []
        self.agent = {"status": "offline", "model": self.config.model, "cycle": 0, "error": None}
        self.last_periodic = 0.0
        self.last_coverage_replan = 0.0
        self.sensor_enabled = True
        self.contact_mapping = {}
        self.contact_counter = 0
        self.observation_cursor = 0
        self.last_observation_time = -1.0
        self.regions = []
        self.region_revision = 0
        self.standing_policy = {"enabled": False, "energy_rotation": False, "local_repair": False, "lost_reacquire": False, "contact_hold": False}
        self.repair_pending = set()
        self.metrics = {"effective_tracking_seconds": 0.0, "lost_seconds": 0.0, "handoff_count": 0, "handoff_attempts": 0, "rotation_count": 0}

    @synchronized
    def save(self):
        terminal = [p for p in self.plans.values() if p["status"] not in ("active", "pending_approval")]
        for plan in terminal[:-_RUNTIME["max_pending_approvals"]]:
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
        self.events = self.events[-_RUNTIME["max_events"]:]
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
        self.rng = random.Random(self.config.seed)
        self._initial()
        self.event("environment_reset", {})
        self.save()

    @synchronized
    def set_mode(self, mode):
        if mode not in ("request", "assisted", "full"):
            raise MissionError("invalid_mode", 422)
        self.mode = mode
        self.policy_version += 1
        self.session_grants = []
        for plan in self.plans.values():
            if plan["status"] == "pending_approval":
                plan["status"] = "expired"
                self._requeue_auto_track(plan)
        self.event("permission_changed", {"mode": mode})
        self.save()

    @synchronized
    def mission_state(self):
        return copy.deepcopy({"episode_id": self.episode, "snapshot_id": self.frame_id,
            "mission_revision": self.revision, "sim_time_s": self.sim_time, "autonomy_mode": self.mode,
            "runtime_status": self.status, "uuvs": [{"id": u["id"], "pose": u["pose"], "generation": u["generation"],
                "remaining_range_m": u["remaining_range_m"], "capabilities": u["capabilities"],
                "sensor_mode": sensor_mode(self.active.get(u["id"], {})),
                "sensor_roles": sensor_roles(self.active.get(u["id"], {})),
                "phase": self.active.get(u["id"], {}).get("phase", "idle"), "task": self.active.get(u["id"], {}).get("kind", "idle")} for u in self.uuvs],
            "contacts": list(self.contacts.values()), "bounds": [0, 0, self.config.width, self.config.height], "obstacles": self.obstacles,
            "obstacle_observations": getattr(self, "obstacle_observations", []),
            "active_plans": [self.summary(p) for p in self.plans.values() if p["status"] in ("active", "pending_approval")],
            "intents": [{**intent, "bbox": [intent["bbox"][0]*self.config.cell, self.config.height-intent["bbox"][3]*self.config.cell,
                intent["bbox"][2]*self.config.cell, self.config.height-intent["bbox"][1]*self.config.cell]} for intent in self.intents],
            "search_regions": [{k: v for k, v in r.items() if k not in ("cells", "scan_cells")} for r in self.regions], "standing_policy": self.standing_policy,
            "metrics": self.metrics, "region_revision": self.region_revision,
            "constraints": {"max_team_size": _ASSIGNMENT["maximum_team_size"], "min_turn_radius_m": self.config.radius, "speed_mps": self.config.speed}})

    @staticmethod
    def summary(result):
        summary = {key: value for key, value in result.items() if key not in ("routes", "points", "start_poses", "coverage_repair")}
        if "regions" in summary:
            summary["regions"] = [{k: v for k, v in r.items() if k not in ("cells", "scan_cells")} for r in summary["regions"]]
        return summary

    def _members(self, data, maximum=_ASSIGNMENT["maximum_team_size"]):
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
            members = copy.deepcopy([u for u in self.uuvs if self.active.get(u["id"], {}).get("kind") not in ("exit", "stranded")]) if automatic else self._members(data,
                self.config.fleet_size if name in ("plan_search", "partition_search_area", "compute_task_allocation") else _ASSIGNMENT["maximum_team_size"])
            if automatic and name != "plan_tracking":
                members = [u for u in members if self.active.get(u["id"], {}).get("kind") != "track"]
            if global_coverage:
                members = [u for u in members if "active" in u["capabilities"]
                    and self.active.get(u["id"], {}).get("kind", "idle") in ("idle", "search", "reacquire")
                    and u["remaining_range_m"] > min(u["pose"][0], u["pose"][1], self.config.width-u["pose"][0], self.config.height-u["pose"][1])+self.config.exit_reserve+_LIFECYCLE["exit_trigger_buffer_m"]]
            if name == "plan_tracking":
                members = [u for u in members if self.active.get(u["id"], {}).get("kind") != "track" or self.active[u["id"]].get("contact_id") == data.get("contact_id")]
                for boat in members:
                    region = next((r for r in self.regions if r["owner"] == boat["id"]), None)
                    boat["search_workload"] = sum(self.scan_times[c][r] < 0 for c, r in region["cells"])/max(1, len(region["cells"])) if region else 0
            members = [navigation_pose(self, boat) for boat in members]
            obstacles = copy.deepcopy(self.obstacles)
            ownership_obstacles = copy.deepcopy(obstacles)
            selected_ids = {u["id"] for u in members}
            obstacles += [{"x": u["pose"][0], "y": u["pose"][1], "radius": self.config.separation+_RUNTIME["obstacle_interaction_padding_m"]}
                          for u in self.uuvs if u["id"] not in selected_ids and u["id"] not in self.active]
            contacts = copy.deepcopy(self.contacts)
            snapshot = self.frame_id
            snapshot_time = self.sim_time
            assignments = {u["id"]: self.active.get(u["id"], {}).get("plan_id") for u in members}
            scan_times = copy.deepcopy(self.scan_times)
            regions = copy.deepcopy(self.regions)
            all_boats = copy.deepcopy(self.uuvs)
            old_actions = copy.deepcopy(self.active)
        bbox = data.get("bbox", _SCENE["default_search_bbox_m"])
        tracked_contacts = {action.get("contact_id") for action in old_actions.values()
                            if action.get("kind") == "track" and action.get("phase") == "tracking"}
        target_evidence = target_evidence_field(scan_times, contacts, snapshot_time, self.config, tracked_contacts)
        if name == "compute_task_allocation" and data.get("contact_id"):
            contact = contacts.get(data["contact_id"])
            if not contact or contact["state"] in ("tentative", "lost"):
                raise MissionError("confirmed_contact_required", 422)
            result = tracking_plan(members, contact, obstacles, required_members=[contact.get("discovered_by")])
            chosen = result.get("members", [])
            members = [boat for boat in members if boat["id"] in chosen]
            result.pop("routes", None)
            result.update(kind="allocation", algorithm="cooperative-geometry-allocation-v2", contact_id=data["contact_id"],
                teams=[{"id": f"candidate-{data['contact_id']}", "task_id": data["contact_id"], "members": chosen}] if chosen else [])
        elif name == "partition_search_area" or (name == "compute_task_allocation" and "tasks" not in data):
            result = partition_regions(members, scan_times, ownership_obstacles if global_coverage else obstacles,
                regions, now=snapshot_time, window_s=self.config.coverage_window_min*60, target_evidence=target_evidence)
            result["algorithm"] = "connected-workload-assignment-v2"
            result["teams"] = [{"id": r["id"], "task_id": r["id"], "members": [r["owner"]], "bbox": r["bbox_m"]} for r in result["regions"]]
        elif name == "compute_task_allocation":
            tasks = data.get("tasks", _SCENE["default_tasks"])
            result = allocate_tasks(members, tasks)
            task_domains = {task["id"]: task["bbox"] for task in tasks if "bbox" in task}
            for team in result["teams"]:
                if team["task_id"] in task_domains:
                    team["bbox"] = copy.deepcopy(task_domains[team["task_id"]])
        elif name == "plan_path":
            if len(members) != 1:
                raise MissionError("path_requires_one_uuv", 422)
            result = plan_path(members[0]["pose"], data.get("goal", _SCENE["default_path_goal"]), obstacles=obstacles)
            result["routes"] = {members[0]["id"]: result.get("points", [])}
            result["kind"] = "path"
        elif name == "plan_search":
            if "standing_policy" in data and not isinstance(data["standing_policy"], bool):
                raise MissionError("boolean_required", 422)
            result = search_bundle(members, scan_times, ownership_obstacles, regions,
                allow_partial=data.get("standing_policy", automatic) or self.standing_policy["energy_rotation"],
                route_obstacles=obstacles, now=snapshot_time, window_s=self.config.coverage_window_min*60,
                target_evidence=target_evidence) if automatic else plan_search(members, bbox, obstacles=obstacles)
            result["kind"] = "reacquire" if data.get("mode") == "reacquire" else "search"
            if not automatic:
                result["bbox"] = bbox
                result["fleet_plan"] = len(members) > _ASSIGNMENT["maximum_team_size"]
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
            result = tracking_plan(members, contact, obstacles, required_members=[contact.get("discovered_by")])
            chosen = result.get("members", [])
            members = [u for u in members if u["id"] in chosen]
            result.update(kind="track", contact_id=contact_id, bbox=[0, 0, self.config.width, self.config.height])
            if result["status"] == "succeeded" and self.standing_policy["enabled"]:
                remaining = [navigation_pose(self, u) for u in all_boats if u["id"] not in chosen and old_actions.get(u["id"], {}).get("kind") in ("search", "reacquire")]
                repair = search_bundle(remaining, scan_times, obstacles, regions,
                    allow_partial=self.standing_policy["energy_rotation"], now=snapshot_time,
                    window_s=self.config.coverage_window_min*60, target_evidence=target_evidence)
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
        if result.get("kind") == "track" and result.get("contact_id") in contacts:
            belief = contacts[result["contact_id"]]
            result["contact_snapshot"] = {key: belief[key] for key in ("x", "y", "vx", "vy")}
        affected = set(result["members"]) | set(result.get("repair_assignments", {}))
        if result.get("fleet_plan") and not result.get("partial_region_update"):
            affected.update(region["owner"] for region in regions)
        result["region_signatures"] = {member: self.region_signature(next((r for r in regions if r["owner"] == member), None)) for member in affected}
        points = [point for route in result.get("routes", {}).values() for point in route]
        padding = _RUNTIME["route_domain_padding_m"]
        result["execution_domain"] = [0, 0, self.config.width, self.config.height] if result.get("kind") == "track" or result.get("fleet_plan") else [max(0, min(p[0] for p in points)-padding), max(0, min(p[1] for p in points)-padding),
            min(self.config.width, max(p[0] for p in points)+padding), min(self.config.height, max(p[1] for p in points)+padding)] if points else [0, 0, self.config.width, self.config.height]
        result["domain_policy"] = "route_envelope_including_transit" if points else "map_wide_tracking"
        with self.lock:
            self.check_episode(episode)
            self.results[result["result_id"]] = result
            while len(self.results) > _RUNTIME["max_results"]:
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
        if not 1 <= len(members) <= (self.config.fleet_size if result.get("fleet_plan") else _ASSIGNMENT["maximum_team_size"]) or len(set(members)) != len(members):
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
            if not points or not path_safe(points, self.obstacles, result.get("execution_domain", (0, 0, self.config.width, self.config.height))):
                errors.append("unsafe_route")
            elif result.get("kind") in ("search", "reacquire") and (not 0 <= cycle < len(points) or
                math.dist(points[cycle][:2], points[-1][:2]) > _CBS["closed_route_tolerance_m"] or
                abs(math.remainder(points[cycle][2]-points[-1][2], 2*math.pi)) > _CBS["closed_heading_tolerance_rad"]):
                errors.append("closed_continuation_required")
        if result.get("kind") == "path":
            errors.append("terminal_continuation_required_use_search")
        for u in self.uuvs:
            if u["id"] in members and math.dist(u["pose"][:2], result["start_poses"][u["id"]][:2]) > _RUNTIME["candidate_freshness_m"]:
                errors.append("start_pose_changed_replan")
            if u["id"] in members:
                x, y = u["pose"][:2]
                if u["remaining_range_m"] < min(x, y, self.config.width-x, self.config.height-y)+self.config.exit_reserve+_LIFECYCLE["exit_trigger_buffer_m"]:
                    errors.append("insufficient_energy")
                capability = "passive" if result.get("kind") == "track" else "active"
                if capability not in u["capabilities"]:
                    errors.append("missing_sensor_capability")
        if result.get("kind") == "track" and self.contacts.get(result.get("contact_id"), {}).get("state") in (None, "lost"):
            errors.append("contact_lost")
        if result.get("kind") == "track" and result.get("contact_snapshot"):
            previous = result["contact_snapshot"]
            current = self.contacts.get(result["contact_id"])
            if current:
                elapsed = max(0, self.sim_time-result["created_at_s"])
                predicted = [previous["x"]+previous["vx"]*elapsed, previous["y"]+previous["vy"]*elapsed]
                if math.dist(predicted, [current["x"], current["y"]]) > _RUNTIME["contact_candidate_freshness_m"]:
                    errors.append("contact_estimate_changed_replan")
                if "last_seen" in current and self.sim_time-current["last_seen"] > _RUNTIME["contact_candidate_max_age_s"]:
                    errors.append("contact_observation_stale_replan")
        if result.get("kind") == "track":
            existing = {member for member, action in self.active.items() if action["kind"] == "track" and action.get("contact_id") == result.get("contact_id")}
            if len(existing | set(members)) > _ASSIGNMENT["maximum_team_size"]:
                errors.append("tracking_handover_team_limit")
            passive_established = self.contacts.get(result.get("contact_id"), {}).get("state") == "tracking" and sum(
                self.active[member].get("phase") == "tracking" and sensor_mode(self.active[member]) == "passive" for member in existing-set(members)) >= 2
            if result.get("acquisition_mode") == "active_until_cooperative_geometry" and not passive_established and sum(
                    boat["id"] in members and "active" in boat["capabilities"] for boat in self.uuvs) < 2:
                errors.append("active_acquisition_requires_two_active_sensors")
        energy_risk = max((1-u["remaining_range_m"]/self.config.range_capacity for u in self.uuvs if u["id"] in members), default=0)
        uncertainty = self.contacts.get(result.get("contact_id"), {}).get("uncertainty_m", 0)
        risk = min(1, (_RUNTIME["tracking_base_risk"] if result.get("kind") in ("track", "reacquire") else _RUNTIME["search_base_risk"])
            + _RUNTIME["energy_risk_weight"]*energy_risk + min(_RUNTIME["uncertainty_risk_cap"], uncertainty/_RUNTIME["uncertainty_risk_scale_m"]))
        permitted = any(grant["policy_version"] == self.policy_version and grant["kind"] == result.get("kind")
            and grant["standing_policy"] == bool(result.get("standing_policy"))
            and grant["contact_id"] == result.get("contact_id")
            and len(result.get("execution_domain", [])) == 4
            and grant["domain"][0] <= result["execution_domain"][0]
            and grant["domain"][1] <= result["execution_domain"][1]
            and grant["domain"][2] >= result["execution_domain"][2]
            and grant["domain"][3] >= result["execution_domain"][3] for grant in self.session_grants)
        return {"result_id": result["result_id"], "valid": not errors, "errors": sorted(set(errors)), "risk": risk,
            "requires_approval": not permitted and (self.mode == "request" or (self.mode == "assisted" and risk > _RUNTIME["risk_approval_threshold"]))}

    @staticmethod
    def region_signature(region):
        if region is None:
            return None
        return hashlib.sha256(json.dumps([region["id"], sorted(region["cells"])]).encode()).hexdigest()

    def submit(self, result_id, command_id, episode, decision_reason=None):
        with self.transaction():
            self.check_episode(episode)
            if decision_reason is not None and (not isinstance(decision_reason, str) or not 1 <= len(decision_reason.strip()) <= 500):
                raise MissionError("invalid_decision_reason", 422)
            digest = hashlib.sha256(json.dumps([result_id, episode, decision_reason]).encode()).hexdigest()
            if command_id in self.receipts:
                prior = self.receipts[command_id]
                if prior["digest"] != digest:
                    raise MissionError("idempotency_conflict")
                return copy.deepcopy(prior["response"])
            if self.status == "stopped":
                raise MissionError("mission_stopped")
            if sum(p["status"] == "pending_approval" for p in self.plans.values()) >= _RUNTIME["max_pending_approvals"]:
                raise MissionError("approval_queue_full", 429)
            assessment = self.evaluate(result_id)
            if not assessment["valid"]:
                raise MissionError(";".join(assessment["errors"]))
            result = copy.deepcopy(self.results[result_id])
            plan = {**result, "plan_id": identifier("plan"), "status": "pending_approval" if assessment["requires_approval"] else "approved",
                "decision_reason": decision_reason.strip() if decision_reason else None,
                "risk": assessment["risk"], "policy_version": self.policy_version, "expires_at_s": self.sim_time+_RUNTIME["approved_plan_expiration_s"],
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
                    self._requeue_auto_track(old)
        for index, member in enumerate(plan["members"]):
            self.active[member] = {"plan_id": plan["plan_id"], "kind": plan["kind"], "contact_id": plan.get("contact_id"),
                "points": plan.get("routes", {}).get(member, []), "index": 0, "slot": index,
                "cycle_start_index": plan.get("cycle_start_indices", {}).get(member, 0),
                "requires_replan_after_cycle": member in plan.get("partial_patrols", {}),
                "phase": "transit" if plan["kind"] == "track" else "scanning", "generation": next(u["generation"] for u in self.uuvs if u["id"] == member)}
            self.active[member]["execution_domain"] = plan["execution_domain"]
            if plan["kind"] == "track" and plan.get("acquisition_mode") == "active_until_cooperative_geometry":
                active_capable = "active" in next(boat["capabilities"] for boat in self.uuvs if boat["id"] == member)
                self.active[member]["acquisition_mode"] = "passive" if passive_established or not active_capable else "active"
        if plan.get("standing_policy"):
            self.standing_policy = {"enabled": True, "energy_rotation": True, "local_repair": True, "lost_reacquire": True,
                "contact_hold": True, "plan_id": plan["plan_id"]}
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
                cells = [[c, r] for c in range(len(self.scan_times)) for r in range(len(self.scan_times[c])) if (c, r) not in owned
                         and bounds[0] <= (c+.5)*self.config.cell < bounds[2]
                         and y0 <= self.config.height-(r+.5)*self.config.cell < y1
                         and path_safe([[(c+.5)*self.config.cell, self.config.height-(r+.5)*self.config.cell, 0]], self.obstacles,
                                       (0, 0, self.config.width, self.config.height), margin=_MISSION["explicit_region_obstacle_margin_m"])]
                self.regions.append({"id": f"region-{member}", "owner": member, "bbox_m": bounds, "cells": cells})
                owned.update(map(tuple, cells))
            self.region_revision += 1
        elif plan["kind"] == "track":
            self.regions = [r for r in self.regions if r["owner"] not in plan["members"]]
            repair = plan.get("coverage_repair")
            if repair:
                self.regions = copy.deepcopy(repair["regions"])
                for member, points in repair["routes"].items():
                    self.active[member].update(points=points, index=0, execution_domain=[0, 0, self.config.width, self.config.height], cycle_start_index=repair.get("cycle_start_indices", {}).get(member, 0))
                self.region_revision += 1
                self.event("coverage_repartitioned", {"regions": len(self.regions), "reason": "tracking_assignment"})
        plan["status"] = "active"
        plan["active_members"] = list(plan["members"])
        self.revision += 1
        self.event("mission_assignment_committed", {"plan_id": plan["plan_id"], "members": plan["members"]})

    @synchronized
    def decide(self, plan_id, decision, comment=None):
        if decision is True:
            decision = "approve_once"
        elif decision is False:
            decision = "reject"
        if decision not in ("approve_once", "approve_session", "reject"):
            raise MissionError("invalid_decision", 422)
        if comment is not None and (not isinstance(comment, str) or not 1 <= len(comment.strip()) <= 500):
            raise MissionError("invalid_approval_comment", 422)
        plan = self.plans.get(plan_id)
        if not plan:
            raise MissionError("plan_not_found", 404)
        if plan["status"] != "pending_approval":
            raise MissionError("approval_not_pending")
        if plan["policy_version"] != self.policy_version or self.sim_time > plan["expires_at_s"]:
            plan["status"] = "expired"
            self._requeue_auto_track(plan)
            self.save()
            raise MissionError("approval_expired")
        if decision != "reject":
            assessment = self._assessment(plan)
            if not assessment["valid"]:
                plan["status"] = "expired"
                self._requeue_auto_track(plan)
                self.save()
                raise MissionError(";".join(assessment["errors"]))
            self._activate(plan)
            if decision == "approve_session":
                self.session_grants.append({"kind": plan["kind"], "standing_policy": bool(plan.get("standing_policy")),
                    "contact_id": plan.get("contact_id"), "domain": list(plan["execution_domain"]),
                    "policy_version": self.policy_version})
        else:
            plan["status"] = "rejected"
            self._requeue_auto_track(plan)
        plan["approval_scope"] = decision
        if comment is not None:
            plan["approval_comment"] = comment.strip()
        self.event("approval_decided", {"plan_id": plan_id, "status": plan["status"], "decision": decision,
            "comment": plan.get("approval_comment")})
        self.save()
        return self.summary(plan)

    @synchronized
    def debug_fuel_shortage(self, uuv_id=None):
        """Drain a boat's remaining range so the standard energy-exit lifecycle engages.

        The chosen level stays above the exit-route requirement but below the
        1.5x-edge trigger and the handover affordability check, so the boat
        exits directly instead of arranging a seamless relief.
        """
        if uuv_id is not None:
            candidates = [u for u in self.uuvs if u["id"] == uuv_id]
            if not candidates:
                raise MissionError("unknown_uuv", 404)
        else:
            tracking = [u for u in self.uuvs if self.active.get(u["id"], {}).get("kind") == "track"]
            others = [u for u in self.uuvs if self.active.get(u["id"], {}).get("kind") not in (None, "track", "exit")]
            candidates = (sorted(tracking, key=lambda u: u["remaining_range_m"])
                          + sorted(others, key=lambda u: u["remaining_range_m"]))
        for boat in candidates:
            action = self.active.get(boat["id"], {})
            if action.get("kind") == "exit":
                continue
            x, y = boat["pose"][:2]
            edge = min(x, y, self.config.width-x, self.config.height-y)
            route = exit_route(self, boat)
            if route is None:
                continue
            required = route["length_m"]+_LIFECYCLE["exit_route_energy_margin_m"]+100
            if required >= 1.5*edge:
                continue
            boat["remaining_range_m"] = required
            self.event("debug_fuel_shortage", {"uuv_id": boat["id"], "generation": boat["generation"],
                "remaining_range_m": round(required, 1), "edge_distance_m": round(edge, 1)})
            self.save()
            return {"uuv_id": boat["id"], "generation": boat["generation"],
                    "remaining_range_m": required, "edge_distance_m": edge}
        raise MissionError("no_feasible_uuv")

    @synchronized
    def queue_agent(self, text, source="human", delivery="followUp", annotation=None, display_text=None):
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
                    queued.update(text=text[:_RUNTIME["max_message_chars"]], source=source, updated_at_s=self.sim_time,
                        coalesced_count=queued.get("coalesced_count", 0)+1)
                    self.event("agent_wakeup_coalesced", {"run_id": queued["run_id"], "source": source,
                        "text": queued["text"], "coalesced_count": queued["coalesced_count"]})
                    self.save()
                return queued
        running = next((j for j in self.agent_jobs if j["status"] == "running"), None)
        if sum(j["status"] in ("queued", "running") for j in self.agent_jobs) >= _RUNTIME["max_queued_jobs"]:
            if not priority:
                self.event("agent_wakeup_deferred", {"source": source, "reason": "queue_full"})
                return None
            if not (source == "human" and running):
                raise MissionError("agent_queue_full", 429)
        if source == "human" and running:
            if len(running.get("feedback", []))+len(running.get("accepted_feedback", [])) >= _RUNTIME["max_feedback_per_run"]:
                raise MissionError("feedback_queue_full", 429)
            running.setdefault("feedback", []).append({"id": identifier("feedback"), "text": text[:_RUNTIME["max_message_chars"]], "delivery": delivery})
            job = running
        else:
            job = {"run_id": identifier("run"), "episode_id": self.episode, "status": "queued", "text": text[:_RUNTIME["max_message_chars"]], "source": source, "feedback": []}
            self.agent_jobs.append(job)
        self.agent_jobs = self.agent_jobs[-_RUNTIME["max_agent_jobs"]:]
        if source == "human":
            self.messages.append({"id": identifier("message"), "role": "user", "text": (display_text if display_text is not None else text)[:_RUNTIME["max_message_chars"]], "time": self.sim_time,
                "status": "queued" if running else "completed", "run_id": job["run_id"], "delivery": delivery, "annotation": annotation,
                "feedback_id": running["feedback"][-1]["id"] if running else None})
            self.messages = self.messages[-_RUNTIME["max_messages"]:]
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
        for cancelled_id in cancelled:
            self.event("agent_cancelled", {"run_id": cancelled_id})
        self.agent["status"] = "idle"
        self.save()

    def _observe(self):
        observe(self)

    def provisional_contact(self, contact_id, sample):
        if not self.standing_policy.get("contact_hold") or self.status != "running":
            return
        member = sample["observer_id"]
        boat = next((u for u in self.uuvs if u["id"] == member), None)
        action = self.active.get(member)
        region = next((r for r in self.regions if r["owner"] == member), None)
        if (not boat or not action or action["kind"] != "search" or not region
                or sample["generation"] != boat["generation"] or "active" not in boat["capabilities"]
                or sum(a["kind"] == "track" and a.get("contact_id") == contact_id for a in self.active.values()) >= _ASSIGNMENT["maximum_team_size"]):
            return
        edge = min(boat["pose"][0], boat["pose"][1], self.config.width-boat["pose"][0], self.config.height-boat["pose"][1])
        if boat["remaining_range_m"] <= edge+self.config.exit_reserve+_RUNTIME["contact_hold_energy_reserve_m"]:
            return
        radius = _RUNTIME["contact_hold_radius_m"]
        x, y = boat["pose"][:2]
        self.active[member] = {"plan_id": action["plan_id"], "kind": "track", "phase": "provisional", "provisional": True,
            "contact_id": contact_id, "acquisition_mode": "active", "generation": boat["generation"],
            "slot": 0, "expires_at_s": self.sim_time+_RUNTIME["contact_hold_max_s"],
            "execution_domain": [max(0, x-radius), max(0, y-radius),
                                 min(self.config.width, x+radius), min(self.config.height, y+radius)]}
        self.regions = [r for r in self.regions if r["owner"] != member]
        self.region_revision += 1
        self.revision += 1
        self.event("provisional_contact_started", {"uuv_id": member, "contact_id": contact_id,
            "observation_id": sample["sample_id"], "observed_at_s": sample["time_s"],
            "estimated_position": [self.contacts[contact_id]["x"], self.contacts[contact_id]["y"]],
            "generation": boat["generation"],
            "gap_region_id": region["id"], "expires_at_s": self.active[member]["expires_at_s"]})

    def _end_provisional_contact(self, member, reason):
        action = self.active.pop(member)
        self.event("provisional_contact_expired" if reason == "lease_expired" else "provisional_contact_aborted",
            {"uuv_id": member, "contact_id": action["contact_id"], "reason": reason})
        return repair_search(self, add=[member], required=False)

    def _search_gap(self):
        if not any(u["id"] not in self.active and "active" in u["capabilities"]
                   for u in self.uuvs):
            return False
        assigned = {tuple(cell) for region in self.regions
                    if self.active.get(region["owner"], {}).get("kind") in ("search", "reacquire")
                    for cell in region["cells"]}
        for col in range(len(self.scan_times)):
            for row in range(len(self.scan_times[col])):
                x0, x1 = col*self.config.cell, (col+1)*self.config.cell
                y0, y1 = self.config.height-(row+1)*self.config.cell, self.config.height-row*self.config.cell
                blocked = any(math.hypot(max(x0, min(o["x"], x1))-o["x"],
                                          max(y0, min(o["y"], y1))-o["y"]) <= o["radius"]+_PARTITION["obstacle_margin_m"]
                              for o in self.obstacles)
                if (col, row) not in assigned and not blocked:
                    return True
        return False

    @synchronized
    def adversary_observation(self):
        target = self.targets[0]
        return copy.deepcopy({"episode_id": self.episode, "sim_time_s": self.sim_time,
            "own_pose": target["pose"], "own_speed_mps": target["speed"],
            "sensor_range_m": self.config.enemy_sensor_range,
            "bounds": [0, 0, self.config.width, self.config.height], "obstacles": self.obstacles,
            "detections": self.adversary["detections"], "history": self.adversary["history"]})

    def set_target_maneuver(self, parameters, reason, run_id):
        previous = self.adversary.get("parameters")
        changed = not previous or any(previous.get(key) != parameters[key] for key in ("speed_mps", "turn_bias", "duration_s"))
        self.adversary["parameters"] = {**parameters, "expires_at_s": self.sim_time+parameters["duration_s"]}
        if changed:
            history = self.adversary.setdefault("maneuver_history", [])
            history.append({"time_s": self.sim_time, "run_id": run_id, "reason": reason,
                "speed_mps": parameters["speed_mps"], "turn_bias": parameters["turn_bias"],
                "duration_s": parameters["duration_s"], "expires_at_s": self.adversary["parameters"]["expires_at_s"], "source": "llm"})
            self.adversary["maneuver_history"] = history[-200:]

    def clear_target_maneuver(self, reason):
        if not self.adversary.get("parameters"):
            return
        self.adversary["parameters"] = None
        history = self.adversary.setdefault("maneuver_history", [])
        history.append({"time_s": self.sim_time, "run_id": None, "reason": reason,
            "speed_mps": _ENEMY["fallback_speed_mps"], "turn_bias": 0,
            "duration_s": None, "expires_at_s": None, "source": "system"})
        self.adversary["maneuver_history"] = history[-200:]

    def plugin_enabled(self, plugin_id):
        return self.plugin_states.get(plugin_id, True)

    def set_plugin_enabled(self, plugin_id, enabled=True):
        spec = next((spec for spec in PLUGIN_SPECS if spec["id"] == plugin_id), None)
        if spec is None or (spec.get("core") and not enabled):
            return False
        self.plugin_states[plugin_id] = bool(enabled)
        return True

    @synchronized
    def tick(self):
        if self.status != "running":
            return
        enemy_job = self.adversary["job"]
        if enemy_job and enemy_job["status"] == "running" and enemy_job["lease_deadline"] <= time.monotonic():
            enemy_job["status"] = "failed"
            self.clear_target_maneuver("目标控制会话失效，恢复默认控制")
            self.adversary["status"] = "degraded"
        if self.adversary.get("parameters") and self.adversary["parameters"]["expires_at_s"] <= self.sim_time:
            self.clear_target_maneuver("机动参数到期，恢复默认控制")
        # Registry-driven pipeline: a stage is skipped only when every owning
        # plugin is disabled; a stage returning False aborts the tick (pause).
        for plugins, stage in self._tick_stages:
            if plugins and not all(self.plugin_enabled(pid) for pid in plugins):
                continue
            if stage() is False:
                return

    def _stage_provisional_leases(self):
        for member, action in list(self.active.items()):
            if action.get("provisional") and self.sim_time >= action["expires_at_s"]:
                if not self._end_provisional_contact(member, "lease_expired"):
                    return False
        return True

    def _stage_contact_repairs(self):
        stale_contacts = {action["contact_id"] for action in self.active.values()
            if action.get("kind") == "track" and not action.get("provisional") and action.get("contact_id") in self.contacts
            and self.contacts[action["contact_id"]]["state"] == "lost"
            and self.sim_time-self.contacts[action["contact_id"]]["last_seen"] >= _RUNTIME["reacquisition_max_unobserved_s"]
            and not any(other.get("kind") == "track" and other.get("contact_id") == action["contact_id"]
                and other.get("phase") == "transit" for other in self.active.values())
            and all(self.sim_time-other.get("acquisition_started_at_s", self.contacts[action["contact_id"]]["last_seen"])
                >= _RUNTIME["reacquisition_max_unobserved_s"] for other in self.active.values()
                if other.get("kind") == "track" and other.get("contact_id") == action["contact_id"])}
        for contact_id in stale_contacts:
            members = [member for member, action in self.active.items()
                if action.get("kind") == "track" and action.get("contact_id") == contact_id]
            if not self.standing_policy.get("local_repair"):
                self.pause("safety_stale_contact_repair_authorization_required")
                return False
            for member in members:
                self.active.pop(member)
            if repair_search(self, add=members, required=False):
                self.event("stale_contact_search_resumed", {"contact_id": contact_id, "members": members,
                    "last_seen_s": self.contacts[contact_id]["last_seen"]})
            self.queue_agent(f"Contact {contact_id} has no fresh observation. Coverage search resumed; plan new tracking only after a measured reacquisition.", "target_lost")
        # Boats dropped by an infeasible repair (coverage_gap_accepted) would
        # otherwise idle forever; retry the repartition as positions evolve.
        pending = [member for member in self.repair_pending if member not in self.active]
        if pending and self.standing_policy["local_repair"]:
            repair_search(self, add=pending, required=False)
        self.repair_pending.difference_update(self.active)
        return True

    def _stage_handover_prepare(self):
        if self.frame_id % _RUNTIME["safety_review_frames"] == 0:
            prepare_handover(self)
        return True

    def _stage_exit_prepare(self):
        return prepare_exits(self)

    def _stage_motion(self):
        next_poses = {}
        controls = {}
        for u in self.uuvs:
            action = self.active.get(u["id"])
            if not action:
                next_poses[u["id"]] = u["pose"]
                continue
            if action.get("generation", u["generation"]) != u["generation"]:
                self.pause("safety_stale_generation")
                return False
            pose = u["pose"]
            if action["kind"] == "track":
                estimate = self.contacts.get(action.get("contact_id"))
                if not estimate or (estimate["state"] == "lost" and not self.standing_policy["lost_reacquire"]):
                    self.pause("safety_tracking_contact_lost")
                    return False
                if estimate["state"] == "lost":
                    if action.get("phase") != "transit":
                        action["phase"] = "reacquiring"
                    action["acquisition_mode"] = "active"
                elif (action.get("phase") == "tracking" and self.standing_policy.get("lost_reacquire")
                      and self.sim_time-estimate["last_seen"] >= _RUNTIME["reacquisition_trigger_s"]):
                    action["phase"] = "reacquiring"
                    action["acquisition_mode"] = "active"
                    self.event("tracking_reacquisition_started", {"uuv_id": u["id"], "contact_id": action["contact_id"],
                        "last_seen_s": estimate["last_seen"]})
                teammates = [b for b in sorted(self.uuvs, key=lambda b: (self.active.get(b["id"], {}).get("slot", 0), b["id"]))
                    if self.active.get(b["id"], {}).get("kind") == "track" and self.active[b["id"]].get("contact_id") == action["contact_id"]
                    and (action.get("phase") == "transit" or self.active[b["id"]].get("phase") != "transit")]
                slot = next(i for i, b in enumerate(teammates) if b["id"] == u["id"])
                preferred = (acquisition_control(pose, estimate, reacquiring=action.get("phase") == "reacquiring") if action.get("acquisition_mode") == "active" and action.get("phase") in ("provisional", "acquiring", "reacquiring")
                    else tracking_control(pose, estimate, slot=slot, team=[b["pose"] for b in teammates] if len(teammates) >= 2 else None))
                if action.get("phase") == "transit" and action.get("points"):
                    points = action["points"]
                    nearest = min(range(action["index"], min(len(points), action["index"]+_RUNTIME["nearest_waypoint_span"])), key=lambda n: math.dist(pose[:2], points[n][:2]))
                    action["index"] = nearest
                    if nearest >= len(points)-_RUNTIME["arrival_waypoint_tolerance"] or (math.dist(pose[:2], points[-1][:2]) < _RUNTIME["arrival_distance_m"] and abs(math.remainder(pose[2]-points[-1][2], 2*math.pi)) < _RUNTIME["arrival_heading_rad"]):
                        action["phase"] = "acquiring"
                        action["acquisition_started_at_s"] = self.sim_time
                        self.event("tracking_position_reached", {"uuv_id": u["id"], "contact_id": action["contact_id"]})
                    else:
                        preferred = follow_path(pose, points[nearest:nearest+_RUNTIME["lookahead_points"]])
            elif action.get("points"):
                points = action["points"]
                index = action["index"]
                end = min(len(points), index+_RUNTIME["nearest_waypoint_span"])
                nearest = min(range(index, end), key=lambda n: math.dist(pose[:2], points[n][:2]))
                action["index"] = nearest
                if nearest >= len(points)-_RUNTIME["cycle_end_waypoint_tolerance"]:
                    if action["kind"] == "exit":
                        preferred = 0
                    elif math.dist(points[action.get("cycle_start_index", 0)][:2], points[-1][:2]) > _RUNTIME["closed_route_tolerance_m"]:
                        self.pause("safety_no_authorized_continuation")
                        return False
                    else:
                        action["index"] = action.get("cycle_start_index", 0)
                        preferred = follow_path(pose, points[action["index"]:action["index"]+_RUNTIME["lookahead_points"]])
                        if action["kind"] in ("search", "reacquire"):
                            self.event("search_complete", {"uav_id": u["id"], "plan_id": action["plan_id"]})
                        if action.get("requires_replan_after_cycle"):
                            self.queue_agent("Coverage gap or overdue revisit detected; review available search assignments.", "search_gap")
                else:
                    preferred = follow_path(pose, points[nearest:nearest+_RUNTIME["lookahead_points"]])
            else:
                self.pause("safety_no_authorized_continuation")
                return False
            domain = action.get("execution_domain", [0, 0, self.config.width, self.config.height])
            if action["kind"] != "exit" and min(pose[0], pose[1], self.config.width-pose[0], self.config.height-pose[1]) < _CONTROL["obstacle_margin_m"]:
                pad = _CONTROL["obstacle_margin_m"]
                domain = [-pad, -pad, self.config.width+pad, self.config.height+pad]
            controls[u["id"]] = {"preferred": preferred, "execution_domain": domain}
        control_diagnostics = {}
        selected = choose_controls(self.uuvs, controls, self.contacts, self.obstacles, separation=self.config.separation,
            diagnostics=control_diagnostics, recovery_domain=[0, 0, self.config.width, self.config.height])
        if selected is None:
            self.event("control_infeasible", {"algorithm": "joint-dubins-beam", "controls": controls, "diagnostics": control_diagnostics,
                "boats": [{"id": u["id"], "pose": u["pose"], "curvature": u["curvature"]} for u in self.uuvs]})
            provisional = next((member for member, action in self.active.items() if action.get("provisional")), None)
            if provisional and self._end_provisional_contact(provisional, "unsafe_joint_control"):
                self.tick()
                return False
            self.pause("safety_infeasible")
            return False
        if control_diagnostics.get("held_boats"):
            self.event("boats_held", {"uuv_ids": control_diagnostics["held_boats"]})
        for u in self.uuvs:
            if u["id"] in selected:
                u["curvature"] = selected[u["id"]]
                next_poses[u["id"]] = integrate(u["pose"], self.config.speed, selected[u["id"]], self.config.dt)
            else:
                next_poses[u["id"]] = u["pose"]
        # A common tick validates all proposed movements before committing any pose.
        # Pairs already inside the separation line (congestion that slipped in
        # via boundary turnover) must keep opening up; only worsening is a pause.
        for index, first in enumerate(self.uuvs):
            for second in self.uuvs[index+1:]:
                next_dist = math.dist(next_poses[first["id"]][:2], next_poses[second["id"]][:2])
                if next_dist < self.config.separation and next_dist < math.dist(first["pose"][:2], second["pose"][:2]):
                    self.pause("safety_infeasible")
                    return False
        target_poses = {}
        for target in self.targets:
            enemy.sample(self.adversary, target["pose"], self.uuvs, self.obstacles,
                         self.config.enemy_sensor_range, self.sim_time, self.config.seed+self.frame_id)
            pose, speed, curvature = enemy.control(target["pose"], self.adversary["detections"],
                self.adversary["parameters"], self.obstacles, self.config, self.sim_time)
            target_poses[target["id"]] = pose
            # Radiated noise gates passive detection: only a moving target is
            # loud enough to hold a passive track; sprinting is fast but
            # audible, going quiet is stealthy but nearly static.
            target.update(speed=speed, curvature=curvature,
                          passive_signal=speed >= _OBS["passive_signal_min_speed_mps"])
        # Truth is used only by this simulator interlock, never to choose a control.
        collisions = [(u["id"], target["id"]) for u in self.uuvs for target in self.targets
                      if math.dist(next_poses[u["id"]][:2], target_poses[target["id"]][:2]) < self.config.separation]
        if collisions:
            # Hold the boats that would collide and let the quarry keep
            # maneuvering; the tick proceeds so separation opens up over
            # successive ticks instead of pause-looping on frozen geometry.
            for target in self.targets:
                target["pose"] = target_poses[target["id"]]
            held = {c[0] for c in collisions}
            for u in self.uuvs:
                if u["id"] in held or any(math.dist(next_poses[u["id"]][:2], t["pose"][:2]) < self.config.separation for t in self.targets):
                    next_poses[u["id"]] = u["pose"]
                    held.add(u["id"])
            # Freezing boats can pull a moving boat's committed pose inside
            # separation of a now-stationary one; freeze worsening pairs too.
            stable = False
            while not stable:
                stable = True
                for index, first in enumerate(self.uuvs):
                    for second in self.uuvs[index+1:]:
                        next_dist = math.dist(next_poses[first["id"]][:2], next_poses[second["id"]][:2])
                        if next_dist < self.config.separation and next_dist < math.dist(first["pose"][:2], second["pose"][:2]):
                            next_poses[first["id"]] = first["pose"]
                            next_poses[second["id"]] = second["pose"]
                            held.update((first["id"], second["id"]))
                            stable = False
            self.event("target_collision_hold", {"collisions": collisions, "held": sorted(held)})
        replacements = {}
        for u in self.uuvs:
            x, y = next_poses[u["id"]][:2]
            if not 0 <= x <= self.config.width or not 0 <= y <= self.config.height:
                if self.active.get(u["id"], {}).get("kind") != "exit":
                    self.pause("safety_boundary_violation")
                    return False
                exiting = {key for key, action in self.active.items()
                    if action.get("kind") == "exit" and key not in replacements}
                replacement = replacement_pose(self, u, {**next_poses, **replacements}, ignore=exiting)
                if replacement is None or any(math.dist(replacement[:2], p[:2]) < _LIFECYCLE["replacement_separation_m"] for p in target_poses.values()):
                    # Hold the crossing boat at the boundary for one tick
                    # instead of freezing the whole sim: other boats and
                    # targets keep moving, so the entry slot can clear.
                    self.event("exit_entry_blocked", {"uuv_id": u["id"], "pose": u["pose"]})
                    next_poses[u["id"]] = u["pose"]
                    continue
                replacements[u["id"]] = replacement
        for u in self.uuvs:
            u["remaining_range_m"] = max(0, u["remaining_range_m"]-math.dist(u["pose"][:2], next_poses[u["id"]][:2]))
            u["pose"] = next_poses[u["id"]]
        apply_replacements(self, replacements)
        for target in self.targets:
            target["pose"] = target_poses[target["id"]]
        self.sim_time = round(self.sim_time+self.config.dt, 6)
        self.frame_id += 1
        return True

    def _stage_observations(self):
        if self.frame_id % _RUNTIME["observation_frames"] == 0:
            self._observe()
            finish_handover(self)
            for key, contact in self.contacts.items():
                if contact.get("auto_track_requested"):
                    self._auto_track(key)
            for u in self.uuvs:
                u["trail"] = (u["trail"]+[u["pose"][:2]])[-_RUNTIME["max_trail_points"]:]
        return True

    def _stage_scene(self):
        for vessel in self.vessels:
            target = next((target for target in self.targets if target["id"] == vessel["scenario_entity_id"]), None)
            if target:
                contact = self.contacts.get(self.contact_mapping.get(target["id"]), {})
                vessel.update(position=self.cells(target["pose"]), heading_deg=-math.degrees(target["pose"][2]),
                    surveillance_stage={"tentative": "detected", "confirmed": "probing", "tracking": "tracking"}.get(contact.get("state"), "undetected"))
        return True

    def _stage_plan_lifecycle(self):
        for plan in self.plans.values():
            if plan["status"] == "pending_approval" and self.sim_time > plan["expires_at_s"]:
                plan["status"] = "expired"
                self._requeue_auto_track(plan)
                self.event("approval_expired", {"plan_id": plan["plan_id"]})
        for intent in self.intents:
            if intent["lifecycle"] == "active" and self.sim_time/60 >= intent["expires_at_min"]:
                intent["lifecycle"] = "expired"
        return True

    def _stage_coverage_review(self):
        if self.sim_time-self.last_periodic >= _RUNTIME["periodic_review_s"]:
            self.last_periodic = self.sim_time
            if self._search_gap():
                self.queue_agent("Coverage gap or overdue revisit detected; preserve valid plans.", "coverage_gap")
        # Region routes sweep only the cells that were due when the last
        # partition ran; owned cells that expire inside the coverage window
        # would otherwise wait for an external trigger and silently decay.
        # A throttled local repair refreshes the due set and its routes.
        if (self.standing_policy["local_repair"]
                and self.sim_time-self.last_coverage_replan >= _RUNTIME["coverage_replan_s"]):
            self.last_coverage_replan = self.sim_time
            window_s = self.config.coverage_window_min*60
            due = any(0 <= cell[0] < len(self.scan_times) and 0 <= cell[1] < len(self.scan_times[cell[0]])
                      and (self.scan_times[cell[0]][cell[1]] < 0
                           or self.sim_time-self.scan_times[cell[0]][cell[1]] > window_s)
                      for region in self.regions for cell in region["cells"])
            if due:
                repair_search(self, required=False)
        return True

    def _requeue_auto_track(self, plan):
        if plan.get("kind") != "track":
            return
        contact = self.contacts.get(plan.get("contact_id"))
        if contact and contact.get("state") == "confirmed":
            contact["auto_track_requested"] = True

    def _auto_track(self, contact_id):
        contact = self.contacts.get(contact_id)
        if not contact:
            return
        contact["auto_track_requested"] = False
        live = any(plan.get("contact_id") == contact_id and plan.get("kind") == "track"
            and (plan["status"] in ("pending_approval", "approved")
                or (plan["status"] == "active" and any(
                    self.active.get(member, {}).get("kind") == "track"
                    and self.active[member].get("contact_id") == contact_id
                    for member in plan.get("active_members", plan.get("members", [])))))
            for plan in self.plans.values())
        # A dead plan must not silence replanning: once nothing pending,
        # approved or actively tracked remains, retry after a cooldown.
        if (contact["state"] != "confirmed" or live
                or self.sim_time < contact.get("auto_track_retry_at_s", 0.0)):
            return
        if any(action.get("kind") == "track" and action.get("contact_id") == contact_id for action in self.active.values()):
            return
        contact["auto_track_planned"] = True
        contact["auto_track_retry_at_s"] = self.sim_time+_RUNTIME["auto_track_retry_s"]
        try:
            result = self.calculate("plan_tracking", {"contact_id": contact_id})
        except MissionError as exc:
            self.event("auto_tracking_unavailable", {"contact_id": contact_id, "reason": str(exc)})
            return
        if result["status"] != "succeeded":
            self.event("auto_tracking_infeasible", {"contact_id": contact_id,
                "reason": result.get("diagnostics", {}).get("reason")})
            return
        try:
            response = self.submit(result["result_id"], f"auto-track-{contact_id}-{self.frame_id}", self.episode)
        except MissionError as exc:
            self.event("auto_tracking_unavailable", {"contact_id": contact_id, "reason": str(exc)})
            return
        self.event("auto_tracking_planned", {"contact_id": contact_id, "plan_id": response.get("plan_id"),
            "members": response.get("members"), "status": response.get("status")})

    def cells(self, pose):
        return [pose[0]/self.config.cell-.5, (self.config.height-pose[1])/self.config.cell-.5]

    @synchronized
    def frame(self):
        cell, width, height = self.config.cell, self.config.width, self.config.height
        searchable = [(c, r) for c in range(len(self.scan_times)) for r in range(len(self.scan_times[c])) if not any(
            math.hypot(max(c*cell, min(o["x"], (c+1)*cell))-o["x"],
                       max(height-(r+1)*cell, min(o["y"], height-r*cell))-o["y"]) <= o["radius"]+_PARTITION["obstacle_margin_m"]
            for o in self.obstacles)]
        information = information_fields(self.scan_times, self.contacts, searchable, self.sim_time, self.config)
        count = sum(self.scan_times[c][r] >= 0 for c, r in searchable)
        coverage = 100*count/max(1, len(searchable))
        window_min = self.config.coverage_window_min
        recent = sum(self.scan_times[c][r] >= 0 and 0 <= self.sim_time-self.scan_times[c][r] <= window_min*60 for c, r in searchable)
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
                "speed_mps": self.config.speed if action and self.status == "running" else 0,
                "heading_deg": math.degrees(u["pose"][2]) % 360,
                "sensor_mode": sensor_mode(action), "sensor_roles": sensor_roles(action), "task_phase": action.get("phase", kind),
                "effective_tracking": kind == "track" and action.get("phase") == "tracking" and sensor_mode(action) == "passive"
                    and self.contacts.get(action.get("contact_id"), {}).get("state") == "tracking"
                    and u["id"] in self.contacts.get(action.get("contact_id"), {}).get("observers", []),
                "trail": [self.cells(p) for p in u["trail"]], "status": action.get("phase", "acquiring") if kind == "track" else "searching" if mode == "coverage" else "transit" if kind in ("loiter", "exit") else "idle",
                "operation_mode": mode, "operational_status": "available", "control_owner": "system",
                "assigned_region_id": region["id"] if region else None, "team_id": action.get("plan_id"), "target_group_id": action.get("contact_id"),
                "task_visual": {"task_type": mode, "phase": kind, "route_source": "python", "route_status": "active" if action else "cleared"},
                "planned_path": [self.cells(p) for p in points[action.get("index", 0)::_RUNTIME["frame_path_stride"]]][:_RUNTIME["frame_path_points"]],
                "sensor_radius_cells": self.config.sensor_range/self.config.cell,
                "side_scan_inner_radius_cells": self.config.side_scan_inner_range/self.config.cell,
                "side_scan_half_angle_deg": self.config.side_scan_half_angle_deg,
                "forward_active_half_angle_deg": self.config.forward_active_half_angle_deg,
                "forward_passive_half_angle_deg": self.config.forward_passive_half_angle_deg})
        contacts = []
        for c in self.contacts.values():
            contacts.append({**c, "estimated_position": self.cells([c["x"], c["y"]]), "vessel_class": c.get("vessel_class", "underwater"),
                "estimated_velocity": [c["vx"]/cell, -c["vy"]/cell],
                "samples": [{**s, **({"position": self.cells([s["x"], s["y"]])} if "x" in s else {})} for s in c["samples"]]})
        plans = [self.summary(p) for p in self.plans.values()]
        current_contact = next(iter(self.contacts.values()), None)
        tracking_started = current_contact.get("tracking_started_at_s") if current_contact else None
        lost_started = current_contact.get("lost_started_at_s") if current_contact else None
        seconds = int(self.sim_time)
        return {"schema_version": "mission-frame/v2", "visual_schema_version": "mission-visual/v1", "episode_id": self.episode,
            "frame_id": self.frame_id, "sim_time_min": self.sim_time/60, "timestamp": f"{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}",
            "cycle": self.agent["cycle"], "runtime_status": self.status, "mission_revision": self.revision, "autonomy_mode": self.mode,
            "agent_status": copy.deepcopy(self.agent), "event_cursor": self.cursor, "information_source": "backend", "information_version": self.frame_id,
            "task_area": {"width_km": width/1000, "height_km": height/1000, "cell_size_km": cell/1000}, **information,
            "plugin_activity": plugin_frame_activity(self),
            "fleet_entry": {**self.fleet_entry, "position": self.cells(self.fleet_entry["position"])} if self.fleet_entry else None,
            "value_matrix": copy.deepcopy(information["target_info_matrix"]), "searchable_cells": len(searchable), "coverage_pct": coverage,
            "coverage_metrics": {"schema_version": "persistent-coverage/v1", "status": "ok", "as_of_min": self.sim_time/60,
                "primary_window_min": window_min, "primary_coverage_pct": 100*recent/max(1, len(searchable)),
                "fixed_searchable_area_km2": len(searchable)*(cell/1000)**2, "cumulative_pct": coverage, "unseen_pct": 100-coverage,
                "windows": [{"minutes": minutes, "window_complete": self.sim_time >= minutes*60,
                    "coverage_pct": 100*sum(0 <= self.scan_times[c][r] and self.sim_time-self.scan_times[c][r] <= minutes*60 for c, r in searchable)/max(1, len(searchable)),
                    "covered_area_km2": sum(0 <= self.scan_times[c][r] and self.sim_time-self.scan_times[c][r] <= minutes*60 for c, r in searchable)*(cell/1000)**2} for minutes in sorted({*_RUNTIME["replay_windows_min"], window_min})]},
            "uavs": boats, "contacts": contacts, "teams": [{"id": p["plan_id"], "members": [u for u in p.get("active_members", p["members"]) if self.active.get(u, {}).get("kind") == "track"], "task": p["kind"]} for p in plans if p["status"] == "active" and p["kind"] == "track"] +
                [{"id": r["id"], "members": [r["owner"]], "task": "search"} for r in self.regions],
            "plans": plans, "pending_approvals": [p for p in plans if p["status"] == "pending_approval"],
            "search_regions": [{"id": r["id"], "bbox": [r["bbox_m"][0]/cell, (height-r["bbox_m"][3])/cell, r["bbox_m"][2]/cell, (height-r["bbox_m"][1])/cell],
                "cells": r["cells"], "assigned_uav_id": r["owner"], "revision": self.region_revision, "priority": "medium",
                "completion_pct": 100*sum(self.scan_times[c][row] >= 0 for c, row in r["cells"])/max(1, len(r["cells"]))} for r in self.regions],
            "mission_metrics": {**self.metrics, "search_boats": sum(a["kind"] in ("search", "reacquire") for a in self.active.values()),
                "single_tracking_seconds": max(0, self.sim_time-tracking_started) if tracking_started is not None and current_contact["state"] == "tracking" else current_contact.get("last_tracking_duration_s") if current_contact else None,
                "current_lost_seconds": max(0, self.sim_time-lost_started) if lost_started is not None and current_contact["state"] == "lost" else None,
                "search_regions": len(self.regions), "coverage_pct": coverage, "unscanned_cells": len(searchable)-count,
                "recent_coverage_pct": 100*recent/max(1, len(searchable)), "coverage_window_min": window_min,
                "revisit_timeliness_pct": 100*recent/count if count else None,
                "handoff_success_rate": 100*self.metrics["handoff_count"]/self.metrics["handoff_attempts"] if self.metrics["handoff_attempts"] else None},
            "standing_policy": copy.deepcopy(self.standing_policy),
            "obstacle_observations": copy.deepcopy(getattr(self, "obstacle_observations", [])),
            "bearing_lines": [{"from": self.cells(s["observer_pose"]),
                "to": self.cells([s["observer_pose"][0]+self.config.sensor_range*math.cos(s["bearing_rad"]), s["observer_pose"][1]+self.config.sensor_range*math.sin(s["bearing_rad"])]),
                "observer_id": s["observer_id"], "contact_id": s["contact_id"]} for s in self.observations if s.get("mode") == "passive" and self.sim_time-s["time_s"] < 2],
            "track_regions": [], "events": self.events[-_RUNTIME["recent_frame_events"]:], "messages": copy.deepcopy(self.messages[-_RUNTIME["recent_frame_messages"]:]), "intents": copy.deepcopy(self.intents), "intent_statuses": [],
            "scenario_vessels": [], "ships": [], "markers": [], "vessel_mutation_allowed": False,
            "adversary_status": {"status": self.adversary["status"] if time.monotonic()-self.adversary["last_heartbeat"] < _RUNTIME["enemy_heartbeat_timeout_s"] else "offline", "model": self.config.model, "cycle": self.adversary["cycle"]},
            "target_maneuver_history": copy.deepcopy(self.adversary.get("maneuver_history", [])),
            "obstacles": [{"id": f"obstacle-{i}", "vertices": [self.cells([o["x"]+o["radius"]*math.cos(a*math.pi/8), o["y"]+o["radius"]*math.sin(a*math.pi/8)]) for a in range(16)]} for i, o in enumerate(self.obstacles)]}
