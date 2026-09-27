"""Deterministic full-fleet evidence. No model calls and no synthesized contacts.

Run from repository root with PYTHONPATH=tools. A short probe intentionally does
not pass full acceptance. Wall pacing distributes actual ticks across the run;
a paused simulation fails immediately instead of waiting out the timer.
"""

import argparse
from collections import Counter
from dataclasses import asdict
import json
import math
from pathlib import Path
import resource
import tempfile
import time

from uuv_game.config import Config
from uuv_game.runtime import MissionRuntime


def _audit(runtime, previous):
    failures = []
    if len(runtime.uuvs) != 8 or len({u["id"] for u in runtime.uuvs}) != 8:
        failures.append("fleet_entity_count")
    search = {member for member, action in runtime.active.items() if action["kind"] in ("search", "reacquire")}
    owners = [region["owner"] for region in runtime.regions]
    if len(owners) != len(search) or set(owners) != search:
        failures.append("search_region_ownership")
    teams = Counter(action.get("contact_id") for action in runtime.active.values() if action["kind"] == "track")
    if any(count > 3 for count in teams.values()):
        failures.append("tracking_team_limit")
    for boat in runtime.uuvs:
        old = previous[boat["id"]]
        if boat["generation"] == old["generation"]:
            traveled = math.dist(boat["pose"][:2], old["pose"][:2])
            angle = abs(math.remainder(boat["pose"][2]-old["pose"][2], 2*math.pi))
            if traveled > 4*runtime.config.dt+1e-6 or angle > traveled/60*1.001+1e-8:
                failures.append(f"motion_constraint:{boat['id']}")
            if boat["remaining_range_m"] > old["remaining_range_m"]+1e-6:
                failures.append(f"energy_increase_without_generation:{boat['id']}")
        else:
            if boat["generation"] != old["generation"]+1:
                failures.append(f"generation_sequence:{boat['id']}")
            if min(old["pose"][0], old["pose"][1], 4000-old["pose"][0], 4000-old["pose"][1]) > 4*runtime.config.dt+1:
                failures.append(f"replacement_before_boundary:{boat['id']}")
            if min(boat["pose"][0], boat["pose"][1], 4000-boat["pose"][0], 4000-boat["pose"][1]) > 100:
                failures.append(f"replacement_not_at_entry:{boat['id']}")
        if not 0 <= boat["remaining_range_m"] <= runtime.config.range_capacity:
            failures.append(f"energy_bounds:{boat['id']}")
    return failures


def run_acceptance(sim_seconds=14400, wall_seconds=0, output=None, seed=42):
    if sim_seconds <= 0 or wall_seconds < 0 or not math.isfinite(sim_seconds+wall_seconds):
        raise ValueError("simulation duration must be positive and wall duration nonnegative")
    report = {"schema": "uuv-v2-acceptance/v1", "passed": False, "run_completed": False,
        "model_connected": False, "requested_sim_seconds": sim_seconds, "requested_wall_seconds": wall_seconds,
        "scenario": "default-eight-search-auto-cooperative-tracking", "seed": seed, "fault_injections": [],
        "failures": [], "pauses": [], "events": [], "samples": [], "calculations": [],
        "fleet_size_min": 8, "fleet_size_max": 8, "search_region_counts": [],
        "tracking_phases": [], "confirmed_contact_time_s": None, "first_tracking_time_s": None,
        "min_remaining_range_m": None, "min_tracking_geometry": None, "max_tracking_uncertainty_m": 0,
        "invariant_ticks": 0, "peak_rss_kib_start": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    with tempfile.TemporaryDirectory(prefix="uuv-v2-acceptance-") as directory:
        runtime = MissionRuntime(Path(directory)/"mission.sqlite", Config(seed=seed))
        report["config"] = asdict(runtime.config)
        started = time.monotonic()
        last_event, last_attempt, last_sample = 0, -30.0, -60.0
        ledger = [column[:] for column in runtime.scan_times]
        combinations_seen, phases_seen = set(), set()
        try:
            runtime.set_mode("full")
            candidate = runtime.calculate("plan_search", {"standing_policy": True})
            report["calculations"].append({"time_s": 0, "tool": "plan_search", "status": candidate["status"], "diagnostics": candidate.get("diagnostics")})
            if candidate["status"] != "succeeded":
                raise RuntimeError("initial_fleet_search_infeasible")
            assessment = runtime.evaluate(candidate["result_id"])
            if not assessment["valid"]:
                raise RuntimeError(f"initial_assessment:{assessment['errors']}")
            runtime.submit(candidate["result_id"], "acceptance-fleet", runtime.episode)
            runtime.start()
            while runtime.sim_time+runtime.config.dt/2 < sim_seconds:
                if wall_seconds:
                    due = started+(runtime.sim_time+runtime.config.dt)/sim_seconds*wall_seconds
                    delay = due-time.monotonic()
                    if delay > 0:
                        time.sleep(min(delay, 0.25))
                        continue
                previous = {u["id"]: {"generation": u["generation"], "pose": u["pose"][:], "remaining_range_m": u["remaining_range_m"]} for u in runtime.uuvs}
                runtime.tick()
                report["invariant_ticks"] += 1
                failures = _audit(runtime, previous)
                report["failures"].extend({"time_s": runtime.sim_time, "reason": failure} for failure in failures)
                count = len(runtime.uuvs)
                report["fleet_size_min"] = min(report["fleet_size_min"], count)
                report["fleet_size_max"] = max(report["fleet_size_max"], count)
                minimum = min(u["remaining_range_m"] for u in runtime.uuvs)
                report["min_remaining_range_m"] = minimum if report["min_remaining_range_m"] is None else min(minimum, report["min_remaining_range_m"])
                combinations_seen.add((sum(a["kind"] in ("search", "reacquire") for a in runtime.active.values()), len(runtime.regions)))
                phases_seen.update(a.get("phase", "unknown") for a in runtime.active.values() if a["kind"] == "track")
                for event in runtime.events:
                    if event["id"] > last_event:
                        report["events"].append(event)
                        last_event = event["id"]
                if runtime.status != "running":
                    report["pauses"].append({"time_s": runtime.sim_time, "status": runtime.status, "recent_events": runtime.events[-5:]})
                    report["failures"].append({"time_s": runtime.sim_time, "reason": "runtime_not_running"})
                    break
                if failures:
                    break
                for contact in runtime.contacts.values():
                    if contact["state"] == "confirmed" and report["confirmed_contact_time_s"] is None:
                        report["confirmed_contact_time_s"] = runtime.sim_time
                    if contact["state"] == "tracking":
                        if report["first_tracking_time_s"] is None:
                            report["first_tracking_time_s"] = runtime.sim_time
                        geometry = contact["geometry_quality"]
                        report["min_tracking_geometry"] = geometry if report["min_tracking_geometry"] is None else min(geometry, report["min_tracking_geometry"])
                        report["max_tracking_uncertainty_m"] = max(report["max_tracking_uncertainty_m"], contact["uncertainty_m"])
                tracking_count = sum(a["kind"] == "track" for a in runtime.active.values())
                contacts = [c for c in runtime.contacts.values() if c["state"] in ("confirmed", "degraded", "tracking")]
                if tracking_count < 2 and contacts and runtime.sim_time-last_attempt >= 30:
                    last_attempt = runtime.sim_time
                    contact = sorted(contacts, key=lambda c: c["contact_id"])[0]
                    candidate = runtime.calculate("plan_tracking", {"contact_id": contact["contact_id"]})
                    entry = {"time_s": runtime.sim_time, "tool": "plan_tracking", "status": candidate["status"],
                        "members": candidate.get("members", []), "diagnostics": candidate.get("diagnostics")}
                    report["calculations"].append(entry)
                    if candidate["status"] == "succeeded":
                        assessment = runtime.evaluate(candidate["result_id"])
                        entry["assessment"] = assessment
                        if assessment["valid"]:
                            receipt = runtime.submit(candidate["result_id"], f"acceptance-track-{runtime.frame_id}", runtime.episode)
                            entry["receipt_status"] = receipt["status"]
                if runtime.sim_time-last_sample >= 60:
                    if any(now < before for current, old in zip(runtime.scan_times, ledger) for now, before in zip(current, old)):
                        report["failures"].append({"time_s": runtime.sim_time, "reason": "coverage_history_regressed"})
                        break
                    ledger = [column[:] for column in runtime.scan_times]
                    runtime.save()
                    sample = {"wall_s": round(time.monotonic()-started, 3), "sim_s": runtime.sim_time,
                        "status": runtime.status, "metrics": dict(runtime.metrics), "search_regions": len(runtime.regions),
                        "jobs": len(runtime.agent_jobs), "events_retained": len(runtime.events), "observations_retained": len(runtime.observations),
                        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        "database_bytes": sum(p.stat().st_size for p in Path(directory).iterdir())}
                    report["samples"].append(sample)
                    print(json.dumps(sample), flush=True)
                    last_sample = runtime.sim_time
            report["run_completed"] = not report["failures"] and runtime.sim_time+runtime.config.dt/2 >= sim_seconds
        except Exception as error:
            report["failures"].append({"time_s": runtime.sim_time, "reason": type(error).__name__, "message": str(error)})
        finally:
            report.update(sim_seconds=runtime.sim_time, wall_seconds=time.monotonic()-started,
                search_region_counts=[list(value) for value in sorted(combinations_seen)], tracking_phases=sorted(phases_seen),
                metrics=dict(runtime.metrics), final_status=runtime.status,
                final_telemetry=runtime.mission_state()["uuvs"],
                final_contacts=runtime.mission_state()["contacts"],
                final_generations={u["id"]: u["generation"] for u in runtime.uuvs},
                peak_rss_kib_end=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            report["acceptance_checks"] = {"four_hours_simulated": runtime.sim_time >= 14400,
                "thirty_minutes_wall": wall_seconds >= 1800 and report["wall_seconds"] >= 1800,
                "cooperative_tracking_established": runtime.metrics["effective_tracking_seconds"] > 0,
                "tracking_transit_observed": "transit" in phases_seen,
                "at_least_two_rotations": runtime.metrics["rotation_count"] >= 2,
                "invariants_and_continuation": report["run_completed"] and not report["failures"]}
            report["passed"] = all(report["acceptance_checks"].values())
            runtime.close()
    if output:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2)+"\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sim-seconds", type=float, default=14400)
    parser.add_argument("--wall-seconds", type=float, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="outputs/v2-acceptance.json")
    args = parser.parse_args()
    report = run_acceptance(args.sim_seconds, args.wall_seconds, args.output, args.seed)
    print(json.dumps({key: value for key, value in report.items() if key not in ("events", "samples", "calculations", "final_telemetry", "final_contacts")}), flush=True)
    raise SystemExit(0 if report["run_completed"] else 1)


if __name__ == "__main__":
    main()
