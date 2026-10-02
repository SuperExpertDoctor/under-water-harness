"""Start local mission services without putting credentials in argv or UI.

The launcher is the module skeleton of the project: it boots every
functional directory (tools/ backend API, ui/ frontend, agent/ PI worker
and adversary) and creates a fresh timestamped run directory under
outputs/<YYYYMMDD-HHMMSS>/ holding that run's sqlite db, worker tokens,
service logs, PI runtime state and recordings. outputs/runtime/ keeps
cross-run shared state only (credentials.env, services.json).
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "outputs/runtime"
LAUNCHER_PATHS = (ROOT / "adapter/launcher.py", ROOT / "tools/launcher.py", ROOT / "tools/scripts/run.py")


def free_port(preferred):
    for port in range(preferred, preferred+100):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free local port")


def is_launcher_command(arguments):
    return len(arguments) > 1 and arguments[1] in (path.as_posix().encode() for path in LAUNCHER_PATHS) and b"--foreground" in arguments


def is_our_supervisor(pid):
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        return is_launcher_command(arguments)
    except OSError:
        return False


def worker_environments(env):
    return ({key: value for key, value in env.items() if key != "UUV_ADVERSARY_TOKEN"},
            {key: value for key, value in env.items() if key != "UUV_WORKER_TOKEN"})


def validate_service_payload(health, state):
    if not isinstance(health, dict) or health.get("status") != "ok" or health.get("service") != "uuv-runtime":
        raise ValueError("health response is not the UUV API")
    episode = state.get("episode_id") if isinstance(state, dict) else None
    if not isinstance(episode, str) or not episode or episode == "local-demo":
        raise ValueError("episode missing or local demo frame returned")
    return episode


def probe_frontend(ui_url):
    def read(path):
        with urlopen(f"{ui_url}{path}", timeout=2) as response:
            if "json" not in response.headers.get("content-type", ""):
                raise ValueError(f"{path} did not return JSON")
            return json.load(response)
    return validate_service_payload(read("/api/health"), read("/api/state"))


def service_status(prior, alive):
    if not alive:
        return {**prior, "status": "not_running"}
    try:
        episode = probe_frontend(prior["ui_url"])
        return {**prior, "status": "running", "episode_id": episode, "error": None}
    except (KeyError, OSError, ValueError, TimeoutError, json.JSONDecodeError) as failure:
        return {**prior, "status": "degraded", "error": f"frontend {prior.get('ui_url')}/api/health or /api/state -> backend {prior.get('backend_url')}: {failure}"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--no-model", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ui-port", type=int, default=5173)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(RUNTIME, 0o700)
    metadata = RUNTIME/"services.json"
    prior = json.loads(metadata.read_text()) if metadata.exists() else {}
    alive = is_our_supervisor(prior.get("supervisor_pid"))
    if args.stop:
        if alive:
            os.kill(prior["supervisor_pid"], signal.SIGTERM)
            print(json.dumps({"status": "stopping", "supervisor_pid": prior["supervisor_pid"]}))
        else:
            print(json.dumps({"status": "not_running"}))
        return
    if args.status or (alive and not args.foreground):
        print(json.dumps(service_status(prior, alive)))
        return
    if not args.foreground:
        command = [sys.executable, str(Path(__file__).resolve()), "--foreground", "--port", str(args.port), "--ui-port", str(args.ui_port)]
        if args.no_model:
            command.append("--no-model")
        with (RUNTIME / "services.log").open("a") as log:
            child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
        print(json.dumps({"supervisor_pid": child.pid, "status": "starting", "details": "outputs/runtime/services.json"}))
        return
    env = dict(os.environ)
    credentials = RUNTIME / "credentials.env"
    if credentials.exists():
        for line in credentials.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                name, value = line.split("=", 1)
                env.setdefault(name.strip(), value.strip())
    run_dir = RUNTIME.parent / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    token_path = run_dir / "worker.token"
    token_path.write_text(secrets.token_urlsafe(32))
    os.chmod(token_path, 0o600)
    env["UUV_WORKER_TOKEN"] = token_path.read_text().strip()
    enemy_token_path = run_dir / "adversary.token"
    enemy_token_path.write_text(secrets.token_urlsafe(32))
    os.chmod(enemy_token_path, 0o600)
    env["UUV_ADVERSARY_TOKEN"] = enemy_token_path.read_text().strip()
    port, ui_port = free_port(args.port), free_port(args.ui_port)
    env["UUV_API_URL"] = f"http://127.0.0.1:{port}"
    env["UUV_UI_URL"] = f"http://127.0.0.1:{ui_port}"
    env["VITE_BACKEND_PORT"] = str(port)
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT), str(ROOT / "tools")))
    env["UUV_DB"] = str(run_dir / "mission.sqlite")
    env["UUV_RECORDING_DIR"] = str(run_dir)
    env["UUV_PI_RUNTIME_DIR"] = str(run_dir / "pi")
    env["UUV_ADVERSARY_RUNTIME_DIR"] = str(run_dir / "pi-adversary")
    api_env = {key: value for key, value in env.items() if key != "LONGCAT_API_KEY"}
    ui_env = {key: value for key, value in api_env.items() if key not in ("UUV_WORKER_TOKEN", "UUV_ADVERSARY_TOKEN")}
    children = []
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True
        for child in children:
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    commands = [
        ("api", [sys.executable, "-m", "uvicorn", "adapter.api:create_app", "--factory", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"], ROOT, api_env),
        ("ui", ["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", str(ui_port), "--strictPort"], ROOT/"ui", ui_env),
    ]
    if not args.no_model:
        if not env.get("LONGCAT_API_KEY"):
            raise RuntimeError("LONGCAT_API_KEY missing; configure outputs/runtime/credentials.env or use --no-model")
        friendly_env, enemy_env = worker_environments(env)
        commands.append(("pi", ["node", "--import", "./packages/coding-agent/src/experimental/source-resolver.ts", "agent/worker.ts"], ROOT, friendly_env))
        commands.append(("pi-adversary", ["node", "--import", "./packages/coding-agent/src/experimental/source-resolver.ts", "agent/adversary.ts"], ROOT, enemy_env))
    logs = []
    for name, command, cwd, child_env in commands:
        log = (run_dir/f"{name}.log").open("a")
        logs.append(log)
        children.append(subprocess.Popen(command, cwd=cwd, env=child_env, stdout=log, stderr=log))
    services = {"supervisor_pid": os.getpid(), "run_dir": str(run_dir), "backend_url": env["UUV_API_URL"], "ui_url": f"http://127.0.0.1:{ui_port}", "status": "starting", "pids": {spec[0]: child.pid for spec, child in zip(commands, children)}}
    (RUNTIME/"services.json").write_text(json.dumps(services, indent=2))
    try:
        deadline = time.monotonic() + 30
        while not stopping:
            try:
                services["episode_id"] = probe_frontend(services["ui_url"])
                services["status"] = "running"
                (RUNTIME/"services.json").write_text(json.dumps(services, indent=2))
                print(json.dumps(services), flush=True)
                break
            except (OSError, ValueError, TimeoutError, json.JSONDecodeError) as failure:
                if time.monotonic() >= deadline or any(child.poll() is not None for child in children[:2]):
                    services.update(status="failed", error=f"frontend {services['ui_url']}/api/health or /api/state -> backend {services['backend_url']}: {failure}")
                    (RUNTIME/"services.json").write_text(json.dumps(services, indent=2))
                    print(json.dumps(services), flush=True)
                    return
                time.sleep(.5)
        while not stopping:
            for index, child in enumerate(children):
                if child.poll() is not None:
                    name, command, cwd, child_env = commands[index]
                    print(f"{name} exited ({child.returncode}); restarting in 5 seconds", flush=True)
                    time.sleep(5)
                    if not stopping:
                        children[index] = subprocess.Popen(command, cwd=cwd, env=child_env, stdout=logs[index], stderr=logs[index])
                        services["pids"][name] = children[index].pid
                        (RUNTIME/"services.json").write_text(json.dumps(services, indent=2))
            time.sleep(.5)
    finally:
        stop(None, None)
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
