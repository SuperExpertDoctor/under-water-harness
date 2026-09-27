import asyncio
import contextlib
import copy
import hashlib
import json
import math
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .runtime import ALGORITHM_IDS, MissionRuntime, MissionError, identifier
from .agent_events import session_event


TOOL_NAMES = ("get_mission_state", "get_observations", "partition_search_area", "compute_task_allocation", "plan_path", "plan_search", "plan_tracking", "evaluate_plan", "submit_mission_plan", "get_action_status")
SKILL_ID = "multi-uuv-recon-tracking"
WORKER_LEASE_SECONDS = 15
WORKER_LIVENESS_SECONDS = 30  # Includes the healthy worker's 15-second success cooldown.


def create_app(db_path=None, worker_token=None, ticking=True):
    db_path = db_path or os.environ.get("UUV_DB", "tools/.runtime/mission.sqlite")
    token = worker_token or os.environ.get("UUV_WORKER_TOKEN", "")
    browser_secret = secrets.token_urlsafe(32)
    runtime = MissionRuntime(db_path)

    def requeue_feedback(job):
        if runtime.status == "stopped" or job["episode_id"] != runtime.episode:
            return
        if job["status"] == "failed" and job.get("accepted_feedback"):
            job["feedback"] = job.pop("accepted_feedback") + job.get("feedback", [])
        if job["status"] == "failed" and job.get("source_feedback_id") and not job.get("source_feedback_recovered"):
            source = job.get("source_feedback", {"id": job["source_feedback_id"], "text": job["text"], "delivery": "followUp", "attempt": 1})
            if source.get("attempt", 1) < 3:
                job["feedback"] = [{**source, "attempt": source.get("attempt", 1)+1}] + job.get("feedback", [])
            else:
                for message in runtime.messages:
                    if message.get("feedback_id") == source["id"]:
                        message.update(status="failed", delivery_status="failed")
                runtime.event("agent_feedback_failed", {"run_id": job["run_id"], "feedback_id": source["id"], "reason": "recovery_attempts_exhausted"})
            job["source_feedback_recovered"] = True
        while job.get("feedback"):
            feedback = job["feedback"][0]
            previous_count = len(runtime.agent_jobs)
            retry_position = next((index+1 for index, current in enumerate(runtime.agent_jobs) if current is job), 0)
            try:
                queued = runtime.queue_agent(feedback["text"], "feedback", delivery=feedback.get("delivery", "followUp"))
            except MissionError as error:
                if error.code == "agent_queue_full":
                    return
                raise
            if queued is None:
                return
            queued["source_feedback_id"] = feedback["id"]
            queued["source_feedback"] = copy.deepcopy(feedback)
            if feedback["id"] == job.get("source_feedback_id"):
                # A retry of older feedback must precede already queued newer input.
                trimmed = max(0, previous_count+1-len(runtime.agent_jobs))
                runtime.agent_jobs.remove(queued)
                runtime.agent_jobs.insert(max(0, retry_position-trimmed), queued)
            for message in runtime.messages:
                if message.get("feedback_id") == feedback["id"]:
                    message.update(run_id=queued["run_id"], status="queued", delivery_status="requeued")
            job["feedback"].pop(0)

    def expire_worker_leases(now):
        jobs = [job for job in runtime.agent_jobs if
            (job["status"] == "running" and job.get("lease_deadline", 0) <= now) or
            (job["status"] in ("failed", "completed") and (job.get("feedback") or job.get("accepted_feedback"))) or
            (job["status"] == "failed" and job.get("source_feedback_id") and not job.get("source_feedback_recovered"))]
        if not jobs:
            return
        with runtime.transaction():
            for job in jobs:
                if job["status"] == "running":
                    job.update(status="failed", error="worker_lease_expired")
                    runtime.event("agent_failed", {"run_id": job["run_id"], "error": "worker_lease_expired"})
                    runtime.agent.update(status="degraded", error="worker_lease_expired")
                requeue_feedback(job)
            runtime.save()

    def queue_user_input(text, delivery="followUp", annotation=None):
        with runtime.lock:
            expire_worker_leases(time.monotonic())
            return runtime.queue_agent(text, delivery=delivery, annotation=annotation)

    def active_run(data):
        runtime.check_episode(data.get("episode_id"))
        expire_worker_leases(time.monotonic())
        job = next((job for job in runtime.agent_jobs if job["run_id"] == data.get("run_id")), None)
        if not job or job["status"] != "running" or job["episode_id"] != runtime.episode:
            raise MissionError("run_not_active")
        return job

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async def loop():
            accumulator = 0.0
            last = time.monotonic()
            last_save = last
            while True:
                now = time.monotonic()
                accumulator += min(now-last, .5)*runtime.config.simulation_speed
                last = now
                while accumulator >= runtime.config.dt:
                    with runtime.lock:
                        try:
                            runtime.tick()
                        except Exception as exc:
                            runtime.status = "safety_paused"
                            runtime.event("runtime_error", {"error_code": "simulation_tick_failed", "exception_type": type(exc).__name__})
                            runtime.save()
                            accumulator = 0
                            break
                    accumulator -= runtime.config.dt
                if now-last_save >= 1:
                    with runtime.lock:
                        runtime.save()
                        runtime.store.frame(runtime.episode, runtime.frame())
                    last_save = now
                with runtime.lock:
                    expire_worker_leases(now)
                    if runtime.last_heartbeat and now-runtime.last_heartbeat > WORKER_LIVENESS_SECONDS:
                        runtime.agent["status"] = "offline"
                await asyncio.sleep(.05)
        task = asyncio.create_task(loop()) if ticking else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            runtime.close()

    app = FastAPI(title="UUV Mission Control", lifespan=lifespan)
    app.state.runtime = runtime
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver", "[::1]"])

    @app.exception_handler(MissionError)
    async def mission_error(_request, exc):
        return JSONResponse({"error_code": exc.code, "message": exc.code}, status_code=exc.status)

    @app.exception_handler(ValueError)
    async def invalid_value(_request, exc):
        return JSONResponse({"error_code": "invalid_input", "message": str(exc)[:200]}, status_code=422)

    @app.middleware("http")
    async def authorize(request, call_next):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost", "testserver", "::1"):
            return JSONResponse({"error_code": "origin_not_allowed"}, status_code=403)
        if request.url.path.startswith("/internal/"):
            provided = request.headers.get("authorization", "")
            if not token or not secrets.compare_digest(provided, f"Bearer {token}"):
                return JSONResponse({"error_code": "worker_auth_required"}, status_code=403)
        elif request.method not in ("GET", "HEAD", "OPTIONS"):
            if not secrets.compare_digest(request.cookies.get("uuv_operator", ""), browser_secret):
                return JSONResponse({"error_code": "operator_session_required"}, status_code=403)
        return await call_next(request)

    async def payload(request, episode=True):
        data = await request.json()
        json.dumps(data, allow_nan=False)
        if not isinstance(data, dict):
            raise MissionError("object_required", 422)
        if episode:
            runtime.check_episode(data.get("episode_id"))
        return data

    def receipt(data, operation):
        with runtime.transaction():
            runtime.check_episode(data.get("episode_id"))
            command = data.get("command_id")
            if not isinstance(command, str) or not 1 <= len(command) <= 120:
                raise MissionError("command_id_required", 422)
            digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
            prior = runtime.receipts.get(command)
            if prior:
                if prior["digest"] != digest:
                    raise MissionError("idempotency_conflict")
                return copy.deepcopy(prior["response"])
            result = operation()
            response = {"command_id": command, "status": "applied", **(result or {})}
            runtime.receipts[command] = {"digest": digest, "response": response}
            runtime.save()
            return copy.deepcopy(response)

    async def invoke(name, data, worker=False):
        if name not in TOOL_NAMES:
            raise MissionError("unknown_tool", 404)
        if name in ("partition_search_area", "compute_task_allocation", "plan_path", "plan_search", "plan_tracking"):
            with runtime.lock:
                if worker:
                    active_run(data)
            result = await asyncio.to_thread(runtime.calculate, name, data)
            with runtime.lock:
                if worker:
                    active_run(data)
            return result
        with runtime.lock:
            if worker:
                active_run(data)
            if name == "get_mission_state":
                return runtime.mission_state()
            if name == "get_observations":
                records = [o for o in runtime.observations if o["time_s"] > float(data.get("after_s", -1)) and o.get("sequence", 0) > int(data.get("cursor", -1))]
                records = records[:min(100, max(1, int(data.get("limit", 50))))]
                return {"observations": copy.deepcopy(records), "cursor": records[-1]["sequence"] if records else runtime.observation_cursor}
            if name == "evaluate_plan":
                return runtime.evaluate(data.get("result_id"))
            if name == "submit_mission_plan":
                if not data.get("command_id"):
                    raise MissionError("command_id_required", 422)
                return runtime.submit(data.get("result_id"), data["command_id"], data.get("episode_id"))
            action_id = data.get("action_id")
            result = runtime.plans.get(action_id) or runtime.results.get(action_id) or runtime.receipts.get(action_id) or runtime.store.get_plan(action_id)
            result = result or next((j for j in runtime.agent_jobs if j["run_id"] == action_id), None)
            if not result:
                raise MissionError("action_not_found", 404)
            return copy.deepcopy(runtime.summary(result))

    @app.get("/api/health")
    async def health():
        response = JSONResponse({"status": "ok", "service": "uuv-runtime", "agent": runtime.agent["status"]})
        response.set_cookie("uuv_operator", browser_secret, httponly=True, samesite="strict")
        return response

    @app.get("/api/config")
    async def config():
        return runtime.config.public()

    @app.get("/api/state")
    async def state():
        return runtime.frame()

    @app.get("/api/scene")
    async def scene():
        with runtime.lock:
            return {"episode_id": runtime.episode, "scenario_vessels": copy.deepcopy(runtime.vessels), "vessel_mutation_allowed": runtime.status != "stopped"}

    @app.post("/api/simulation/{operation}")
    async def simulation(operation, request: Request):
        await payload(request)
        if operation not in ("start", "pause", "stop", "reset"):
            raise MissionError("unknown_operation", 404)
        getattr(runtime, operation)()
        return {"status": runtime.status, "episode_id": runtime.episode}

    @app.get("/api/events")
    async def events(after: int = 0, limit: int = 200):
        with runtime.lock:
            return {"events": copy.deepcopy([e for e in runtime.events if e["id"] > after][:min(500, max(1, limit))]), "cursor": runtime.cursor}

    @app.websocket("/ws/{stream}")
    async def stream(websocket: WebSocket, stream: str):
        origin = websocket.headers.get("origin")
        if stream not in ("live", "state") or (origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost", "testserver", "::1")):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        cursor = -1
        episode = None
        try:
            while True:
                message = None
                with runtime.lock:
                    if episode != runtime.episode:
                        cursor = -1
                        episode = runtime.episode
                    if stream == "live":
                        message = copy.deepcopy(runtime.frame())
                    elif runtime.cursor != cursor:
                        message = copy.deepcopy({"type": "state", "episode_id": runtime.episode, "cursor": runtime.cursor,
                            "agent": runtime.agent, "messages": runtime.messages, "jobs": runtime.agent_jobs,
                            "plans": [runtime.summary(p) for p in runtime.plans.values()], "autonomy_mode": runtime.mode,
                            "events": [e for e in runtime.events if e["id"] > cursor][-500:]})
                        cursor = runtime.cursor
                if message is not None:
                    await websocket.send_json(message)
                try:
                    message = await asyncio.wait_for(websocket.receive_text(), timeout=.1 if stream == "live" else .3)
                    if message == "ping":
                        await websocket.send_text("pong")
                except asyncio.TimeoutError:
                    pass
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.get("/api/pi-agent/status")
    async def agent_status():
        with runtime.lock:
            return {**runtime.agent, "queued": sum(j["status"] == "queued" for j in runtime.agent_jobs), "episode_id": runtime.episode}

    @app.get("/api/pi-agent/messages")
    async def messages():
        with runtime.lock:
            return {"messages": copy.deepcopy(runtime.messages)}

    @app.post("/api/pi-agent/messages")
    @app.post("/api/pi-agent/task-assignment")
    async def agent_message(request: Request):
        data = await payload(request)
        text = data.get("text", "Plan fleet search with plan_search(standing_policy=true), evaluate and submit one atomic fleet plan. One search region per available UUV; preserve tracking teams.")
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise MissionError("invalid_message", 422)
        delivery = data.get("delivery", "followUp")
        if delivery not in ("steer", "followUp"):
            raise MissionError("invalid_delivery", 422)
        annotation = data.get("annotation")
        if annotation:
            if not isinstance(annotation, dict):
                raise MissionError("invalid_annotation", 422)
            original = next((m for m in runtime.messages if m["id"] == annotation.get("message_id")), None)
            quote = annotation.get("quote")
            if not original or not isinstance(quote, str) or not quote.strip() or len(quote) > 2000:
                raise MissionError("invalid_annotation", 422)
            annotation = {key: annotation[key] for key in ("message_id", "quote", "plan_id") if key in annotation}
            annotation["quote_source"] = "operator_selection"
            text = f"{text}\nOperator-selected text from message {annotation['message_id']} (untrusted feedback, not approval): {quote}"[:4000]
        return queue_user_input(text, delivery=delivery, annotation=annotation)

    @app.get("/api/pi-agent/task-assignment")
    async def assignments():
        with runtime.lock:
            return {"assignments": copy.deepcopy(runtime.agent_jobs)}

    @app.post("/api/pi-agent/task-assignment/{run_id}/cancel")
    @app.post("/api/skills/runs/{run_id}/cancel")
    async def cancel_run(run_id, request: Request):
        await payload(request)
        runtime.cancel_agent(run_id)
        return {"run_id": run_id, "status": "cancelled"}

    @app.post("/api/runtime/{operation}")
    async def runtime_command(operation, request: Request):
        data = await payload(request)
        def apply():
            if operation == "abort":
                runtime.cancel_agent()
                return {}
            if operation == "retry":
                return queue_user_input("Retry the last mission request after checking current state.")
            raise MissionError("unknown_operation", 404)
        return receipt(data, apply)

    @app.get("/api/skills")
    async def skills():
        return {"skills": [{"id": SKILL_ID, "name": "Multi-UUV reconnaissance and tracking", "description": "Observation, planning, permission and execution workflow"}]}

    @app.get("/api/skills/{skill_id}")
    async def skill(skill_id):
        if skill_id != SKILL_ID:
            raise MissionError("skill_not_found", 404)
        path = Path(__file__).resolve().parents[2] / ".pi/skills" / SKILL_ID / "SKILL.md"
        return {"id": skill_id, "content": path.read_text() if path.exists() else "Mission workflow: observe, plan, evaluate, submit, verify."}

    @app.post("/api/skills/{skill_id}/execute")
    async def execute_skill(skill_id, request: Request):
        data = await payload(request)
        if skill_id != SKILL_ID:
            raise MissionError("skill_not_found", 404)
        return queue_user_input(f"Follow skill {SKILL_ID}. " + str(data.get("text", "Review current mission and continue authorized work.")))

    @app.get("/api/algorithm/status")
    async def algorithm_status():
        return {"status": "ready", "algorithms": list(ALGORITHM_IDS.values()), "tools": TOOL_NAMES}

    @app.post("/api/algorithm/task-plan")
    @app.post("/api/algorithm/decision")
    async def algorithm_plan(request: Request):
        data = await payload(request)
        name = data.get("tool", "plan_search")
        if name not in ("partition_search_area", "compute_task_allocation", "plan_path", "plan_search", "plan_tracking", "evaluate_plan"):
            raise MissionError("calculation_only", 422)
        return await invoke(name, data)

    @app.post("/api/algorithm/commands")
    async def algorithm_command(request: Request):
        return await invoke("submit_mission_plan", await payload(request))

    @app.get("/api/task-assembly/tasks")
    async def tasks():
        with runtime.lock:
            return {"tasks": copy.deepcopy(runtime.tasks)}

    @app.post("/api/task-assembly/assemble")
    async def assemble(request: Request):
        data = await payload(request)
        with runtime.lock:
            runtime.check_episode(data.get("episode_id"))
            result = runtime.results.get(data.get("result_id"))
            if not result:
                raise MissionError("result_not_found", 404)
            task = {"task_id": identifier("task"), "result_id": result["result_id"], "label": str(data.get("label", "Mission plan"))[:100], "status": "draft", "revision": 1}
            runtime.tasks.append(task)
            runtime.tasks = runtime.tasks[-100:]
            runtime.save()
            return copy.deepcopy(task)

    @app.get("/api/task-assembly/tasks/{task_id}")
    async def get_task(task_id):
        with runtime.lock:
            task = next((t for t in runtime.tasks if t["task_id"] == task_id), None)
            if not task:
                raise MissionError("task_not_found", 404)
            return copy.deepcopy(task)

    @app.patch("/api/task-assembly/tasks/{task_id}")
    async def update_task(task_id, request: Request):
        data = await payload(request)
        with runtime.lock:
            runtime.check_episode(data.get("episode_id"))
            task = next((t for t in runtime.tasks if t["task_id"] == task_id), None)
            if not task:
                raise MissionError("task_not_found", 404)
            if data.get("expected_revision") != task["revision"]:
                raise MissionError("stale_revision")
            if set(data) - {"episode_id", "expected_revision", "label", "command_id"}:
                raise MissionError("recompute_plan_to_change_execution", 422)
            task.update(label=str(data.get("label", task["label"]))[:100], revision=task["revision"]+1)
            runtime.save()
            return copy.deepcopy(task)

    @app.get("/api/approvals")
    async def approvals():
        with runtime.lock:
            return {"approvals": copy.deepcopy([runtime.summary(p) for p in runtime.plans.values() if p["status"] == "pending_approval"])}

    @app.post("/api/approvals/{plan_id}/decision")
    async def decide(plan_id, request: Request):
        data = await payload(request)
        if data.get("decision") not in ("approve", "reject"):
            raise MissionError("invalid_decision", 422)
        return runtime.decide(plan_id, data["decision"] == "approve")

    @app.post("/api/permissions/mode")
    async def mode(request: Request):
        data = await payload(request)
        runtime.set_mode(data.get("mode"))
        return {"mode": runtime.mode, "policy_version": runtime.policy_version}

    @app.get("/api/plans/{plan_id}")
    async def plan(plan_id):
        with runtime.lock:
            result = runtime.plans.get(plan_id) or runtime.results.get(plan_id) or runtime.store.get_plan(plan_id)
            if not result:
                raise MissionError("plan_not_found", 404)
            return copy.deepcopy(result)

    @app.get("/api/actions/{action_id}")
    async def action(action_id):
        return await invoke("get_action_status", {"action_id": action_id})

    @app.post("/api/intents")
    @app.patch("/api/intents/{intent_id}")
    @app.delete("/api/intents/{intent_id}")
    async def intent(request: Request, intent_id: str = ""):
        data = await payload(request)
        def apply():
            current = next((i for i in runtime.intents if i["intent_id"] == intent_id), None)
            if intent_id and (not current or data.get("expected_revision") != current["revision"]):
                raise MissionError("stale_intent")
            if request.method == "DELETE":
                current["lifecycle"] = "cancelled"
                current["revision"] += 1
            else:
                bbox = data.get("bbox")
                if not isinstance(bbox, list) or len(bbox) != 4 or not all(isinstance(v, (int, float)) and 0 <= v <= 40 for v in bbox) or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                    raise MissionError("invalid_bbox", 422)
                duration = float(data.get("valid_duration_min", 120))
                if not 1 <= duration <= 10080:
                    raise MissionError("invalid_duration", 422)
                item = {"intent_id": intent_id or identifier("intent"), "revision": (current["revision"] if current else 0)+1,
                    "label": str(data.get("label", "Region"))[:80], "bbox": bbox, "mode": data.get("mode", "search_priority"),
                    "priority": data.get("priority", "medium"), "weight": min(2, max(0, float(data.get("weight", .5)))),
                    "expires_at_min": runtime.sim_time/60+duration, "revisit_interval_min": data.get("revisit_interval_min"), "lifecycle": "active"}
                if current:
                    current.update(item)
                else:
                    runtime.intents.append(item)
                    runtime.intents = runtime.intents[-100:]
            runtime.event("intent_changed", {"intent_id": intent_id})
            runtime.queue_agent("Human mission region changed. Read current intents and review plans.", "intent_changed")
            return {}
        return receipt(data, apply)

    @app.get("/api/intent-commands/{command_id}")
    @app.get("/api/vessel-commands/{command_id}")
    async def command_receipt(command_id):
        with runtime.lock:
            if command_id not in runtime.receipts:
                raise MissionError("command_not_found", 404)
            return copy.deepcopy(runtime.receipts[command_id]["response"])

    @app.post("/api/vessels")
    @app.delete("/api/vessels/{vessel_id}")
    @app.patch("/api/vessels/{vessel_id}/ais")
    async def vessel(request: Request, vessel_id: str = ""):
        data = await payload(request)
        if runtime.status == "stopped":
            raise MissionError("mission_stopped")
        def apply():
            current = next((v for v in runtime.vessels if v["scenario_entity_id"] == vessel_id), None)
            if vessel_id and (not current or data.get("expected_revision") != current["revision"]):
                raise MissionError("stale_vessel")
            if request.method == "DELETE":
                runtime.vessels.remove(current)
                runtime.targets = [target for target in runtime.targets if target["id"] != vessel_id]
                contact_id = runtime.contact_mapping.pop(vessel_id, None)
                runtime.contacts.pop(contact_id, None)
                runtime.observations = [sample for sample in runtime.observations if sample["contact_id"] != contact_id]
            elif request.method == "PATCH":
                if not current["ais_controllable"]:
                    raise MissionError("ais_fixed")
                if not isinstance(data.get("ais_enabled"), bool):
                    raise MissionError("boolean_required", 422)
                current.update(ais_enabled=data["ais_enabled"], revision=current["revision"]+1)
                for target in runtime.targets:
                    if target["id"] == vessel_id:
                        target["ais_enabled"] = data["ais_enabled"]
            else:
                position = data.get("position_cells")
                if data.get("vessel_class") not in ("type_i", "type_ii") or not isinstance(position, list) or len(position) != 2 or not all(isinstance(v, (int, float)) and 0 <= v < 40 for v in position):
                    raise MissionError("invalid_vessel", 422)
                if len(runtime.vessels) >= 20:
                    raise MissionError("scene_capacity")
                entity_id = identifier("vessel")
                x, y = (position[0]+.5)*100, 4000-(position[1]+.5)*100
                if not 0 <= x <= 4000 or not 0 <= y <= 4000:
                    raise MissionError("invalid_vessel", 422)
                heading = math.atan2(2000-y, 2000-x)
                ais_mmsi = str(900000000+int(entity_id[-8:], 16) % 100000000)
                runtime.targets.append({"id": entity_id, "pose": [x, y, heading], "speed": 2.5,
                    "vessel_class": data["vessel_class"], "ais_enabled": True, "ais_mmsi": ais_mmsi, "ais_synthetic": True})
                runtime.vessels.append({"scenario_entity_id": entity_id, "vessel_class": data["vessel_class"], "position": position,
                    "heading_deg": -math.degrees(heading), "ais_mmsi": ais_mmsi, "ais_synthetic": True,
                    "revision": 1, "ais_enabled": True, "ais_controllable": data["vessel_class"] == "type_ii", "surveillance_stage": "undetected"})
            runtime.event("scene_changed", {})
            return {}
        return receipt(data, apply)

    @app.get("/api/replay/list")
    async def replay_list():
        with runtime.lock:
            return {"files": runtime.store.episodes()}

    @app.get("/api/replay")
    async def replay(file: str, offset: int = 0, limit: int = 120):
        with runtime.lock:
            if file not in runtime.store.episodes():
                raise MissionError("replay_not_found", 404)
            return runtime.store.replay(file, max(0, offset), min(120, max(1, limit)))

    @app.get("/api/export/capabilities")
    async def export_capabilities():
        return {"mp4": shutil.which("ffmpeg") is not None}

    @app.post("/api/export/mp4")
    async def export_mp4(request: Request):
        executable = shutil.which("ffmpeg")
        if not executable:
            raise MissionError("ffmpeg_unavailable", 503)
        if request.headers.get("content-type") != "video/webm":
            raise MissionError("webm_required", 415)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 50*1024*1024:
                raise MissionError("video_too_large", 413)
        if body[:4] != b"\x1aE\xdf\xa3":
            raise MissionError("invalid_webm", 422)
        def encode():
            with tempfile.TemporaryDirectory(prefix="uuv-export-") as directory:
                source, dest = Path(directory)/"source.webm", Path(directory)/"output.mp4"
                source.write_bytes(body)
                try:
                    result = subprocess.run([executable, "-nostdin", "-loglevel", "error", "-i", str(source), "-an", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)], capture_output=True, timeout=60)
                except subprocess.TimeoutExpired as exc:
                    raise MissionError("encoding_timeout", 504) from exc
                if result.returncode:
                    raise MissionError("encoding_failed", 422)
                return dest.read_bytes()
        return Response(await asyncio.to_thread(encode), media_type="video/mp4")

    @app.post("/api/test/{kind}")
    async def test_event(kind, request: Request):
        data = await payload(request)
        if data.get("debug") is not True:
            raise MissionError("explicit_debug_required", 403)
        if kind not in ("target-detected", "target-lost"):
            raise MissionError("unknown_test", 404)
        with runtime.lock:
            runtime.check_episode(data.get("episode_id"))
            runtime.sensor_enabled = kind == "target-detected"
            runtime.event("debug_sensor_changed", {"enabled": runtime.sensor_enabled})
            return {"status": "applied", "sensor_enabled": runtime.sensor_enabled}

    @app.post("/internal/tools/{name}")
    async def tool(name, request: Request):
        data = await payload(request, episode=False)
        with runtime.lock:
            active_run(data)
            params = {key: copy.deepcopy(data[key]) for key in ("mission_revision", "algorithm_id", "members", "bbox", "goal",
                "contact_id", "result_id", "command_id", "action_id", "mode", "after_s", "limit", "standing_policy", "cursor") if key in data}
            if isinstance(data.get("tasks"), list):
                params["tasks"] = [{key: copy.deepcopy(task[key]) for key in ("id", "center", "size", "priority") if key in task}
                    for task in data["tasks"][:8] if isinstance(task, dict)]
            runtime.event("tool_started", {"tool": name, "run_id": data["run_id"], "params": params})
        try:
            result = await invoke(name, data, worker=True)
            with runtime.lock:
                active_run(data)
                summary = {key: copy.deepcopy(result[key]) for key in ("result_id", "plan_id", "valid", "errors", "risk",
                    "requires_approval", "members", "kind", "algorithm", "mission_revision", "policy_version") if key in result}
                runtime.event("tool_completed", {"tool": name, "run_id": data["run_id"],
                    "status": result.get("status", "succeeded"), **summary})
            return result
        except Exception as exc:
            with runtime.lock:
                if data.get("episode_id") == runtime.episode:
                    runtime.event("tool_failed", {"tool": name, "run_id": data.get("run_id"),
                        "error_code": exc.code if isinstance(exc, MissionError) else "tool_error"})
            raise

    @app.post("/internal/agent/next")
    async def next_job(request: Request):
        data = await payload(request, episode=False)
        defer_routine = data.get("defer_routine", False)
        if not isinstance(defer_routine, bool):
            raise MissionError("boolean_required", 422)
        with runtime.lock:
            now = time.monotonic()
            expire_worker_leases(now)
            runtime.last_heartbeat = now
            running = next((j for j in runtime.agent_jobs if j["status"] == "running"), None)
            priorities = {"human": 0, "feedback": 0, "target_found": 1, "target_lost": 1, "energy_exit": 1}
            eligible = [job for job in runtime.agent_jobs if job["status"] == "queued"
                and (not defer_routine or priorities.get(job["source"], 2) < 2)]
            job = None if running else min(eligible, key=lambda job: priorities.get(job["source"], 2), default=None)
            if job:
                job.update(status="running", lease_deadline=now+WORKER_LEASE_SECONDS)
                runtime.agent.update(status="running", error=None, cycle=runtime.agent["cycle"]+1)
                runtime.event("agent_started", {"run_id": job["run_id"]})
                runtime.save()
            elif not running:
                runtime.agent["status"] = "idle"
            return {"job": copy.deepcopy(job), "episode_id": runtime.episode}

    @app.post("/internal/agent/heartbeat")
    async def heartbeat(request: Request):
        data = await payload(request, episode=False)
        with runtime.lock:
            try:
                job = active_run(data)
            except MissionError:
                return {"cancel": True}
            now = time.monotonic()
            runtime.last_heartbeat = now
            job["lease_deadline"] = now+WORKER_LEASE_SECONDS
            acknowledged_ids = data.get("acknowledged_feedback_ids", [])
            if not isinstance(acknowledged_ids, list) or len(acknowledged_ids) > 20 or not all(isinstance(value, str) and len(value) <= 160 for value in acknowledged_ids):
                raise MissionError("invalid_feedback_acknowledgment", 422)
            acknowledged = set(acknowledged_ids).intersection(f["id"] for f in job.get("feedback", []))
            if acknowledged:
                with runtime.transaction():
                    job.setdefault("accepted_feedback", []).extend(f for f in job.get("feedback", []) if f["id"] in acknowledged)
                    job["feedback"] = [f for f in job.get("feedback", []) if f["id"] not in acknowledged]
                    for message in runtime.messages:
                        if message.get("run_id") == job["run_id"] and message.get("feedback_id") in acknowledged:
                            message.update(status="completed", delivery_status="delivered")
                    runtime.event("agent_feedback_delivered", {"run_id": job["run_id"], "feedback_ids": sorted(acknowledged)})
                    runtime.save()
            return {"cancel": False, "feedback": copy.deepcopy(job.get("feedback", []))}

    @app.post("/internal/agent/event")
    async def worker_event(request: Request):
        data = await payload(request)
        with runtime.lock:
            job = active_run(data)
            kind = data.get("type")
            if kind == "session_event":
                event = data.get("event")
                if not isinstance(event, dict):
                    raise MissionError("invalid_session_event", 422)
                session_event(runtime, job, event)
                return {"status": "ok"}
            if kind not in ("completed", "failed"):
                raise MissionError("invalid_agent_event", 422)
            with runtime.transaction():
                job["status"] = kind
                runtime.agent.update(status="idle" if kind == "completed" else "degraded", error=data.get("error"))
                existing = [m for m in runtime.messages if m.get("run_id") == job["run_id"] and m["role"] == "assistant"]
                if kind == "completed" and not existing:
                    runtime.messages.append({"id": identifier("message"), "role": "assistant", "text": str(data.get("text", ""))[:8000], "time": runtime.sim_time, "model": runtime.config.model})
                    runtime.messages = runtime.messages[-100:]
                for message in existing:
                    message["status"] = "completed" if kind == "completed" else "failed"
                if kind == "completed":
                    job["accepted_feedback"] = []
                    for message in runtime.messages:
                        if message.get("feedback_id") == job.get("source_feedback_id") and job.get("source_feedback_id"):
                            message.update(status="completed", delivery_status="processed")
                requeue_feedback(job)
                runtime.event(f"agent_{kind}", {"run_id": job["run_id"], "error": data.get("error")})
                runtime.save()
            return {"status": "ok"}

    return app
