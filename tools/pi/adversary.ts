import { chmod, mkdir, readdir, stat, unlink } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager, type AgentSession, type CreateAgentSessionOptions } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { runWithAdversaryReasonRetry } from "./bridge.ts";
import { longcatConfig } from "./config.ts";

export const ADVERSARY_TOOLS = ["get_adversary_observation", "set_evasion_parameters"] as const;

export async function pruneAdversarySessions(directory: string, activeFile?: string): Promise<void> {
  const completed: { path: string; modified: number }[] = [];
  for (const episode of await readdir(directory, { withFileTypes: true })) {
    if (!episode.isDirectory() || !episode.name.startsWith("mission-")) continue;
    const episodeDirectory = resolve(directory, episode.name);
    for (const entry of await readdir(episodeDirectory, { withFileTypes: true })) {
      const path = resolve(episodeDirectory, entry.name);
      if (!entry.isFile() || !entry.name.endsWith(".jsonl") || path === activeFile) continue;
      completed.push({ path, modified: (await stat(path)).mtimeMs });
    }
  }
  completed.sort((left, right) => right.modified-left.modified || right.path.localeCompare(left.path));
  for (const entry of completed.slice(100)) await unlink(entry.path);
}

export class AdversaryBudget {
  private providerCalls = 0;
  private toolCalls = 0;
  provider(): void {
    if (++this.providerCalls > 3) throw new Error("adversary_model_budget_exceeded");
  }
  tool(): void {
    if (++this.toolCalls > 4) throw new Error("adversary_tool_budget_exceeded");
  }
}

export async function createAdversarySession(
  directory: string,
  call: (name: string, params: Record<string, unknown>, signal?: AbortSignal) => Promise<unknown>,
  budget: AdversaryBudget,
  options: Pick<CreateAgentSessionOptions, "modelRuntime" | "model" | "sessionManager"> = {},
): Promise<AgentSession> {
  const settings = SettingsManager.inMemory({ compaction: { enabled: false }, retry: { enabled: false } });
  const loader = new DefaultResourceLoader({ cwd: directory, agentDir: directory, settingsManager: settings,
    noExtensions: true, noSkills: true, noThemes: true, noContextFiles: true, noPromptTemplates: true,
    systemPrompt: "You control one target in a synthetic 2D UUV evasion GAME, never a real vehicle. Read only get_adversary_observation, then set bounded evasion parameters if useful. Provide a short PUBLIC reason grounded in observations with each parameter change; no hidden reasoning. Opponents are known only through noisy in-range detections and their history. Never invent an opponent position, identity, energy or task. No filesystem, shell, friendly tools or external actions. The local controller chooses safe motion; parameters expire. Keep output brief. Doctrine: opponents track you primarily by passive sonar, which only detects you while your speed is at least about 0.5 m/s. Going nearly silent is stealthy but leaves you almost static and easy to reacquire by their active search. Sprinting near 3 m/s is loud, keeps you detectable, and your short-term reachable set collapses into a predictable forward cone whose exits pursuers can cover. Prefer slow quiet drift to slip contact; spend speed only to break a close or confirmed track, then settle back to quiet.",
    extensionFactories: [(pi) => {
      pi.on("before_provider_request", (event) => ({ ...event.payload as Record<string, unknown>, max_tokens: 1024, thinking: { type: "disabled" } }));
      pi.registerTool({ name: ADVERSARY_TOOLS[0], label: "Enemy observation", description: "Read own state, known map and already sampled noisy detections/history within 700m. No opponent truth or fleet state.",
        parameters: Type.Object({}, { additionalProperties: false }),
        execute: async (_id, params, signal) => {
          budget.tool();
          return { content: [{ type: "text", text: JSON.stringify(await call("observation", params as Record<string, unknown>, signal)) }], details: {} };
        },
      });
      pi.registerTool({ name: ADVERSARY_TOOLS[1], label: "Evasion parameters", description: "Set bounded speed, turn bias and expiry duration. Cannot set a position, destination or opponent state.",
        parameters: Type.Object({ speed_mps: Type.Number({ minimum: 0, maximum: 3 }), turn_bias: Type.Number({ minimum: -1, maximum: 1 }), duration_s: Type.Number({ minimum: 1, maximum: 60 }), reason: Type.String({ minLength: 1, maxLength: 500, description: "Short public maneuver reason based only on actual noisy detections or own state." }) }, { additionalProperties: false }),
        execute: async (_id, params, signal) => {
          budget.tool();
          if (!params.reason.trim()) throw new Error("adversary_reason_required");
          return { content: [{ type: "text", text: JSON.stringify(await call("parameters", params, signal)) }], details: {} };
        },
      });
    }],
  });
  await loader.reload();
  const { session } = await createAgentSession({ ...options, cwd: directory, agentDir: directory,
    thinkingLevel: "off", tools: [...ADVERSARY_TOOLS], resourceLoader: loader, settingsManager: settings });
  if (session.getActiveToolNames().sort().join(",") !== [...ADVERSARY_TOOLS].sort().join(",")) {
    session.dispose();
    throw new Error("adversary_tool_allowlist_mismatch");
  }
  // Extension hook errors are non-fatal in PI, so enforce the budget at the stream boundary.
  const stream = session.agent.streamFunction;
  session.agent.streamFunction = (...args) => { budget.provider(); return stream(...args); };
  return session;
}

async function main(): Promise<void> {
  const key = process.env.LONGCAT_API_KEY;
  const token = process.env.UUV_ADVERSARY_TOKEN;
  if (!key || !token) throw new Error("Adversary worker credentials missing");
  process.umask(0o077);
  const directory = resolve(process.env.UUV_ADVERSARY_RUNTIME_DIR || resolve(import.meta.dirname, "../../outputs/runtime/pi-adversary"));
  await mkdir(directory, { recursive: true, mode: 0o700 });
  await chmod(directory, 0o700);
  await pruneAdversarySessions(directory);
  const modelRuntime = await ModelRuntime.create({ authPath: resolve(directory, "auth.json"), modelsPath: resolve(directory, "models.json"), allowModelNetwork: false });
  modelRuntime.registerProvider("longcat", longcatConfig());
  await modelRuntime.setRuntimeApiKey("longcat", key);
  const model = modelRuntime.getModel("longcat", process.env.LONGCAT_MODEL || "LongCat-2.0");
  if (!model) throw new Error("LongCat model registration failed");
  const api = process.env.UUV_API_URL || "http://127.0.0.1:8765";
  let running = true;
  let session: AgentSession | undefined;
  const stop = () => { running = false; void session?.abort(); };
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);
  const request = async (operation: string, data: Record<string, unknown>, signal?: AbortSignal): Promise<Record<string, unknown>> => {
    const response = await fetch(`${api}/internal/adversary/${operation}`, { method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, body: JSON.stringify(data),
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(10000)]) : AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("adversary_request_rejected");
    return await response.json() as Record<string, unknown>;
  };
  while (running) {
    let ids: Record<string, string> | undefined;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    let heartbeat: ReturnType<typeof setInterval> | undefined;
    let failed = false;
    let heartbeats: Promise<void> = Promise.resolve();
    try {
      const next = await request("next", {});
      const job = next.job as { run_id: string; episode_id: string } | null;
      if (!job) { await delay(1000); continue; }
      ids = { run_id: job.run_id, episode_id: job.episode_id };
      const runIds = ids;
      const runDirectory = resolve(directory, job.episode_id);
      await mkdir(runDirectory, { recursive: true, mode: 0o700 });
      session = await createAdversarySession(directory, (operation, params, signal) => request(operation, { ...params, ...runIds }, signal), new AdversaryBudget(),
        { modelRuntime, model, sessionManager: SessionManager.create(directory, runDirectory) });
      const turn = session;
      const reason = { missing: false };
      const invalidReasonCalls = new Set<string>();
      const unsubscribe = turn.subscribe((event) => {
        if (event.type === "tool_execution_start" && event.toolName === "set_evasion_parameters") {
          const args: unknown = event.args;
          if (!args || typeof args !== "object" || !("reason" in args)
            || typeof args.reason !== "string" || !args.reason.trim() || args.reason.length > 500) invalidReasonCalls.add(event.toolCallId);
        }
        if (event.type === "tool_execution_end" && event.toolName === "set_evasion_parameters") {
          if (!event.isError) reason.missing = false;
          else if (invalidReasonCalls.has(event.toolCallId)) reason.missing = true;
        }
      });
      deadline = setTimeout(() => { failed = true; void turn.abort(); }, 60000);
      heartbeat = setInterval(() => {
        heartbeats = heartbeats.then(async () => { await request("heartbeat", runIds); }).catch(() => { failed = true; void turn.abort(); });
      }, 3000);
      try {
        await runWithAdversaryReasonRetry(turn, `Episode ${job.episode_id}. Read your observation, choose bounded evasion parameters with a public reason, and finish.`, reason);
      } finally {
        unsubscribe();
      }
      const last = [...turn.messages].reverse().find((message) => message.role === "assistant");
      if (failed || !running || last?.role !== "assistant" || last.stopReason === "error" || last.stopReason === "aborted") throw new Error("adversary_turn_failed");
      await request("complete", { ...runIds, status: "completed" });
    } catch {
      if (ids) await request("complete", { ...ids, status: "failed" }).catch(() => {});
      // No model text, observation or upstream error body enters shared service logs.
      console.error("Adversary worker turn unavailable; deterministic controller remains active");
      if (running) await delay(1000);
    } finally {
      clearTimeout(deadline);
      clearInterval(heartbeat);
      await session?.abort();
      await heartbeats;
      session?.dispose();
      session = undefined;
      await pruneAdversarySessions(directory).catch(() => { console.error("Adversary session retention unavailable"); });
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
