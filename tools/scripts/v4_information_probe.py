"""Read-only evidence of information updates from a real running game."""
import argparse
import json
from pathlib import Path
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deadline", type=float, default=1200)
    args = parser.parse_args()
    services = json.loads(Path("tools/.runtime/services.json").read_text())
    report = {"status": "failed", "source": "live public HTTP frames", "samples": [],
              "decayed_cells": 0, "refreshed_cells": 0, "target_neighborhood": False}
    previous = None
    deadline = time.monotonic()+args.deadline
    with httpx.Client(base_url=services["backend_url"], timeout=20) as client:
        while time.monotonic() < deadline:
            state = client.get("/api/state").raise_for_status().json()
            scans = [value for column in state["info_matrix"] for value in column]
            targets = [value for column in state["target_info_matrix"] for value in column]
            if previous and previous["episode_id"] == state["episode_id"]:
                old = [value for column in previous["info_matrix"] for value in column]
                report["decayed_cells"] += sum(0 < after < before for before, after in zip(old, scans))
                report["refreshed_cells"] += sum(after > before for before, after in zip(old, scans))
            report["samples"].append({"sim_time_s": state["information_model"]["as_of_s"],
                "scan_max": max(scans), "target_max": max(targets),
                "target_cells_over_001": sum(value > .01 for value in targets),
                "contacts": len(state["contacts"]), "status": state["runtime_status"]})
            report["target_neighborhood"] = len(state["contacts"]) > 0 and sum(value > .01 for value in targets) > 1
            if report["decayed_cells"] and report["refreshed_cells"] and report["target_neighborhood"]:
                report["status"] = "passed"
                break
            previous = state
            time.sleep(5)
    Path("outputs/v4-information-live.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps({key: value for key, value in report.items() if key != "samples"}))
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
