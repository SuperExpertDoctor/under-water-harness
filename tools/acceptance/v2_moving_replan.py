"""Explicit real-model search replanning while an approved simulation keeps running."""

from datetime import datetime, timezone
import json
from pathlib import Path
import time

import httpx

from v2_live_acceptance import Acceptance, ROOT, require


def tracking_assignments(state):
    return sorted((boat["id"], boat["generation"], boat["team_id"], boat["target_group_id"])
        for boat in state["uavs"] if boat["operation_mode"] == "track")


def main():
    services = json.loads((ROOT/"tools/.runtime/services.json").read_text())
    report = {"status": "failed", "started_at": datetime.now(timezone.utc).isoformat(), "events": [],
        "states": [], "operator_actions": [], "runs": {}, "errors": []}
    started = time.monotonic()
    with httpx.Client(base_url=services["backend_url"], timeout=20) as client:
        acceptance = Acceptance(client, started+240, report)
        try:
            acceptance.request("GET", "/api/health")
            initial = acceptance.request("GET", "/api/state")
            acceptance.episode, acceptance.cursor = initial["episode_id"], initial["event_cursor"]
            acceptance.phase = "moving_human_replan"
            require(initial["runtime_status"] == "running", "An already running mission is required")
            require(initial["autonomy_mode"] == "full", "Operator must have selected Full autonomy")
            original_tracking = tracking_assignments(initial)
            require(len(original_tracking) >= 2, "An established assigned tracking team is required")
            job = acceptance.operator("/api/pi-agent/messages", "explicit moving search replan through native feedback",
                delivery="followUp", text="这是一次明确的人工搜索航线调整请求，不是普通巡检。"
                "先读取当前状态，然后用 plan_search 省略 members，standing_policy=true，生成当前可用搜索艇的舰队搜索候选，"
                "evaluate_plan 后 submit_mission_plan 一次。保留现有跟踪队及其跟踪计划，绝不把跟踪艇纳入搜索。"
                "不要暂停、停止、重置仿真或改变权限。整个规划、评估、提交过程必须在艇队继续运动时完成。")
            run_id = job["run_id"]
            report["runs"]["moving_search_replan"] = {"run_id": run_id}
            while True:
                state = acceptance.state()
                require(state["runtime_status"] == "running", "Simulation stopped during moving replanning")
                require(tracking_assignments(state) == original_tracking, "Human search replan changed the tracking team")
                jobs = acceptance.jobs()
                job = next(job for job in jobs if job["run_id"] == run_id)
                if job["status"] in ("completed", "failed", "cancelled"):
                    require(job["status"] == "completed", f"Model run ended {job['status']}")
                    report["runs"]["moving_search_replan"]["terminal_job"] = job
                    break
                time.sleep(.5)
            events = acceptance.receipts(run_id, {"get_mission_state", "plan_search", "evaluate_plan", "submit_mission_plan"})
            submitted = [event for event in events if event["type"] == "tool_completed"
                and event["data"].get("tool") == "submit_mission_plan"]
            require(len(submitted) == 1 and submitted[0]["data"].get("status") == "active", "No single real active submission")
            plan = next(event for event in events if event["type"] == "tool_started" and event["data"].get("tool") == "plan_search")
            require("members" not in plan["data"]["params"], "Expected automatic available-search-fleet selection")
            require(submitted[0]["time"] > plan["time"], "Simulation did not advance through plan and submit")
            require(not any(event["type"] == "tool_failed" for event in events), "Moving tool sequence had a failure")
            report.update(status="passed", episode_id=acceptance.episode,
                tracking_assignments=original_tracking, initial_sim_s=initial["sim_time_min"]*60,
                final_sim_s=state["sim_time_min"]*60,
                plan_to_submit_sim_s=(submitted[0]["time"]-plan["time"])*60)
        except (Exception, KeyboardInterrupt) as error:
            report["errors"].append({"type": type(error).__name__, "message": str(error)})
    report["elapsed_wall_s"] = time.monotonic()-started
    Path("outputs/v2-moving-replan.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({key: report.get(key) for key in ("status", "elapsed_wall_s", "plan_to_submit_sim_s", "errors")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
