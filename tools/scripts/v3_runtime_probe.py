"""Read-only, credential-free live acceptance summary. Never emits private observations."""
import argparse
import json
from pathlib import Path
import sqlite3

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    runtime = Path(__file__).resolve().parents[1] / ".runtime"
    services = json.loads((runtime / "services.json").read_text())
    credentials = dict(line.split("=", 1) for line in (runtime / "credentials.env").read_text().splitlines()
                       if "=" in line and not line.startswith("#"))
    configured = {}
    for role in ("pi", "pi-adversary"):
        entries = Path(f"/proc/{services['pids'][role]}/environ").read_bytes().split(b"\0")
        environment = dict(entry.split(b"=", 1) for entry in entries if b"=" in entry)
        configured[role] = bool(credentials.get("LONGCAT_API_KEY")) and environment.get(b"LONGCAT_API_KEY") == credentials["LONGCAT_API_KEY"].encode()
    with httpx.Client(base_url=services["backend_url"], timeout=10) as client:
        state = client.get("/api/state").raise_for_status().json()
        jobs = client.get("/api/pi-agent/task-assignment").raise_for_status().json()["assignments"]
    with sqlite3.connect(f"file:{runtime / 'mission.sqlite'}?mode=ro", uri=True) as db:
        checkpoint = json.loads(db.execute("SELECT data FROM checkpoint WHERE id=1").fetchone()[0])
    enemy = checkpoint.get("adversary", {})
    receipt_count = 0
    for path in (runtime / "pi-adversary" / state["episode_id"]).glob("*.jsonl"):
        for line in path.read_text().splitlines():
            entry = json.loads(line).get("message", {})
            if entry.get("role") == "toolResult" and entry.get("toolName") == "set_evasion_parameters" and not entry.get("isError"):
                receipt_count += 1
    summary = {"episode_id": state["episode_id"], "sim_time_min": state["sim_time_min"],
        "configured_key_loaded": configured,
        "runtime_status": state["runtime_status"], "agent_status": state["agent_status"],
        "adversary_status": state.get("adversary_status"), "real_enemy_parameter_receipts": receipt_count,
        "boats": len(state["uavs"]), "search_regions": len(state["search_regions"]),
        "metrics": state["mission_metrics"], "contacts": [{key: c.get(key) for key in ("contact_id", "state", "uncertainty_m")} for c in state["contacts"]],
        "latest_jobs": [{key: job.get(key) for key in ("run_id", "status", "error")} for job in jobs[-3:]],
        "private_enemy_status": enemy.get("status")}
    print(json.dumps(summary, ensure_ascii=False))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
