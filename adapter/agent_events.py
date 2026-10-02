"""Bounded public projections of native PI session events."""

PUBLIC_EVENTS = {"message_start", "message_update", "message_end", "tool_execution_start", "tool_execution_update",
    "tool_execution_end", "compaction_start", "compaction_end", "auto_retry_start", "auto_retry_end", "agent_start",
    "agent_end", "agent_settled", "queue_update", "turn_start", "turn_end", "summarization_retry_scheduled",
    "summarization_retry_attempt_start", "summarization_retry_finished"}


def session_event(runtime, job, event):
    kind = event.get("type")
    if kind not in PUBLIC_EVENTS:
        raise ValueError("unknown_session_event")
    data = {key: event[key] for key in ("type", "message_id", "tool_call_id", "tool_name", "is_error", "count", "reason", "attempt", "will_retry", "status") if key in event}
    text = str(event.get("text", ""))[:12000]
    data.update(run_id=job["run_id"], text=text)
    if kind.startswith("message_"):
        message_id = str(event.get("message_id", ""))[:160]
        if not message_id:
            raise ValueError("message_id_required")
        message = next((m for m in runtime.messages if m["id"] == message_id and m.get("run_id") == job["run_id"]), None)
        if message is None:
            message = {"id": message_id, "role": "assistant", "text": "", "time": runtime.sim_time,
                "model": runtime.config.model, "run_id": job["run_id"], "status": "streaming"}
            runtime.messages.append(message)
        message["text"] = (message["text"]+text)[-12000:] if kind == "message_update" else text
        if kind == "message_end":
            message["status"] = "failed" if event.get("status") == "failed" else "completed"
        runtime.messages = runtime.messages[-100:]
    runtime.event(kind, data)
