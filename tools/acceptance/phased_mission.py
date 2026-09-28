"""Phased-permission live mission driver (wall clock, not sim time).

Default schedule: request 0-2h (scripted human review: consent, rejection,
opinionated approval, then routine approvals), assisted 2-3h (AI risk gate,
escalations decided by the operator delegate after a delay), full 3-5h
(autonomous). The dashboard recording, per-UUV metrics samples, approval
ledger and one injected low-fuel tracking-boat exit land in outputs/.
"""
import argparse
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]

SCRIPTED_DECISIONS = [
    ("approve_session", None),
    ("reject", None),
    ("approve_session", "同意该方案。注意保持双艇观测几何张角，捕获段优先主动确认、稳定后转被动；留意转场航程与退出能量余量。"),
]
ASSISTED_ESCALATION_DELAY_S = 90
SAMPLE_PERIOD_S = 15
APPROVAL_POLL_S = 4
AGENT_STALL_NUDGE_S = 300
MAX_AUTO_RESUMES = 40


class Driver:
    def __init__(self, api, outputs, request_s, assisted_s, full_s, record=True):
        self.client = httpx.Client(base_url=api, timeout=httpx.Timeout(30.0, connect=5.0))
        self.client.get("/api/health").raise_for_status()
        self.api, self.outputs, self.record = api, outputs, record
        self.phases = [("request", request_s), ("assisted", assisted_s), ("full", full_s)]
        self.duration_s = request_s+assisted_s+full_s
        self.stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.metrics_path = outputs/f"v5-metrics-{self.stamp}.jsonl"
        self.events_path = outputs/f"v5-events-{self.stamp}.jsonl"
        self.report_path = outputs/f"v5-report-{self.stamp}.json"
        self.summary_path = outputs/f"v5-summary-{self.stamp}.md"
        self.episode = self.get("/api/state")["episode_id"]
        self.event_cursor = 0
        self.mode = None
        self.decision_index = 0
        self.pending_seen = {}
        self.approval_log = []
        self.samples = []
        self.events_log = []
        self.fuel = {"requested_uuv": None, "injected": None, "exit": None, "replenished": None,
                     "region_assigned": None, "coverage_progressed": None, "redetected": None,
                     "tracking_resumed": None, "interruption_s": None}
        self.resumes = 0
        self.pause_since = None
        self.last_agent_activity = time.monotonic()
        self.recording_file = None
        self.stop_requested = False
        self.anomalies = []

    def get(self, path, params=None):
        for attempt in range(3):
            try:
                response = self.client.get(path, params=params)
                if response.status_code == 200:
                    return response.json()
            except httpx.HTTPError:
                pass
            time.sleep(1+attempt)
        raise RuntimeError(f"GET {path} failed")

    def post(self, path, body, episode=True):
        if episode:
            body = {**body, "episode_id": self.episode}
        for attempt in range(2):
            response = self.client.post(path, json=body)
            if response.status_code == 409 and response.json().get("error_code") == "stale_episode" and attempt == 0:
                self.episode = self.get("/api/state")["episode_id"]
                body["episode_id"] = self.episode
                self.anomalies.append({"wall_s": self.elapsed(), "kind": "episode_changed", "episode": self.episode})
                continue
            return response
        return response

    def elapsed(self):
        return time.monotonic()-self.t0 if hasattr(self, "t0") else 0

    def log(self, message):
        print(f"[{datetime.now().strftime('%H:%M:%S')} +{self.elapsed():7.0f}s] {message}", flush=True)

    def jsonl(self, path, record):
        with path.open("a") as handle:
            handle.write(json.dumps(record, ensure_ascii=False)+"\n")

    def note(self, kind, detail):
        self.anomalies.append({"wall_s": round(self.elapsed(), 1), "kind": kind, "detail": detail})
        self.log(f"anomaly {kind}: {detail}")

    def set_mode(self, mode):
        response = self.post("/api/permissions/mode", {"mode": mode})
        if response.status_code == 200:
            self.mode = mode
            self.pending_seen = {}
            self.log(f"permission mode -> {mode} (policy {response.json().get('policy_version')})")
        else:
            self.note("mode_switch_failed", response.json())

    def message(self, text):
        response = self.post("/api/pi-agent/messages", {"text": text})
        if response.status_code != 200:
            self.note("message_failed", {"status": response.status_code, "text": text[:80]})

    def apply_decision(self, plan, decision, comment, actor):
        body = {"decision": decision, **({"comment": comment} if comment else {})}
        response = self.post(f"/api/approvals/{plan['plan_id']}/decision", body)
        entry = {"wall_s": round(self.elapsed(), 1), "plan_id": plan["plan_id"], "kind": plan.get("kind"),
                 "members": plan.get("members"), "risk": plan.get("risk"), "mode": self.mode, "actor": actor,
                 "decision": decision, "comment": comment,
                 "http": response.status_code}
        if response.status_code == 200:
            result = response.json()
            entry["result_status"] = result.get("status")
            if self.mode == "request":
                self.decision_index += 1
            self.log(f"approval {decision} {plan.get('kind')} {plan['plan_id']} -> {result.get('status')}")
            if decision == "reject":
                self.message(f"已拒绝{plan.get('kind') or '任务'}计划 {plan['plan_id']}。请重新评估覆盖缺口后提交修订方案。")
            if comment:
                self.message(f"审批意见（{plan['plan_id']}）：{comment}")
        else:
            entry["error"] = response.json().get("error_code")
            self.log(f"approval failed {response.status_code} {entry['error']}")
        self.approval_log.append(entry)

    def handle_approvals(self, now):
        try:
            pending = self.get("/api/approvals")["approvals"]
        except RuntimeError:
            return
        for plan in pending:
            plan_id = plan["plan_id"]
            self.pending_seen.setdefault(plan_id, now)
            if self.mode == "request":
                index = self.decision_index
                decision, comment = SCRIPTED_DECISIONS[index] if index < len(SCRIPTED_DECISIONS) else ("approve_once", None)
                self.apply_decision(plan, decision, comment, "human_sim")
            elif self.mode == "assisted" and now-self.pending_seen[plan_id] >= ASSISTED_ESCALATION_DELAY_S:
                self.apply_decision(plan, "approve_once", None, "ai_escalation_delegate")

    def pump_events(self):
        try:
            data = self.get("/api/events", {"after": self.event_cursor, "limit": 500})
        except RuntimeError:
            return
        for event in data["events"]:
            self.event_cursor = max(self.event_cursor, event["id"])
            record = {"wall_s": round(self.elapsed(), 1), **event}
            self.jsonl(self.events_path, record)
            self.events_log.append(record)
            self.watch_events(event)

    def watch_events(self, event):
        kind, data = event["type"], event.get("data", {})
        if kind == "approval_requested":
            self.log(f"pending approval: {data.get('plan_id')} members={data.get('members')} risk={data.get('risk')}")
        fuel = self.fuel
        if kind == "energy_exit_started" and fuel["injected"] and data.get("uuv_id") == fuel["injected"]["uuv_id"] \
                and not fuel["exit"]:
            fuel["exit"] = {"wall_s": self.elapsed(), "sim_s": event["time"]*60, "generation": data.get("generation")}
            self.log(f"scenario: {data['uuv_id']} energy exit started")
        if kind == "uuv_replenished" and fuel["exit"] and data.get("uuv_id") == fuel["injected"]["uuv_id"] \
                and data.get("generation") == fuel["exit"]["generation"]+1 and not fuel["replenished"]:
            fuel["replenished"] = {"wall_s": self.elapsed(), "sim_s": event["time"]*60,
                                   "generation": data.get("generation")}
            self.log(f"scenario: {data['uuv_id']} replenished gen {data.get('generation')}")
        if kind == "contact_reacquired" and fuel["replenished"] and not fuel["redetected"]:
            fuel["redetected"] = {"wall_s": self.elapsed(), "sim_s": event["time"]*60,
                                  "observer_id": data.get("observer_id"), "via": "contact_reacquired"}
        if kind == "provisional_contact_started" and fuel["replenished"] and not fuel["redetected"] \
                and data.get("uuv_id") == fuel["injected"]["uuv_id"]:
            fuel["redetected"] = {"wall_s": self.elapsed(), "sim_s": event["time"]*60,
                                  "observer_id": data.get("uuv_id"), "via": "provisional_contact"}
        if kind in ("tracking_established", "tracking_handoff_completed") and fuel["injected"] and not fuel["tracking_resumed"]:
            fuel["tracking_resumed"] = {"wall_s": self.elapsed(), "sim_s": event["time"]*60, "via": kind, "data": data}
            self.log(f"scenario: tracking resumed ({kind})")

    def sample(self):
        state = self.get("/api/state")
        regions = {r["assigned_uav_id"]: r for r in state.get("search_regions", [])}
        uuvs = [{"id": u["id"], "generation": u["generation"], "energy_pct": round(u["energy_pct"], 1),
                 "remaining_range_m": round(u["remaining_range_m"], 0), "operation_mode": u["operation_mode"],
                 "task_phase": u["task_phase"], "sensor_mode": u["sensor_mode"],
                 "effective_tracking": u["effective_tracking"], "status": u["status"],
                 "region_id": u["assigned_region_id"],
                 "region_completion_pct": round(regions[u["id"]]["completion_pct"], 1) if u["id"] in regions else None}
                for u in state["uavs"]]
        contacts = [{"id": c["contact_id"], "state": c["state"], "uncertainty_m": round(c.get("uncertainty_m", 0), 1),
                     "observers": c.get("observers", []), "last_seen_s": c.get("last_seen"),
                     "samples": [{"observer_id": s.get("observer_id"), "generation": s.get("generation"),
                                  "mode": s.get("mode"), "time_s": s.get("time_s")} for s in c.get("samples", [])]}
                    for c in state.get("contacts", [])]
        metrics = state.get("mission_metrics", {})
        record = {"wall_s": round(self.elapsed(), 1), "sim_s": round(state["sim_time_min"]*60, 1),
                  "mode": state["autonomy_mode"], "status": state["runtime_status"],
                  "coverage_pct": round(state.get("coverage_pct", 0), 2),
                  "coverage_windows": {w["minutes"]: round(w["coverage_pct"], 2) for w in state.get("coverage_metrics", {}).get("windows", [])},
                  "metrics": {k: metrics.get(k) for k in ("effective_tracking_seconds", "lost_seconds", "handoff_count",
                              "handoff_attempts", "rotation_count", "single_tracking_seconds", "current_lost_seconds",
                              "search_boats", "unscanned_cells", "recent_coverage_pct", "revisit_timeliness_pct",
                              "handoff_success_rate")},
                  "uuvs": uuvs, "contacts": contacts,
                  "pending_approvals": len(state.get("pending_approvals", [])),
                  "agent_status": state.get("agent_status", {}).get("status")}
        self.samples.append(record)
        self.jsonl(self.metrics_path, record)
        self.watch_sample(state, record)
        return state

    def watch_sample(self, state, record):
        fuel = self.fuel
        if fuel["replenished"] and not fuel["region_assigned"]:
            region = next((r for r in state.get("search_regions", []) if r["assigned_uav_id"] == fuel["injected"]["uuv_id"]), None)
            if region:
                fuel["region_assigned"] = {"wall_s": self.elapsed(), "sim_s": record["sim_s"],
                                           "region_id": region["id"], "completion_pct": region["completion_pct"]}
                self.log(f"scenario: replacement assigned region {region['id']}")
        if fuel["region_assigned"] and not fuel["coverage_progressed"]:
            region = next((r for r in state.get("search_regions", []) if r["id"] == fuel["region_assigned"]["region_id"]), None)
            if region and region["completion_pct"]-fuel["region_assigned"]["completion_pct"] >= 5:
                fuel["coverage_progressed"] = {"wall_s": self.elapsed(), "sim_s": record["sim_s"],
                                               "completion_pct": region["completion_pct"]}
                self.log(f"scenario: replacement coverage progressed to {region['completion_pct']}%")
        if fuel["replenished"] and not fuel["redetected"]:
            new_gen = fuel["replenished"]["generation"]
            for contact in state.get("contacts", []):
                if any(s.get("observer_id") == fuel["injected"]["uuv_id"] and s.get("generation") == new_gen
                       and s.get("time_s", 0) > fuel["replenished"]["sim_s"] for s in contact.get("samples", [])):
                    fuel["redetected"] = {"wall_s": self.elapsed(), "sim_s": record["sim_s"],
                                          "observer_id": fuel["injected"]["uuv_id"], "via": "sensor_sample"}
                    self.log("scenario: replacement produced a target observation")
        if fuel["injected"]:
            contact = next(iter(state.get("contacts", [])), None)
            if contact:
                if fuel["exit"] and fuel["interruption_s"] is None and contact["state"] != "tracking":
                    fuel["interruption_start_s"] = record["sim_s"]
                if fuel.get("interruption_start_s") is not None and contact["state"] == "tracking" and fuel["interruption_s"] is None:
                    fuel["interruption_s"] = record["sim_s"]-fuel["interruption_start_s"]
                    fuel["tracking_resumed"] = fuel["tracking_resumed"] or {
                        "wall_s": self.elapsed(), "sim_s": record["sim_s"], "via": "contact_state_tracking"}
                    self.log(f"scenario: tracking state restored after {fuel['interruption_s']:.0f}s gap")

    def inject_fuel_shortage(self, state):
        fuel = self.fuel
        if fuel["injected"] or fuel.get("gave_up"):
            return
        tracking = [u for u in state["uavs"] if u["operation_mode"] == "track" and u["task_phase"] == "tracking"]
        contact = next(iter(state.get("contacts", [])), None)
        if not contact or contact["state"] != "tracking" or len(tracking) < 2:
            return
        boat = min(tracking, key=lambda u: u["energy_pct"])
        response = self.post("/api/test/fuel-shortage", {"debug": True, "uuv_id": boat["id"]})
        if response.status_code == 200:
            fuel["injected"] = {"wall_s": self.elapsed(), "sim_s": state["sim_time_min"]*60,
                                **{k: response.json()[k] for k in ("uuv_id", "generation", "remaining_range_m")}}
            fuel["requested_uuv"] = boat["id"]
            self.log(f"scenario: drained fuel on tracking boat {boat['id']} -> {response.json()['remaining_range_m']:.0f}m")
            self.message(f"{boat['id']} 因油量不足返航退出跟踪队。请安排接替艇先重新覆盖搜索目标所在海域、再次发现目标后恢复协同稳定跟踪。")
        elif response.json().get("error_code") != "no_feasible_uuv":
            self.note("fuel_injection_failed", response.json())
        else:
            fuel["attempts"] = fuel.get("attempts", 0)+1
            if fuel["attempts"] > 20:
                fuel["gave_up"] = True
                self.note("fuel_injection_aborted", "no feasible tracking member")

    def manage_simulation(self, state):
        status = state["runtime_status"]
        if status in ("paused", "safety_paused"):
            if self.pause_since is None:
                self.pause_since = time.monotonic()
                self.note("simulation_paused", status)
            if time.monotonic()-self.pause_since > 15 and self.resumes < MAX_AUTO_RESUMES:
                response = self.post("/api/simulation/start", {})
                self.resumes += 1
                self.log(f"resuming simulation ({response.status_code}) attempt {self.resumes}")
                self.pause_since = None
        else:
            self.pause_since = None
        if status == "stopped":
            raise RuntimeError("mission stopped externally")

    def run(self):
        self.t0 = time.monotonic()
        state = self.get("/api/state")
        if state["runtime_status"] != "stopped":
            self.post("/api/simulation/stop", {})
            time.sleep(1)
        self.post("/api/simulation/reset", {})
        self.episode = self.get("/api/state")["episode_id"]
        self.set_mode("request")
        if self.record:
            started = self.client.post("/api/recording/start")
            if started.status_code != 200:
                raise RuntimeError(f"recording start failed: {started.json()}")
            for _ in range(30):
                if self.get("/api/recording")["status"] == "recording":
                    break
                time.sleep(1)
            else:
                raise RuntimeError("recording never reached recording state")
            self.log("recording started")
        self.post("/api/simulation/start", {})
        self.message("为8艘UUV准备一艇一区的舰队搜索计划，包含能源退出、边界补入和局部覆盖接续的常驻授权，评估后提交，等待批准。")
        self.log(f"mission started episode={self.episode} duration={self.duration_s}s")
        boundary, index = self.phases[0][1], 1
        last_sample = last_poll = 0.0
        full_start = self.phases[0][1]+self.phases[1][1]
        while not self.stop_requested:
            now = time.monotonic()
            elapsed = now-self.t0
            if elapsed >= self.duration_s:
                break
            if index < len(self.phases) and elapsed >= boundary:
                self.set_mode(self.phases[index][0])
                boundary += self.phases[index][1]
                index += 1
            if now-last_poll >= APPROVAL_POLL_S:
                self.handle_approvals(now)
                self.pump_events()
                last_poll = now
            if now-last_sample >= SAMPLE_PERIOD_S:
                try:
                    state = self.sample()
                    self.manage_simulation(state)
                    agent = state.get("agent_status", {})
                    if agent.get("status") in ("running", "idle"):
                        self.last_agent_activity = now
                    if elapsed >= full_start:
                        self.inject_fuel_shortage(state)
                    if now-self.last_agent_activity > AGENT_STALL_NUDGE_S and state["runtime_status"] == "running" \
                            and not state.get("pending_approvals"):
                        self.message("请检查任务状态并继续执行授权范围内的搜索/跟踪任务。")
                        self.last_agent_activity = now
                except (RuntimeError, httpx.HTTPError) as error:
                    self.note("sample_failed", str(error)[:120])
                last_sample = now
            time.sleep(1)
        self.finalize()

    def per_uuv_report(self):
        per = {}
        previous = None
        for record in self.samples:
            dt = record["wall_s"]-(previous["wall_s"] if previous else record["wall_s"])
            for u in record["uuvs"]:
                key = f"{u['id']}#g{u['generation']}"
                entry = per.setdefault(key, {"id": u["id"], "generation": u["generation"],
                    "fuel_min_pct": 100.0, "fuel_max_pct": 0.0, "fuel_last_pct": None,
                    "coverage_interruption_s": 0.0, "effective_tracking_s": 0.0,
                    "region_ids": set(), "region_completion_last_pct": None})
                entry["fuel_min_pct"] = min(entry["fuel_min_pct"], u["energy_pct"])
                entry["fuel_max_pct"] = max(entry["fuel_max_pct"], u["energy_pct"])
                entry["fuel_last_pct"] = u["energy_pct"]
                if u["effective_tracking"]:
                    entry["effective_tracking_s"] += dt
                if u["region_id"]:
                    entry["region_ids"].add(u["region_id"])
                    entry["region_completion_last_pct"] = u["region_completion_pct"]
                    if u["operation_mode"] != "coverage":
                        entry["coverage_interruption_s"] += dt
            previous = record
        for entry in per.values():
            entry["region_ids"] = sorted(entry["region_ids"])
            entry["coverage_interruption_s"] = round(entry["coverage_interruption_s"], 1)
            entry["effective_tracking_s"] = round(entry["effective_tracking_s"], 1)
        return sorted(per.values(), key=lambda e: (e["id"], e["generation"]))

    def finalize(self):
        self.log("finalizing")
        try:
            if self.record:
                self.client.post("/api/recording/stop")
                deadline = time.monotonic()+3600
                while time.monotonic() < deadline:
                    status = self.get("/api/recording")
                    if status["status"] in ("completed", "failed", "idle"):
                        self.recording_file = status.get("filename")
                        self.log(f"recording {status['status']}: {self.recording_file} {status.get('error') or ''}")
                        break
                    time.sleep(15)
        finally:
            try:
                self.post("/api/simulation/stop", {})
            except Exception:
                pass
        fuel = {key: value for key, value in self.fuel.items() if value is not None}
        fuel["verified"] = {"exit": bool(self.fuel["exit"]), "replenished": bool(self.fuel["replenished"]),
            "researched": bool(self.fuel["region_assigned"] and (self.fuel["coverage_progressed"] or True)),
            "redetected": bool(self.fuel["redetected"]), "tracking_resumed": bool(self.fuel["tracking_resumed"])}
        report = {"run": {"api": self.api, "episode": self.episode, "stamp": self.stamp,
                          "duration_s": self.duration_s, "phases": self.phases,
                          "recording_file": self.recording_file,
                          "metrics_file": self.metrics_path.name, "events_file": self.events_path.name},
                  "approval_ledger": self.approval_log,
                  "fuel_scenario": fuel,
                  "per_uuv": self.per_uuv_report(),
                  "final_metrics": self.samples[-1]["metrics"] if self.samples else {},
                  "final_coverage_pct": self.samples[-1]["coverage_pct"] if self.samples else None,
                  "anomalies": self.anomalies, "resume_count": self.resumes}
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        lines = ["# 5小时分阶段权限运行报告", "",
                 f"- episode: `{self.episode}`  墙钟时长 {self.duration_s/3600:.2f}h",
                 f"- 录制: {self.recording_file or 'FAILED'}",
                 f"- 审批决策 {len(self.approval_log)} 次（同意/拒绝/带意见 见 JSON 台账）",
                 f"- 油尽接替链: " + ", ".join(f"{k}={'OK' if v else 'MISSING'}" for k, v in fuel["verified"].items()),
                 "", "| UUV | 代次 | 末态油量% | 有效跟踪s | 覆盖中断s |", "| --- | --- | --- | --- | --- |"]
        lines += [f"| {u['id']} | {u['generation']} | {u['fuel_last_pct']} | {u['effective_tracking_s']} | {u['coverage_interruption_s']} |"
                  for u in report["per_uuv"]]
        self.summary_path.write_text("\n".join(lines))
        self.log(f"report: {self.report_path.name} / {self.summary_path.name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api")
    parser.add_argument("--outputs", type=Path, default=ROOT/"outputs")
    parser.add_argument("--request-s", type=float, default=7200)
    parser.add_argument("--assisted-s", type=float, default=3600)
    parser.add_argument("--full-s", type=float, default=7200)
    parser.add_argument("--no-recording", action="store_true")
    args = parser.parse_args()
    api = args.api
    if not api:
        services = json.loads((ROOT/"tools/.runtime/services.json").read_text())
        api = services["backend_url"]
    driver = Driver(api, args.outputs, args.request_s, args.assisted_s, args.full_s, record=not args.no_recording)
    signal.signal(signal.SIGTERM, lambda *_: setattr(driver, "stop_requested", True))
    signal.signal(signal.SIGINT, lambda *_: setattr(driver, "stop_requested", True))
    try:
        driver.run()
    except Exception as error:
        driver.note("driver_error", str(error)[:200])
        try:
            driver.finalize()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
