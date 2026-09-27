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
from .algorithms.tracking import tracking_control, follow_path


CHECKPOINT_FIELDS = tuple(("episode sim_time frame_id revision policy_version mode status uuvs targets obstacles active results plans contacts observations events cursor scan_times intents vessels tasks messages agent_jobs agent last_periodic sensor_enabled").split())
ALGORITHM_IDS = {"compute_task_allocation": "slot_assignment", "plan_path": "dubins_hybrid",
                 "plan_search": "strip_coverage", "plan_tracking": "distance_band"}


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
            self.agent["model"] = self.config.model
            for job in self.agent_jobs:
                if job["status"] == "running":
                    job["status"] = "failed"
                    job["error"] = "worker_restarted"
            self.event("runtime_restored", {})

    def _initial(self):
        self.episode = identifier("mission")
        self.sim_time = 0.0
        self.frame_id = 0
        self.revision = 0
        self.policy_version = 0
        self.mode = "assisted"
        self.status = "ready"
        self.uuvs = [{"id": f"UUV-{i+1}", "pose": [400.0, 400.0+i*400, 0.0], "trail": [], "curvature": 0.0} for i in range(8)]
        self.targets = [{"id": "TARGET-1", "pose": [900.0, 700.0, 0.5], "speed": 2.5}]
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
            "runtime_status": self.status, "uuvs": [{"id": u["id"], "pose": u["pose"], "task": self.active.get(u["id"], {}).get("kind", "idle")} for u in self.uuvs],
            "contacts": list(self.contacts.values()), "bounds": [0, 0, 4000, 4000], "obstacles": self.obstacles,
            "active_plans": [self.summary(p) for p in self.plans.values() if p["status"] in ("active", "pending_approval")],
            "intents": [{**intent, "bbox": [intent["bbox"][0]*100, 4000-intent["bbox"][3]*100,
                intent["bbox"][2]*100, 4000-intent["bbox"][1]*100]} for intent in self.intents],
            "constraints": {"max_team_size": 3, "min_turn_radius_m": 60, "speed_mps": 4}})

    @staticmethod
    def summary(result):
        return {key: value for key, value in result.items() if key not in ("routes", "points", "start_poses")}

    def _members(self, data):
        members = data.get("members", ["UUV-1", "UUV-2", "UUV-3"])
        if not isinstance(members, list) or not 1 <= len(members) <= 3 or not all(isinstance(member, str) for member in members) or len(set(members)) != len(members):
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
            episode, revision = self.episode, self.revision
            members = self._members(data) if name != "compute_task_allocation" else copy.deepcopy(self.uuvs)
            obstacles = copy.deepcopy(self.obstacles)
            selected_ids = {u["id"] for u in members}
            obstacles += [{"x": u["pose"][0], "y": u["pose"][1], "radius": self.config.separation+12}
                          for u in self.uuvs if u["id"] not in selected_ids and u["id"] not in self.active]
            contacts = copy.deepcopy(self.contacts)
            snapshot = self.frame_id
            assignments = {u["id"]: self.active.get(u["id"], {}).get("plan_id") for u in members}
        bbox = data.get("bbox", [250, 250, 3750, 3750])
        if name == "compute_task_allocation":
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
            result = plan_search(members, bbox, obstacles=obstacles)
            result["kind"] = "reacquire" if data.get("mode") == "reacquire" else "search"
            result["bbox"] = bbox
        elif name == "plan_tracking":
            contact_id = data.get("contact_id")
            contact = contacts.get(contact_id)
            if not contact or contact["state"] in ("tentative", "lost"):
                raise MissionError("confirmed_contact_required", 422)
            result = {"status": "succeeded", "kind": "track", "contact_id": contact_id,
                "routes": {}, "algorithm": "distance_band", "diagnostics": [], "bbox": [0, 0, 4000, 4000]}
        else:
            raise MissionError("unknown_calculation", 404)
        result.update({"result_id": identifier("result"), "episode_id": episode, "mission_revision": revision,
            "snapshot_id": snapshot, "members": [u["id"] for u in members], "created_at_s": self.sim_time,
            "start_poses": {u["id"]: u["pose"] for u in members}, "assignments": assignments})
        points = [point for route in result.get("routes", {}).values() for point in route]
        result["execution_domain"] = [max(0, min(p[0] for p in points)-100), max(0, min(p[1] for p in points)-100),
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
        if any(self.active.get(member, {}).get("plan_id") != result.get("assignments", {}).get(member) for member in members):
            errors.append("member_assignment_changed")
        if not 1 <= len(members) <= 3 or len(set(members)) != len(members):
            errors.append("invalid_team")
        if result.get("kind") not in ("search", "reacquire", "track", "path"):
            errors.append("not_executable")
        for points in result.get("routes", {}).values():
            if not points or not path_safe(points, self.obstacles, result.get("execution_domain", (0, 0, 4000, 4000))):
                errors.append("unsafe_route")
            elif result.get("kind") in ("search", "reacquire") and (math.dist(points[0][:2], points[-1][:2]) > 1 or abs(math.remainder(points[0][2]-points[-1][2], 2*math.pi)) > .05):
                errors.append("closed_continuation_required")
        if result.get("kind") == "path":
            errors.append("terminal_continuation_required_use_search")
        for u in self.uuvs:
            if u["id"] in members and math.dist(u["pose"][:2], result["start_poses"][u["id"]][:2]) > 200:
                errors.append("start_pose_changed_replan")
        if result.get("kind") == "track" and self.contacts.get(result.get("contact_id"), {}).get("state") in (None, "lost"):
            errors.append("contact_lost")
        risk = 0.65 if result.get("kind") in ("track", "reacquire") else 0.2
        return {"result_id": result["result_id"], "valid": not errors, "errors": sorted(set(errors)), "risk": risk,
            "requires_approval": self.mode == "request" or (self.mode == "assisted" and risk > 0.55)}

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
                "fallback": "repeat_approved_closed_route" if result["kind"] in ("search", "reacquire") else "protective_pause",
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
        for old in self.plans.values():
            if old["plan_id"] != plan["plan_id"] and old["status"] == "active" and set(old["members"]) & set(plan["members"]):
                remaining = [member for member in old["members"] if member not in plan["members"] and self.active.get(member, {}).get("plan_id") == old["plan_id"]]
                old["active_members"] = remaining
                if not remaining:
                    old["status"] = "superseded"
        for index, member in enumerate(plan["members"]):
            self.active[member] = {"plan_id": plan["plan_id"], "kind": plan["kind"], "contact_id": plan.get("contact_id"),
                "points": plan.get("routes", {}).get(member, []), "index": 0, "slot": index}
            self.active[member]["execution_domain"] = plan["execution_domain"]
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
    def queue_agent(self, text, source="human"):
        if self.status == "stopped":
            raise MissionError("mission_stopped")
        if source != "human" and any(j["status"] == "queued" and j["source"] != "human" for j in self.agent_jobs):
            return next(j for j in self.agent_jobs if j["status"] == "queued" and j["source"] != "human")
        if sum(j["status"] in ("queued", "running") for j in self.agent_jobs) >= 12:
            if source != "human":
                self.event("agent_wakeup_deferred", {"source": source, "reason": "queue_full"})
                return None
            raise MissionError("agent_queue_full", 429)
        job = {"run_id": identifier("run"), "episode_id": self.episode, "status": "queued", "text": text[:4000], "source": source}
        self.agent_jobs.append(job)
        self.agent_jobs = self.agent_jobs[-100:]
        if source == "human":
            self.messages.append({"id": identifier("message"), "role": "user", "text": text[:4000], "time": self.sim_time})
            self.messages = self.messages[-100:]
        self.event("agent_queued", {"run_id": job["run_id"], "source": source})
        self.save()
        return job

    @synchronized
    def cancel_agent(self, run_id=None):
        for job in self.agent_jobs:
            if (not run_id or job["run_id"] == run_id) and job["status"] in ("queued", "running"):
                job["status"] = "cancelled"
        self.agent["status"] = "idle"
        self.save()

    def _observe(self):
        for target in self.targets:
            seen = [u for u in self.uuvs if u["id"] in self.active and math.dist(u["pose"][:2], target["pose"][:2]) <= self.config.sensor_range]
            contact = self.contacts.get(target["id"])
            ais = target.get("vessel_class") in ("type_i", "type_ii") and target.get("ais_enabled", False)
            if ais or (seen and self.sensor_enabled):
                x, y = [target["pose"][i]+self.rng.gauss(0, 3) for i in (0, 1)]
                if not contact:
                    contact = {"contact_id": target["id"], "x": x, "y": y, "vx": 0.0, "vy": 0.0,
                        "state": "tentative", "last_seen": self.sim_time, "hits": [], "samples": [], "uncertainty_m": 10.0,
                        "vessel_class": target.get("vessel_class", "underwater")}
                    self.contacts[target["id"]] = contact
                    self.event("contact_created", {"contact_id": target["id"]})
                dt = max(1, self.sim_time-contact["last_seen"])
                contact["vx"] = 0.8*contact["vx"]+0.2*(x-contact["x"])/dt
                contact["vy"] = 0.8*contact["vy"]+0.2*(y-contact["y"])/dt
                contact.update(x=x, y=y, last_seen=self.sim_time, uncertainty_m=10.0)
                if ais:
                    contact.update(ais_mmsi=target["ais_mmsi"], ais_synthetic=True)
                contact["hits"] = [t for t in contact["hits"] if self.sim_time-t <= 5]+[self.sim_time]
                old = contact["state"]
                if len(contact["hits"]) >= 3:
                    contact["state"] = "tracking" if any(a.get("contact_id") == target["id"] and a["kind"] == "track" for a in self.active.values()) else "confirmed"
                    if old in ("tentative", "lost", "reacquiring"):
                        self.event("target_found", {"contact_id": target["id"]})
                        self.queue_agent(f"Contact {target['id']} confirmed. Review tracking allocation.", "target_found")
                sample = {"sample_id": identifier("obs"), "contact_id": target["id"], "x": x, "y": y, "time_s": self.sim_time,
                    "observers": [] if ais else [u["id"] for u in seen], "source": "ais" if ais else "sensor"}
                contact["samples"] = (contact["samples"]+[sample])[-20:]
                self.observations = (self.observations+[sample])[-500:]
            elif contact:
                contact["uncertainty_m"] = 10+3*(self.sim_time-contact["last_seen"])
                if self.sim_time-contact["last_seen"] > 15 and contact["state"] != "lost":
                    contact["state"] = "lost"
                    self.event("target_lost", {"contact_id": target["id"]})
                    self.queue_agent(f"Contact {target['id']} lost. Plan reacquisition using last estimate.", "target_lost")
        for u in self.uuvs:
            if u["id"] not in self.active:
                continue
            x, y = u["pose"][:2]
            for col in range(max(0, int((x-350)//100)), min(40, int((x+350)//100)+1)):
                for row in range(max(0, int((4000-y-350)//100)), min(40, int((4000-y+350)//100)+1)):
                    if math.hypot((col+.5)*100-x, 4000-(row+.5)*100-y) <= 350:
                        self.scan_times[col][row] = self.sim_time

    @synchronized
    def tick(self):
        if self.status != "running":
            return
        next_poses = {}
        for u in self.uuvs:
            action = self.active.get(u["id"])
            if not action:
                next_poses[u["id"]] = u["pose"]
                continue
            pose = u["pose"]
            if action["kind"] == "track":
                estimate = self.contacts.get(action.get("contact_id"))
                if not estimate or estimate["state"] == "lost":
                    self.pause("safety_tracking_contact_lost")
                    return
                preferred = tracking_control(pose, estimate, slot=action["slot"])
            elif action.get("points"):
                points = action["points"]
                index = action["index"]
                end = min(len(points), index+50)
                nearest = min(range(index, end), key=lambda n: math.dist(pose[:2], points[n][:2]))
                action["index"] = nearest
                if nearest >= len(points)-3:
                    if math.dist(points[0][:2], points[-1][:2]) > 1:
                        self.pause("safety_no_authorized_continuation")
                        return
                    action["index"] = 0
                    preferred = follow_path(pose, points[:60])
                    self.event("search_complete", {"uav_id": u["id"], "plan_id": action["plan_id"]})
                    self.queue_agent("Search segment complete; plan revisit if needed.", "search_complete")
                else:
                    preferred = follow_path(pose, points[nearest:nearest+60])
            else:
                self.pause("safety_no_authorized_continuation")
                return
            candidates = sorted(set([max(-1/60, min(1/60, preferred))]+[n/360 for n in range(-6, 7)]), key=lambda k: abs(k-preferred))
            chosen = None
            for curvature in candidates:
                prediction = [integrate(pose, 4, curvature, t) for t in (0, 2, 4, 6)]
                if not path_safe(prediction, self.obstacles, action.get("execution_domain", (0, 0, 4000, 4000)), margin=10):
                    continue
                if any(math.hypot(point[0]-contact["x"]-contact["vx"]*t, point[1]-contact["y"]-contact["vy"]*t) < self.config.separation+min(200, contact["uncertainty_m"])
                       for contact in self.contacts.values() if contact["state"] != "lost"
                       for t, point in zip((0, 2, 4, 6), prediction)):
                    continue
                safe = True
                for other in self.uuvs:
                    if other["id"] == u["id"]:
                        continue
                    for t, point in zip((0, 2, 4, 6), prediction):
                        moving = other["id"] in self.active
                        other_pose = integrate(other["pose"], 4, other.get("curvature", 0), t) if moving else other["pose"]
                        if math.dist(point[:2], other_pose[:2]) < self.config.separation+4:
                            safe = False
                            break
                    if not safe:
                        break
                if safe:
                    chosen = curvature
                    break
            if chosen is None:
                self.pause("safety_infeasible")
                return
            u["curvature"] = chosen
            next_poses[u["id"]] = integrate(pose, 4, chosen, self.config.dt)
        # A common tick validates all proposed movements before committing any pose.
        for index, first in enumerate(self.uuvs):
            for second in self.uuvs[index+1:]:
                if math.dist(next_poses[first["id"]][:2], next_poses[second["id"]][:2]) < 40:
                    self.pause("safety_infeasible")
                    return
        target_poses = {}
        for target in self.targets:
            x, y, heading = target["pose"]
            desired = math.atan2(2000-y, 2000-x) if min(x, y, 4000-x, 4000-y) < 400 else heading+0.003
            error = (desired-heading+math.pi) % (2*math.pi)-math.pi
            target_poses[target["id"]] = integrate(target["pose"], target["speed"], max(-1/80, min(1/80, error/100)), self.config.dt)
        # Truth is used only by this simulator interlock, never to choose a control.
        if any(math.dist(pose[:2], target_pose[:2]) < self.config.separation for pose in next_poses.values() for target_pose in target_poses.values()):
            self.pause("safety_target_collision")
            return
        for u in self.uuvs:
            u["pose"] = next_poses[u["id"]]
        for target in self.targets:
            target["pose"] = target_poses[target["id"]]
        self.sim_time = round(self.sim_time+self.config.dt, 6)
        self.frame_id += 1
        if self.frame_id % 5 == 0:
            self._observe()
            for u in self.uuvs:
                u["trail"] = (u["trail"]+[u["pose"][:2]])[-300:]
        for vessel in self.vessels:
            target = next((target for target in self.targets if target["id"] == vessel["scenario_entity_id"]), None)
            if target:
                contact = self.contacts.get(target["id"], {})
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
        information = [[0 if t < 0 else math.exp(-math.log(2)*(self.sim_time-t)/1800) for t in col] for col in self.scan_times]
        count = sum(t >= 0 for col in self.scan_times for t in col)
        boats = []
        for u in self.uuvs:
            action = self.active.get(u["id"], {})
            kind = action.get("kind", "idle")
            mode = "track" if kind == "track" else "coverage" if kind in ("search", "reacquire", "path") else "idle"
            points = action.get("points", [])
            boats.append({"id": u["id"], "position": self.cells(u["pose"]), "heading_rad": -u["pose"][2],
                "trail": [self.cells(p) for p in u["trail"]], "status": "tracking" if kind == "track" else "searching" if mode == "coverage" else "transit" if kind == "loiter" else "idle",
                "operation_mode": mode, "operational_status": "available", "control_owner": "system",
                "assigned_region_id": action.get("plan_id"), "team_id": action.get("plan_id"), "target_group_id": action.get("contact_id"),
                "task_visual": {"task_type": mode, "phase": kind, "route_source": "python", "route_status": "active" if action else "cleared"},
                "planned_path": [self.cells(p) for p in points[action.get("index", 0)::10]][:400], "sensor_radius_cells": 3.5})
        contacts = []
        for c in self.contacts.values():
            contacts.append({**c, "estimated_position": self.cells([c["x"], c["y"]]), "vessel_class": c.get("vessel_class", "underwater"),
                "estimated_velocity": [c["vx"]/100, -c["vy"]/100],
                "samples": [{**s, "position": self.cells([s["x"], s["y"]])} for s in c["samples"]]})
        plans = [self.summary(p) for p in self.plans.values()]
        seconds = int(self.sim_time)
        return {"schema_version": "mission-frame/v2", "visual_schema_version": "mission-visual/v1", "episode_id": self.episode,
            "frame_id": self.frame_id, "sim_time_min": self.sim_time/60, "timestamp": f"{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}",
            "cycle": self.agent["cycle"], "runtime_status": self.status, "mission_revision": self.revision, "autonomy_mode": self.mode,
            "agent_status": copy.deepcopy(self.agent), "event_cursor": self.cursor, "information_source": "backend", "information_version": self.frame_id,
            "task_area": {"width_km": 4, "height_km": 4, "cell_size_km": .1}, "info_matrix": information,
            "value_matrix": [[.1]*40 for _ in range(40)], "searchable_cells": 1600, "coverage_pct": count/16,
            "coverage_metrics": {"schema_version": "persistent-coverage/v1", "status": "ok", "as_of_min": self.sim_time/60,
                "fixed_searchable_area_km2": 16, "cumulative_pct": count/16, "unseen_pct": 100-count/16,
                "windows": [{"minutes": minutes, "window_complete": self.sim_time >= minutes*60,
                    "coverage_pct": sum(t >= 0 and self.sim_time-t <= minutes*60 for col in self.scan_times for t in col)/16,
                    "covered_area_km2": sum(t >= 0 and self.sim_time-t <= minutes*60 for col in self.scan_times for t in col)*.01} for minutes in (30, 60, 120)]},
            "uavs": boats, "contacts": contacts, "teams": [{"id": p["plan_id"], "members": p.get("active_members", p["members"]), "task": p["kind"]} for p in plans if p["status"] == "active"],
            "plans": plans, "pending_approvals": [p for p in plans if p["status"] == "pending_approval"],
            "search_regions": [{"id": p["plan_id"], "bbox": [p["bbox"][0]/100, (4000-p["bbox"][3])/100, p["bbox"][2]/100, (4000-p["bbox"][1])/100], "assigned_uav_id": ",".join(p.get("active_members", p["members"])), "priority": "medium", "completion_pct": count/16} for p in plans if p["status"] == "active" and p.get("bbox") and p.get("kind") in ("search", "reacquire")],
            "track_regions": [], "events": self.events[-120:], "intents": copy.deepcopy(self.intents), "intent_statuses": [],
            "scenario_vessels": copy.deepcopy(self.vessels), "ships": [], "markers": [], "vessel_mutation_allowed": self.status != "stopped",
            "obstacles": [{"id": f"obstacle-{i}", "vertices": [self.cells([o["x"]+o["radius"]*math.cos(a*math.pi/8), o["y"]+o["radius"]*math.sin(a*math.pi/8)]) for a in range(16)]} for i, o in enumerate(self.obstacles)]}
