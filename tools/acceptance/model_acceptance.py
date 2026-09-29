"""Explicit, separately invoked live-model acceptance; never imported by tests."""
import argparse
import json
from pathlib import Path
import time

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api")
    parser.add_argument("--reset", action="store_true", help="Explicitly begin a fresh acceptance episode")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mission", action="store_true")
    mode.add_argument("--fleet", action="store_true")
    args = parser.parse_args()
    if not args.api:
        args.api = json.loads(Path("outputs/runtime/services.json").read_text())["backend_url"]
    with httpx.Client(base_url=args.api, timeout=20) as client:
        client.get("/api/health").raise_for_status()
        state = client.get("/api/state").json()
        if args.reset:
            client.post("/api/simulation/reset", json={"episode_id": state["episode_id"]}).raise_for_status()
            state = client.get("/api/state").json()
        cursor = state["event_cursor"]
        prompt = "仅执行读取验收：调用 get_mission_state，告诉我当前己方UUV总数和权限模式。不要规划或提交任务。"
        if args.mission:
            prompt = "开始演示任务：为UUV-1生成搜索计划，members只包含UUV-1，bbox为[300,300,1700,1700]米，评估后提交计划。其他艇暂不派遣。不要重复提交。"
        if args.fleet:
            prompt = "读取任务状态并加载 multi-uuv-recon-tracking Skill，为8艘UUV准备第二版区域搜索演示。调用 plan_search，省略members，standing_policy=true，生成一个完整舰队方案；evaluate_plan后submit_mission_plan一次。每艘搜索艇独占一个连通区域，8艇应有8区，不再使用3+3+2分组搜索。保持仿真就绪，等人类点击开始。简短中文汇报。"
        response = client.post("/api/pi-agent/messages", json={"episode_id": state["episode_id"], "text": prompt})
        response.raise_for_status()
        run_id = response.json()["run_id"]
        deadline = time.monotonic()+210
        while time.monotonic() < deadline:
            jobs = client.get("/api/pi-agent/task-assignment").json()["assignments"]
            job = next(j for j in jobs if j["run_id"] == run_id)
            if job["status"] in ("completed", "failed", "cancelled"):
                events = []
                while True:
                    batch = client.get("/api/events", params={"limit": 500, "after": cursor}).json()["events"]
                    events.extend(batch)
                    if not batch or len(batch) < 500:
                        break
                    cursor = batch[-1]["id"]
                report = {"run_id": run_id, "status": job["status"], "agent": client.get("/api/pi-agent/status").json(),
                    "messages": client.get("/api/pi-agent/messages").json()["messages"][-2:],
                    "tool_events": [e for e in events if e["type"].startswith("tool_")],
                    "native_event_types": sorted({e["type"] for e in events if e.get("data", {}).get("run_id") == run_id})}
                directory = Path("tools/artifacts")
                directory.mkdir(exist_ok=True)
                if args.fleet:
                    report["teams"] = client.get("/api/state").json()["teams"]
                (directory/("longcat-fleet.json" if args.fleet else "longcat-mission.json" if args.mission else "longcat-read.json")).write_text(json.dumps(report, indent=2, ensure_ascii=False))
                print(json.dumps(report, ensure_ascii=False))
                if job["status"] != "completed":
                    raise SystemExit(1)
                if not any(e["type"] == "tool_completed" and e["data"]["tool"] == "get_mission_state" for e in report["tool_events"]):
                    raise SystemExit("No real get_mission_state tool receipt")
                if args.mission or args.fleet:
                    completed = {e["data"]["tool"] for e in report["tool_events"] if e["type"] == "tool_completed"}
                    if not {"plan_search", "evaluate_plan", "submit_mission_plan"} <= completed:
                        raise SystemExit("Mission did not complete real plan/evaluate/submit tool calls")
                if args.fleet:
                    members = [member for team in report["teams"] for member in team["members"]]
                    if len(set(members)) != 8 or len(members) != 8 or not all(len(team["members"]) == 1 for team in report["teams"]):
                        raise SystemExit("Fleet allocation did not activate eight uniquely owned search regions")
                return
            time.sleep(1)
        raise SystemExit("LongCat acceptance timed out")


if __name__ == "__main__":
    main()
