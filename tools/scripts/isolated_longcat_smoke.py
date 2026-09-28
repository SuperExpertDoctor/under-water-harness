"""Isolated live LongCat + PI tool smoke; never touches the active mission."""

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx


ROOT = Path(__file__).resolve().parents[2]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def main():
    credentials = ROOT / "tools/.runtime/credentials.env"
    key = os.environ.get("LONGCAT_API_KEY")
    if not key and credentials.exists():
        key = next((line.partition("=")[2].strip() for line in credentials.read_text().splitlines()
                    if line.startswith("LONGCAT_API_KEY=")), None)
    if not key:
        raise RuntimeError("LONGCAT_API_KEY not configured")
    with tempfile.TemporaryDirectory(prefix="uuv-longcat-") as scratch:
        temporary = Path(scratch)
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        environment = {**os.environ, "UUV_API_URL": url,
            "UUV_DB": str(temporary / "mission.sqlite"),
            "UUV_WORKER_TOKEN": secrets.token_urlsafe(32),
            "UUV_ADVERSARY_TOKEN": secrets.token_urlsafe(32),
            "UUV_PI_RUNTIME_DIR": str(temporary / "pi"),
            "LONGCAT_API_KEY": key,
            "PYTHONPATH": str(ROOT / "tools")}
        backend_env = {name: value for name, value in environment.items() if name != "LONGCAT_API_KEY"}
        backend = subprocess.Popen([sys.executable, "-m", "uvicorn", "uuv_game.api:create_app", "--factory",
            "--host", "127.0.0.1", "--port", str(port), "--no-access-log"], cwd=ROOT,
            env=backend_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        worker = None
        try:
            with httpx.Client(base_url=url, timeout=10) as client:
                for _ in range(60):
                    try:
                        response = client.get("/api/health")
                        if response.is_success:
                            break
                    except httpx.RequestError:
                        pass
                    if backend.poll() is not None:
                        raise RuntimeError("isolated backend exited before startup")
                    time.sleep(.25)
                else:
                    raise RuntimeError("isolated backend startup timed out")
                worker = subprocess.Popen(["node", "--import", "./packages/coding-agent/src/experimental/source-resolver.ts",
                    "tools/pi/worker.ts"], cwd=ROOT, env=environment,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                state = client.get("/api/state").json()
                cursor = state["event_cursor"]
                response = client.post("/api/pi-agent/messages", json={"episode_id": state["episode_id"],
                    "text": "仅执行读取验收：调用 get_mission_state，告诉我当前己方 UUV 总数和权限模式。不要规划或提交任务。"})
                response.raise_for_status()
                run_id = response.json()["run_id"]
                for _ in range(180):
                    job = next(item for item in client.get("/api/pi-agent/task-assignment").json()["assignments"]
                               if item["run_id"] == run_id)
                    if job["status"] in ("completed", "failed", "cancelled"):
                        break
                    if worker.poll() is not None:
                        raise RuntimeError("isolated LongCat worker exited before completion")
                    time.sleep(1)
                else:
                    raise RuntimeError("isolated LongCat request timed out")
                events = client.get("/api/events", params={"limit": 500, "after": cursor}).json()["events"]
                completed_tools = [item["data"].get("tool") for item in events if item["type"] == "tool_completed"]
                report = {"job_status": job["status"], "model": client.get("/api/pi-agent/status").json().get("model"),
                    "completed_tools": completed_tools, "sim_time_s": state["sim_time_min"] * 60,
                    "tool_receipt_verified": "get_mission_state" in completed_tools}
                output = ROOT / "outputs/longcat-isolated-smoke.json"
                output.parent.mkdir(exist_ok=True)
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(report, ensure_ascii=False))
                if job["status"] != "completed" or not report["tool_receipt_verified"]:
                    raise SystemExit(1)
        finally:
            for process in (worker, backend):
                if process is None:
                    continue
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
