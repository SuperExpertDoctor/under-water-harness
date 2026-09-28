"""Explicit real-worker acceptance. Uses public operator APIs only; never imported by tests."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from urllib.parse import urlparse

import httpx


TERMINAL = {"completed", "failed", "cancelled"}
ROOT = Path(__file__).resolve().parents[2]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def assignment(state):
    fields = ("id", "generation", "team_id", "target_group_id", "assigned_region_id", "operation_mode", "task_phase")
    return sorted(tuple(boat.get(field) for field in fields) for boat in state["uavs"])


class Acceptance:
    def __init__(self, client, deadline, report):
        self.client = client
        self.deadline = deadline
        self.report = report
        self.episode = None
        self.cursor = None
        self.phase = "initialization"
        self.last_snapshot = 0

    def request(self, method, path, **kwargs):
        remaining = self.deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Acceptance deadline reached during {self.phase}")
        response = self.client.request(method, path, timeout=min(20, remaining), **kwargs)
        if response.is_error:
            raise RuntimeError(f"{method} {path}: HTTP {response.status_code}: {response.text[:600]}")
        return response.json()

    def operator(self, path, reason, **payload):
        action = {"path": path, "reason": reason, "phase": self.phase, "payload": payload,
            "wall_time": datetime.now(timezone.utc).isoformat(), "test_operator_action": True}
        self.report["operator_actions"].append(action)
        result = self.request("POST", path, json={"episode_id": self.episode, **payload})
        action["response"] = result
        return result

    def events(self):
        if self.cursor is None:
            return
        while True:
            batch = self.request("GET", "/api/events", params={"after": self.cursor, "limit": 500})["events"]
            if not batch:
                break
            require(batch[0]["id"] == self.cursor+1, "Public event retention gap; complete receipts unavailable")
            self.report["events"].extend(batch)
            self.cursor = batch[-1]["id"]
            if len(batch) < 500:
                break

    def state(self, force=False):
        state = self.request("GET", "/api/state")
        require(state["episode_id"] == self.episode, "Episode changed during acceptance")
        require(not state.get("scenario_vessels"), "Normal state exposed scenario truth")
        self.events()
        if force or time.monotonic()-self.last_snapshot >= 3:
            snapshot = {key: state.get(key) for key in ("episode_id", "frame_id", "sim_time_min", "runtime_status",
                "mission_revision", "autonomy_mode", "agent_status", "standing_policy", "mission_metrics",
                "teams", "contacts", "bearing_lines", "plans", "pending_approvals", "event_cursor")}
            snapshot["uavs"] = [{key: value for key, value in boat.items() if key not in ("trail", "planned_path")}
                for boat in state["uavs"]]
            snapshot["search_regions"] = [{key: value for key, value in region.items() if key != "cells"}
                for region in state["search_regions"]]
            snapshot["phase"] = self.phase
            self.report["states"].append(snapshot)
            self.last_snapshot = time.monotonic()
        require(state["runtime_status"] != "safety_paused", "Runtime entered safety pause")
        return state

    def jobs(self):
        self.events()
        jobs = self.request("GET", "/api/pi-agent/task-assignment")["assignments"]
        self.report["jobs"] = jobs
        return [job for job in jobs if job["episode_id"] == self.episode]

    def idle(self):
        while any(job["status"] not in TERMINAL for job in self.jobs()):
            self.state()
            time.sleep(.4)

    def send(self, label, text):
        self.idle()
        prior = {job["run_id"] for job in self.jobs()}
        job = self.operator("/api/pi-agent/messages", label, text=text)
        require(job["run_id"] not in prior, "Prompt raced a busy worker; not an isolated acceptance job")
        self.report["runs"][label] = {"run_id": job["run_id"]}
        return job["run_id"]

    def wait_run(self, run_id, unchanged=None):
        while True:
            jobs = self.jobs()
            job = next((job for job in jobs if job["run_id"] == run_id), None)
            require(job is not None, f"Acceptance job disappeared: {run_id}")
            state = self.state()
            if unchanged is not None:
                require(assignment(state) == unchanged, "Request-mode proposal changed execution before human approval")
            if job["status"] in TERMINAL:
                self.events()
                for run in self.report["runs"].values():
                    if run["run_id"] == run_id:
                        run["terminal_job"] = job
                require(job["status"] == "completed", f"Worker job {run_id} ended {job['status']}: {job.get('error')}")
                return state
            time.sleep(.4)

    def receipts(self, run_id, required_tools):
        events = [event for event in self.report["events"] if event.get("data", {}).get("run_id") == run_id]
        completed = [event["data"] for event in events if event["type"] == "tool_completed"]
        require(required_tools <= {event["tool"] for event in completed}, f"Missing real tool receipts for {run_id}")
        native = {event["type"] for event in events}
        require({"message_start", "message_update", "message_end", "tool_execution_start", "tool_execution_end", "agent_settled"} <= native,
            f"Missing native streaming/settled evidence for {run_id}")
        messages = self.request("GET", "/api/pi-agent/messages")["messages"]
        messages = [message for message in messages if message.get("run_id") == run_id]
        require(any(message["role"] == "assistant" and "longcat" in message.get("model", "").lower() for message in messages),
            "No LongCat assistant message bound to acceptance run")
        for run in self.report["runs"].values():
            if run["run_id"] == run_id:
                run.update(tool_receipts=completed, native_event_types=sorted(native), messages=messages)
        return events

    def execute(self, reset):
        self.request("GET", "/api/health")
        initial = self.request("GET", "/api/state")
        self.episode = initial["episode_id"]
        self.cursor = initial["event_cursor"]
        if reset:
            result = self.operator("/api/simulation/reset", "explicit --reset")
            self.episode = result["episode_id"]
            self.cursor = self.request("GET", "/api/state")["event_cursor"]
        self.report["episode_id"] = self.episode
        self.report["agent"] = self.request("GET", "/api/pi-agent/status")
        require("longcat" in self.report["agent"].get("model", "").lower(), "Running worker is not configured for LongCat")
        self.operator("/api/simulation/pause", "freeze initial fleet planning")
        self.operator("/api/permissions/mode", "allow low-risk fleet search", mode="assisted")
        self.phase = "fleet_search"
        run_id = self.send(self.phase, "Load multi-uuv-recon-tracking with the native read tool. Read mission state. "
            "Generate one atomic eight-UUV search plan using plan_search({standing_policy:true}) with BOTH members AND bbox OMITTED. "
            "Do not switch to explicit members or a bounding box; do not use the old strip-sweep planner. "
            "Evaluate then submit that plan exactly once. Each of the eight boats must own one distinct region. "
            "Keep simulation paused; do not start it. Do not change the operator's permission mode.")
        state = self.wait_run(run_id)
        receipts = self.receipts(run_id, {"get_mission_state", "plan_search", "evaluate_plan", "submit_mission_plan"})
        require(sum(event["type"] == "tool_started" and event["data"].get("tool") == "submit_mission_plan"
            for event in receipts) == 1, "Fleet planning did not use exactly one atomic submission")
        require(any(event["type"] == "tool_execution_end" and event["data"].get("tool_name") == "read"
            and not event["data"].get("is_error") for event in receipts), "No native trusted-skill read receipt")
        search_calls = [event["data"].get("params", {}) for event in receipts
            if event["type"] == "tool_started" and event["data"].get("tool") == "plan_search"]
        require(any(call.get("standing_policy") is True and "members" not in call for call in search_calls),
            "Fleet plan did not request atomic omitted-members standing policy")
        boats, regions = state["uavs"], state["search_regions"]
        submitted = [event["data"] for event in receipts if event["type"] == "tool_completed"
            and event["data"].get("tool") == "submit_mission_plan"]
        require(len(submitted) == 1 and submitted[0].get("algorithm") == "connected-coverage-dubins-v2",
            "Submitted plan was not the automatic connected full-area candidate")
        owned = [tuple(cell) for region in regions for cell in region["cells"]]
        require(len(owned) == len(set(owned)) == state["searchable_cells"],
            "Search regions must cover every searchable cell exactly once")
        require(len(boats) == len(regions) == 8, "Eight active search regions required")
        require(len({region["id"] for region in regions}) == 8 and
            {region["assigned_uav_id"] for region in regions} == {boat["id"] for boat in boats}, "Search ownership not unique")
        require(all(boat["operation_mode"] == "coverage" and boat["assigned_region_id"] for boat in boats), "Fleet search not active")
        require(all(state["standing_policy"].get(grant) for grant in ("enabled", "energy_rotation", "local_repair")), "Standing policy not activated")
        self.report["checks"]["atomic_eight_boat_search"] = True
        self.operator("/api/permissions/mode", "human approval required for tracking", mode="request")
        self.operator("/api/simulation/start", "observe real default-scenario detection")
        self.phase = "contact_detection"
        while True:
            state = self.state()
            contact = next((contact for contact in state["contacts"] if contact["state"] == "confirmed"), None)
            if contact:
                break
            time.sleep(.4)
        contact_id = contact["contact_id"]
        self.report["detected_contact"] = contact
        self.operator("/api/simulation/pause", "freeze real detection during human approval dialogue")
        self.idle()
        state = self.state(force=True)
        before = assignment(state)
        self.report["preapproval_assignment"] = before
        self.phase = "tracking_proposal"
        run_id = self.send(self.phase, f"Load the mission skill. Call get_mission_state, then explicitly call get_observations to read the actual sensor records for {contact_id}. "
            "Produce one NEW tracking candidate with plan_tracking, omitting members so selection is automatic. "
            "Use a two-boat team when feasible. Evaluate it, then submit once to REQUEST human approval. "
            "Earlier unapproved proposals are not executed tasks; create this candidate for this explicit operator review. "
            "Keep all existing execution unchanged until operator approval. Do not approve, change mode, or start simulation.")
        state = self.wait_run(run_id, unchanged=before)
        receipts = self.receipts(run_id, {"get_mission_state", "get_observations", "plan_tracking", "evaluate_plan", "submit_mission_plan"})
        require(any(event["type"] == "tool_started" and event["data"].get("tool") == "plan_tracking"
            and "members" not in event["data"].get("params", {}) for event in receipts), "Tracking members were not selected automatically")
        plan_ids = {event["data"].get("plan_id") for event in receipts
            if event["type"] == "tool_completed" and event["data"].get("tool") == "submit_mission_plan"}
        pending = self.request("GET", "/api/approvals")["approvals"]
        candidates = [plan for plan in pending if plan["plan_id"] in plan_ids and plan.get("contact_id") == contact_id]
        require(len(candidates) == 1, "Expected one pending tracking proposal from the explicit acceptance run")
        plan = candidates[0]
        require(len(plan["members"]) == 2, "Automatic tracking selection did not produce two boats")
        require(assignment(state) == before, "Execution changed before approval")
        self.report["tracking_plan"] = plan
        self.report["checks"]["request_mode_preserved_execution"] = True
        self.operator(f"/api/approvals/{plan['plan_id']}/decision", "explicit human approval of reviewed candidate", decision="approve")
        state = self.state(force=True)
        require(any(item["plan_id"] == plan["plan_id"] and item["status"] == "active" for item in state["plans"]), "Approved plan did not activate")
        require({boat["id"] for boat in state["uavs"] if boat["team_id"] == plan["plan_id"] and boat["operation_mode"] == "track"}
            == set(plan["members"]), "Approved two-boat tracking assignment missing")
        self.report["checks"]["human_approval_activated_tracking"] = True
        baseline = state["mission_metrics"]["effective_tracking_seconds"]
        self.operator("/api/simulation/start", "verify physical tracking after approval")
        self.phase = "effective_tracking"
        while True:
            state = self.state()
            contact = next(item for item in state["contacts"] if item["contact_id"] == contact_id)
            lines = {line["observer_id"] for line in state["bearing_lines"] if line["contact_id"] == contact_id}
            effective = state["mission_metrics"]["effective_tracking_seconds"]-baseline
            if (contact["state"] == "tracking" and contact.get("geometry_quality", 0) > .3
                    and contact.get("uncertainty_m", float("inf")) <= 120
                    and set(plan["members"]) <= lines and effective >= 30):
                break
            time.sleep(.4)
        self.report["checks"]["effective_tracking_30_sim_seconds"] = True
        self.report["effective_tracking_delta_s"] = effective
        self.operator("/api/simulation/pause", "freeze valid plan for annotation review")
        self.annotation_review(plan, contact_id)
        self.state(force=True)

    def annotation_review(self, plan, contact_id):
        self.phase = "annotation_feedback"
        self.idle()
        before = assignment(self.state(force=True))
        messages = self.request("GET", "/api/pi-agent/messages")["messages"]
        original = next((message for message in reversed(messages) if message["role"] == "assistant"
            and message.get("text", "").strip()), None)
        require(original is not None, "No actual assistant response available for annotation")
        # Select a literal plain-text token visible in the assistant response, not invented Markdown source.
        tokens = re.findall(r"[\w\u4e00-\u9fff][\w\u4e00-\u9fff-]{2,79}", original["text"])
        require(tokens, "No plain-text assistant selection available")
        quote = contact_id if contact_id in original["text"] else tokens[0]
        run_id = self.send("annotation_review", "Read current mission state and latest observations, then explain the "
            "existing approved tracking plan. This is read-only review; preserve every valid active plan and assignment. "
            "Do not plan, evaluate, submit, change permissions, or resume simulation.")
        while True:
            job = next(job for job in self.jobs() if job["run_id"] == run_id)
            if job["status"] == "running" or job["status"] in TERMINAL:
                break
            time.sleep(.15)
        delivery = "steer" if job["status"] == "running" else "followUp"
        response = self.operator("/api/pi-agent/messages", "annotate actual assistant selection; preserve approved execution",
            text="For the selected text, explain the existing approved plan briefly. Read-only feedback, not permission "
                "to alter or resubmit any plan. Preserve valid tasks and keep simulation paused.", delivery=delivery,
            annotation={"message_id": original["id"], "quote": quote, "plan_id": plan["plan_id"]})
        feedback_run = response["run_id"]
        self.report["annotation"] = {"message_id": original["id"], "quote": quote, "requested_delivery": delivery,
            "run_id": feedback_run, "joined_running_job": feedback_run == run_id and delivery == "steer"}
        self.report["runs"]["annotation_feedback"] = {"run_id": feedback_run}
        self.wait_run(run_id, unchanged=before)
        if feedback_run != run_id:
            self.wait_run(feedback_run, unchanged=before)
        self.idle()
        self.receipts(run_id, {"get_mission_state"})
        self.events()
        self.report["annotation"]["native_feedback_delivered"] = any(event["type"] == "agent_feedback_delivered"
            and event.get("data", {}).get("run_id") == feedback_run for event in self.report["events"])
        messages = self.request("GET", "/api/pi-agent/messages")["messages"]
        annotations = [message for message in messages if message.get("run_id") == feedback_run
            and (message.get("annotation") or {}).get("message_id") == original["id"]]
        require(len(annotations) == 1 and annotations[0]["status"] == "completed", "Annotation feedback was not delivered")
        self.report["annotation"]["message"] = annotations[0]
        require(not any(event["type"] == "tool_started" and event.get("data", {}).get("run_id") in (run_id, feedback_run)
            and event["data"].get("tool") in ("plan_search", "plan_tracking", "plan_path", "submit_mission_plan")
            for event in self.report["events"]), "Read-only annotation review attempted replanning")
        self.report["checks"]["annotation_preserved_active_plan"] = assignment(self.state()) == before
        require(self.report["checks"]["annotation_preserved_active_plan"], "Annotation changed active assignment")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", help="Defaults to existing tools/.runtime/services.json backend_url")
    parser.add_argument("--reset", action="store_true", help="Explicitly reset the live episode before acceptance")
    parser.add_argument("--deadline", type=float, default=600, help="Global wall-clock budget in seconds")
    parser.add_argument("--output", type=Path, default=Path("outputs/v2-longcat-acceptance.json"))
    args = parser.parse_args()
    require(args.deadline > 0, "Deadline must be positive")
    api = args.api or json.loads((ROOT/"tools/.runtime/services.json").read_text())["backend_url"]
    parsed = urlparse(api)
    require(parsed.hostname in ("127.0.0.1", "localhost", "::1") and not parsed.username and not parsed.password,
        "Acceptance requires a local service URL without embedded credentials")
    started = time.monotonic()
    report = {"status": "failed", "started_at": datetime.now(timezone.utc).isoformat(), "api": api,
        "evidence_source": "public HTTP API of existing PI worker; no fake provider or direct runtime calls",
        "reset_requested": args.reset, "deadline_s": args.deadline, "runs": {}, "checks": {}, "events": [],
        "states": [], "operator_actions": [], "errors": []}
    with httpx.Client(base_url=api, timeout=20) as client:
        acceptance = Acceptance(client, started+args.deadline, report)
        try:
            acceptance.execute(args.reset)
            report["status"] = "passed"
        except (Exception, KeyboardInterrupt) as error:
            report["errors"].append({"phase": acceptance.phase, "type": type(error).__name__, "message": str(error)})
        finally:
            # Cleanup has an independent bounded budget, including after the main deadline expires.
            try:
                state_response = client.get("/api/state", timeout=20)
                state_response.raise_for_status()
                state = state_response.json()
                report["precleanup_state"] = {key: state.get(key) for key in ("episode_id", "runtime_status", "sim_time_min", "mission_metrics", "event_cursor")}
                require(state["episode_id"] == acceptance.episode, "Do not pause a different operator's episode")
                action = {"path": "/api/simulation/pause", "reason": "test operator cleanup", "test_operator_action": True}
                report["operator_actions"].append(action)
                response = client.post("/api/simulation/pause", json={"episode_id": acceptance.episode}, timeout=20)
                response.raise_for_status()
                action["response"] = response.json()
                require(action["response"]["status"] == "paused", "Cleanup failed to pause simulation")
                report["cleanup_paused"] = True
                acceptance.deadline = time.monotonic()+20
                acceptance.events()
                report["final_messages"] = acceptance.request("GET", "/api/pi-agent/messages")["messages"]
            except (Exception, KeyboardInterrupt) as error:
                report["status"] = "failed"
                report["errors"].append({"phase": "cleanup", "type": type(error).__name__, "message": str(error)})
    report["elapsed_wall_s"] = round(time.monotonic()-started, 3)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n")
    print(json.dumps({"status": report["status"], "output": str(args.output), "elapsed_wall_s": report["elapsed_wall_s"],
        "checks": report["checks"], "cleanup_paused": report.get("cleanup_paused", False), "errors": report["errors"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
