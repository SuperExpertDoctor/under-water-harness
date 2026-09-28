import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { createAgentSession, ModelRuntime, SessionManager, SettingsManager, type AgentSession } from "@earendil-works/pi-coding-agent";
import { longcatConfig, redact, TOOL_NAMES } from "./config.ts";
import { FeedbackDelivery, PublicEventProjector, runWithReasonRetry } from "./bridge.ts";
import { createMissionResources } from "./resources.ts";
import { RoutineCooldown, withRunHeartbeat } from "./scheduling.ts";

const root = resolve(import.meta.dirname, "../..");
const api = process.env.UUV_API_URL || "http://127.0.0.1:8765";
const workerToken = process.env.UUV_WORKER_TOKEN;
const key = process.env.LONGCAT_API_KEY;
if (!workerToken || !key) throw new Error("Set UUV_WORKER_TOKEN and LONGCAT_API_KEY before starting PI worker");
const secrets = [workerToken, key];
const runtimeDir = resolve(process.env.UUV_PI_RUNTIME_DIR || resolve(root, "tools/.runtime/pi"));
await mkdir(runtimeDir, { recursive: true, mode: 0o700 });
const modelRuntime = await ModelRuntime.create({ authPath: resolve(runtimeDir, "auth.json"), modelsPath: resolve(runtimeDir, "models.json"), allowModelNetwork: false });
modelRuntime.registerProvider("longcat", longcatConfig());
await modelRuntime.setRuntimeApiKey("longcat", key);
const model = modelRuntime.getModel("longcat", process.env.LONGCAT_MODEL || "LongCat-2.0");
if (!model) throw new Error("LongCat model registration failed");

interface Job { run_id: string; episode_id: string; text: string; source: string }
let activeJob: Job | undefined;
let session: AgentSession | undefined;
let sessionEpisode = "";
let running = true;
let calls = 0;
let feedback: FeedbackDelivery | undefined;
let heartbeatTask: Promise<void> = Promise.resolve();
let runFailure: Error | undefined;
let deadline: ReturnType<typeof setTimeout> | undefined;
let startDeadline: (() => void) | undefined;
const schedule = new RoutineCooldown();

async function request(path: string, data: unknown, signal?: AbortSignal): Promise<Record<string, unknown>> {
  const response = await fetch(`${api}${path}`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${workerToken}` },
    body: JSON.stringify(data), signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000) });
  const result = await response.json() as Record<string, unknown>;
  if (!response.ok) throw new Error(String(result.error_code || `HTTP_${response.status}`));
  return result;
}

async function makeSession(episode: string): Promise<AgentSession> {
  const settings = SettingsManager.inMemory({ compaction: { enabled: true }, retry: { enabled: true, maxRetries: 1, baseDelayMs: 2000 } });
  const loader = createMissionResources(runtimeDir, resolve(root, ".pi/skills/multi-uuv-recon-tracking"), settings, async (name, params, signal) => {
      if (!activeJob || ++calls > 24) throw new Error("turn_tool_budget_exceeded");
      const job = activeJob;
      await heartbeatRun(job, signal);
      if (runFailure) throw runFailure;
      const result = await request(`/internal/tools/${name}`, { ...params, run_id: job.run_id, episode_id: job.episode_id }, signal);
      if (name !== "submit_mission_plan" || result.status !== "pending_approval" || typeof result.plan_id !== "string") return result;
      clearTimeout(deadline);
      deadline = undefined;
      try {
        let current = result;
        while (current.status === "pending_approval") {
          await delay(2000, undefined, { signal });
          if (activeJob !== job || runFailure) throw runFailure || new Error("run_cancelled");
          await heartbeatRun(job, signal);
          current = await request("/internal/tools/get_action_status", { action_id: result.plan_id, run_id: job.run_id, episode_id: job.episode_id }, signal);
        }
        return current;
      } finally {
        if (activeJob === job && !runFailure) startDeadline?.();
      }
  });
  await loader.reload();
  const { session: created } = await createAgentSession({ cwd: runtimeDir, agentDir: runtimeDir, modelRuntime, model,
    thinkingLevel: "off", tools: [...TOOL_NAMES, "read"], resourceLoader: loader, settingsManager: settings,
    sessionManager: SessionManager.continueRecent(runtimeDir, resolve(runtimeDir, episode)) });
  if (created.getActiveToolNames().sort().join(",") !== [...TOOL_NAMES, "read"].sort().join(",")) throw new Error("Mission tool registration incomplete");
  return created;
}

function heartbeatRun(job: Job, signal?: AbortSignal): Promise<void> {
  heartbeatTask = heartbeatTask.then(async () => {
    if (activeJob !== job) return;
    const ids = feedback?.acknowledgedIds() || [];
    const response = await request("/internal/agent/heartbeat", { run_id: job.run_id, episode_id: job.episode_id, acknowledged_feedback_ids: ids }, signal);
    feedback?.confirmAcknowledged(ids);
    if (response.cancel) throw new Error("run_cancelled");
    if (session?.isStreaming) await feedback?.deliver(response.feedback);
  });
  return heartbeatTask;
}

process.on("SIGTERM", () => { running = false; void session?.abort(); });
process.on("SIGINT", () => { running = false; void session?.abort(); });
console.log("PI worker ready: LongCat, ten mission tools and trusted skill read");
while (running) {
  try {
    const next = await request("/internal/agent/next", schedule.nextRequest(performance.now()));
    const job = next.job as Job | null;
    if (!job) { await delay(1000); continue; }
    activeJob = job;
    calls = 0;
    runFailure = undefined;
    heartbeatTask = Promise.resolve();
    if (!session || sessionEpisode !== job.episode_id) {
      session?.dispose();
      session = await makeSession(job.episode_id);
      sessionEpisode = job.episode_id;
    }
    const turnSession = session;
    feedback = new FeedbackDelivery(turnSession);
    const projector = new PublicEventProjector(job.run_id, secrets);
    let events: Promise<void> = Promise.resolve();
    let eventCount = 0;
    const reasonState = { missing: false };
    const invalidReasonCalls = new Set<string>();
    const unsubscribe = turnSession.subscribe((event) => {
      const projected = projector.project(event);
      if (!projected) return;
      if (event.type === "tool_execution_start" && event.toolName === "submit_mission_plan") {
        const args: unknown = event.args;
        if (!args || typeof args !== "object" || !("decision_reason" in args)
          || typeof args.decision_reason !== "string" || !args.decision_reason.trim() || args.decision_reason.trim().length > 500) invalidReasonCalls.add(event.toolCallId);
      }
      if (event.type === "tool_execution_end" && event.toolName === "submit_mission_plan") {
        if (!event.isError) reasonState.missing = false;
        else if (invalidReasonCalls.has(event.toolCallId) || projected.text?.includes("decision_reason_required") || projected.text?.includes("invalid_decision_reason")) reasonState.missing = true;
      }
      if (++eventCount > 12000) {
        runFailure = new Error("turn_event_budget_exceeded");
        void turnSession.abort();
        return;
      }
      events = events.then(async () => {
        if (runFailure) return;
        await request("/internal/agent/event", { run_id: job.run_id, episode_id: job.episode_id, type: "session_event", event: projected });
      }).catch((error: unknown) => {
        runFailure = error instanceof Error ? error : new Error("event_delivery_failed");
        void turnSession.abort();
      });
    });
    startDeadline = () => { deadline = setTimeout(() => { runFailure = new Error("turn_deadline_exceeded"); void turnSession.abort(); }, 180000); };
    startDeadline();
    try {
      await withRunHeartbeat(async () => {
        await runWithReasonRetry(turnSession, `Episode ${job.episode_id}. Trigger: ${job.source}. ${job.text}\nFirst read the multi-uuv-recon-tracking Skill using read, then get_mission_state. Preserve existing plans unless a change is needed.`, reasonState);
        await heartbeatRun(job);
        await events;
        if (runFailure) throw runFailure;
        const last = [...turnSession.messages].reverse().find((message) => message.role === "assistant");
        if (last?.role === "assistant" && (last.stopReason === "error" || last.stopReason === "aborted")) {
          throw new Error(last.errorMessage || last.stopReason);
        }
        await request("/internal/agent/event", { run_id: job.run_id, episode_id: job.episode_id, type: "completed", text: redact(turnSession.getLastAssistantText() || "本轮没有新增操作。", secrets) });
      }, () => heartbeatRun(job), (error: unknown) => {
        runFailure = error instanceof Error ? error : new Error("heartbeat_failed");
        void turnSession.abort();
      });
    } finally {
      clearTimeout(deadline);
      deadline = undefined;
      startDeadline = undefined;
      unsubscribe();
      await turnSession.abort();
      await heartbeatTask.catch(() => {});
      await events;
      feedback = undefined;
    }
    activeJob = undefined;
    schedule.completed(performance.now());
  } catch (error) {
    const message = redact(error instanceof Error ? error.message : "worker_error", secrets).slice(0, 300);
    if (activeJob) {
      await request("/internal/agent/event", { run_id: activeJob.run_id, episode_id: activeJob.episode_id, type: "failed", error: message }).catch(() => {});
      activeJob = undefined;
    }
    console.error(`PI worker: ${message}`);
    await delay(5000);
  }
}
session?.dispose();
