import { mkdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager, type AgentSession } from "@earendil-works/pi-coding-agent";
import { longcatConfig, redact, TOOL_NAMES } from "./config.ts";
import { missionExtension } from "./extension.ts";

const root = resolve(import.meta.dirname, "../..");
const api = process.env.UUV_API_URL || "http://127.0.0.1:8765";
const workerToken = process.env.UUV_WORKER_TOKEN;
const key = process.env.LONGCAT_API_KEY;
if (!workerToken || !key) throw new Error("Set UUV_WORKER_TOKEN and LONGCAT_API_KEY before starting PI worker");
const secrets = [workerToken, key];
const runtimeDir = resolve(root, "tools/.runtime/pi");
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

async function request(path: string, data: unknown, signal?: AbortSignal): Promise<Record<string, unknown>> {
  const response = await fetch(`${api}${path}`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${workerToken}` },
    body: JSON.stringify(data), signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000) });
  const result = await response.json() as Record<string, unknown>;
  if (!response.ok) throw new Error(String(result.error_code || `HTTP_${response.status}`));
  return result;
}

async function makeSession(episode: string): Promise<AgentSession> {
  const skill = await readFile(resolve(root, ".pi/skills/multi-uuv-recon-tracking/SKILL.md"), "utf8");
  const settings = SettingsManager.inMemory({ compaction: { enabled: true }, retry: { enabled: true, maxRetries: 1, baseDelayMs: 2000 } });
  const loader = new DefaultResourceLoader({ cwd: runtimeDir, agentDir: runtimeDir, settingsManager: settings,
    noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
    systemPrompt: `You coordinate a 2D eight-UUV reconnaissance GAME. Reply in concise Chinese. Only use registered mission tools. No real-world vehicle control. Never invent execution, observations or approvals. ${skill}`,
    extensionFactories: [missionExtension(async (name, params, signal) => {
      if (!activeJob || ++calls > 24) throw new Error("turn_tool_budget_exceeded");
      const job = activeJob;
      const heartbeat = await request("/internal/agent/heartbeat", { run_id: job.run_id, episode_id: job.episode_id }, signal);
      if (heartbeat.cancel) throw new Error("run_cancelled");
      return request(`/internal/tools/${name}`, { ...params, run_id: job.run_id, episode_id: job.episode_id }, signal);
    })],
  });
  await loader.reload();
  const { session: created } = await createAgentSession({ cwd: runtimeDir, agentDir: runtimeDir, modelRuntime, model,
    thinkingLevel: "off", tools: [...TOOL_NAMES], resourceLoader: loader, settingsManager: settings,
    sessionManager: SessionManager.continueRecent(runtimeDir, resolve(runtimeDir, episode)) });
  if (created.getActiveToolNames().length !== TOOL_NAMES.length) throw new Error("Mission tool registration incomplete");
  return created;
}

process.on("SIGTERM", () => { running = false; void session?.abort(); });
process.on("SIGINT", () => { running = false; void session?.abort(); });
console.log("PI worker ready: LongCat, mission tools only");
while (running) {
  try {
    const next = await request("/internal/agent/next", {});
    const job = next.job as Job | null;
    if (!job) { await delay(1000); continue; }
    activeJob = job;
    calls = 0;
    if (!session || sessionEpisode !== job.episode_id) {
      session?.dispose();
      session = await makeSession(job.episode_id);
      sessionEpisode = job.episode_id;
    }
    const turnSession = session;
    const deadline = setTimeout(() => { void turnSession.abort(); }, 90000);
    const heartbeat = setInterval(() => {
      void request("/internal/agent/heartbeat", { run_id: job.run_id, episode_id: job.episode_id }).then((value) => {
        if (value.cancel) void turnSession.abort();
      }).catch(() => { void turnSession.abort(); });
    }, 3000);
    try {
      await turnSession.prompt(`Episode ${job.episode_id}. Trigger: ${job.source}. ${job.text}\nFirst use get_mission_state. Preserve existing plans unless a change is needed.`);
      const last = [...turnSession.messages].reverse().find((message) => message.role === "assistant");
      if (last?.role === "assistant" && (last.stopReason === "error" || last.stopReason === "aborted")) {
        throw new Error(last.errorMessage || last.stopReason);
      }
      await request("/internal/agent/event", { run_id: job.run_id, episode_id: job.episode_id, type: "completed", text: redact(turnSession.getLastAssistantText() || "本轮没有新增操作。", secrets) });
    } finally {
      clearTimeout(deadline);
      clearInterval(heartbeat);
    }
    activeJob = undefined;
    await delay(15000);
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
