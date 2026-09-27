"""Explicit paid-model continuation against an already approved local mission."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.parse import urlparse

import httpx

from v2_live_acceptance import Acceptance, ROOT, require


def resources(services):
    memory = {}
    for name, pid in services.get("pids", {}).items():
        status = Path(f"/proc/{pid}/status")
        if status.exists():
            values = dict(line.split(":", 1) for line in status.read_text().splitlines())
            memory[name] = int(values.get("VmRSS", "0 kB").split()[0])
    return {"rss_kib": memory, "database_bytes": sum(path.stat().st_size
        for path in (ROOT/"tools/.runtime").glob("mission.sqlite*") if path.is_file())}


def check_running(state):
    require(state["runtime_status"] == "running", "Runtime stopped during continuation")
    require(len(state["uavs"]) == 8, "Fleet entity count changed")
    search = {boat["id"] for boat in state["uavs"] if boat["operation_mode"] == "coverage"}
    owners = [region["assigned_uav_id"] for region in state["search_regions"]]
    require(len(owners) == len(search) and set(owners) == search, "Search ownership invariant failed")
    teams = Counter(boat["target_group_id"] for boat in state["uavs"] if boat["operation_mode"] == "track")
    require(all(size <= 3 for size in teams.values()), "Tracking team limit exceeded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=1800)
    parser.add_argument("--output", type=Path, default=Path("outputs/v2-longcat-soak.json"))
    args = parser.parse_args()
    require(0 < args.seconds <= 7200, "Duration must be between zero and two hours")
    services = json.loads((ROOT/"tools/.runtime/services.json").read_text())
    api = services["backend_url"]
    require(urlparse(api).hostname in ("127.0.0.1", "localhost", "::1"), "Only local services are supported")
    report = {"schema": "uuv-v2-live-soak/v2", "status": "failed", "model_connected": True, "started_at": datetime.now(timezone.utc).isoformat(),
        "requested_wall_s": args.seconds, "events": [], "states": [], "operator_actions": [], "errors": [], "samples": []}
    with httpx.Client(base_url=api, timeout=20) as client:
        acceptance = Acceptance(client, time.monotonic()+args.seconds+90, report)
        started, start_requested = None, False
        try:
            acceptance.request("GET", "/api/health")
            initial = acceptance.request("GET", "/api/state")
            acceptance.episode, acceptance.cursor = initial["episode_id"], initial["event_cursor"]
            require(initial["runtime_status"] == "paused", "Continuation requires an explicitly paused mission")
            require(initial["standing_policy"]["enabled"], "Approved standing policy required")
            require(all(boat["operation_mode"] != "idle" for boat in initial["uavs"]), "An active fleet is required")
            require("longcat" in initial["agent_status"]["model"].lower(), "Real LongCat worker required")
            report["initial_metrics"] = initial["mission_metrics"]
            report["initial_sim_time_s"] = initial["sim_time_min"]*60
            report["episode_id"] = acceptance.episode
            report["team_release_observed"] = False
            report["tracking_reestablished_after_release"] = False
            ever_tracking = any(contact["state"] == "tracking" for contact in initial["contacts"])
            acceptance.phase = "real_model_continuation"
            acceptance.operator("/api/permissions/mode", "explicit Full autonomy continuation acceptance", mode="full")
            start_requested = True
            acceptance.operator("/api/simulation/start", "explicit real model wall-clock acceptance")
            started, last_sample = time.monotonic(), -30
            while time.monotonic()-started < args.seconds:
                state = acceptance.state()
                check_running(state)
                teams = Counter(boat["target_group_id"] for boat in state["uavs"] if boat["operation_mode"] == "track")
                tracking = any(contact["state"] == "tracking" and teams[contact["contact_id"]] >= 2 for contact in state["contacts"])
                if ever_tracking and not teams and not report["team_release_observed"]:
                    report["team_release_observed"] = True
                    report["team_released_at_sim_s"] = state["sim_time_min"]*60
                if tracking and report["team_release_observed"] and not report["tracking_reestablished_after_release"]:
                    report["tracking_reestablished_after_release"] = True
                    report["tracking_reestablished_at_sim_s"] = state["sim_time_min"]*60
                ever_tracking = ever_tracking or tracking
                elapsed = time.monotonic()-started
                if elapsed-last_sample >= 30:
                    sample = {"wall_s": round(elapsed, 3), "sim_s": state["sim_time_min"]*60,
                        "metrics": state["mission_metrics"], "agent": state["agent_status"], **resources(services)}
                    report["samples"].append(sample)
                    print(json.dumps(sample), flush=True)
                    last_sample = elapsed
                time.sleep(.5)
            final = acceptance.state(force=True)
            check_running(final)
            report["final_metrics"] = final["mission_metrics"]
            report["final_sim_time_s"] = final["sim_time_min"]*60
            report["completed_model_runs"] = sum(event["type"] == "agent_completed" for event in report["events"])
            require(report["completed_model_runs"] >= 2, "No repeated real worker completions observed")
            require(final["frame_id"] > initial["frame_id"], "Simulation did not advance")
            if args.seconds >= 1800:
                require(final["mission_metrics"]["rotation_count"]-initial["mission_metrics"]["rotation_count"] >= 2,
                    "Fewer than two natural rotations during the online run")
                require(final["mission_metrics"]["effective_tracking_seconds"]-initial["mission_metrics"]["effective_tracking_seconds"] >= 60,
                    "Insufficient effective cooperative tracking during the online run")
                require(not report["team_release_observed"] or report["tracking_reestablished_after_release"],
                    "Tracking team fully exited but cooperative tracking was never reestablished")
            report["status"] = "passed"
        except (Exception, KeyboardInterrupt) as error:
            report["errors"].append({"type": type(error).__name__, "message": str(error)})
        finally:
            report["wall_seconds"] = time.monotonic()-started if started else 0
            acceptance.deadline = time.monotonic()+30
            try:
                if start_requested:
                    current = acceptance.request("GET", "/api/state")
                    require(current["episode_id"] == acceptance.episode, "Cannot pause another episode")
                    report["precleanup_state"] = {key: current[key] for key in ("runtime_status", "sim_time_min", "mission_metrics", "agent_status")}
                    acceptance.operator("/api/simulation/pause", "test operator cleanup")
                    acceptance.events()
                    report["cleanup_paused"] = True
            except Exception as error:
                report["status"] = "failed"
                report["errors"].append({"phase": "cleanup", "type": type(error).__name__, "message": str(error)})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n")
    print(json.dumps({key: report.get(key) for key in ("status", "wall_seconds", "completed_model_runs", "errors")}), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
