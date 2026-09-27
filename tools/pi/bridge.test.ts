import assert from "node:assert/strict";
import { test } from "node:test";
import type { AgentSessionEvent } from "@earendil-works/pi-coding-agent";
import type { AssistantMessage } from "@earendil-works/pi-ai";
import { FeedbackDelivery, PublicEventProjector, runUntilSettled } from "./bridge.ts";

const assistant: AssistantMessage = { role: "assistant", content: [{ type: "text", text: "public" }, { type: "thinking", thinking: "hidden" }], api: "openai-completions", provider: "longcat", model: "test", usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } }, stopReason: "stop", timestamp: 1 };

test("public event projection keeps message identity and strips thinking and secrets", () => {
  const project = new PublicEventProjector("run-1", ["credential"]);
  const start = project.project({ type: "message_start", message: structuredClone(assistant) } as AgentSessionEvent);
  const update = project.project({ type: "message_update", message: structuredClone(assistant), assistantMessageEvent: { type: "text_delta", contentIndex: 0, delta: "credential", partial: structuredClone(assistant) } } as AgentSessionEvent);
  const end = project.project({ type: "message_end", message: structuredClone(assistant) } as AgentSessionEvent);
  assert.equal(start?.message_id, update?.message_id);
  assert.equal(end?.message_id, start?.message_id);
  assert.equal(update?.text, "[REDACTED]");
  assert.equal(end?.text, "public");
  assert.ok(!JSON.stringify(end).includes("hidden"));
  assert.equal(project.project({ type: "message_update", message: structuredClone(assistant), assistantMessageEvent: { type: "thinking_delta", contentIndex: 1, delta: "hidden", partial: structuredClone(assistant) } } as AgentSessionEvent), undefined);
  assert.deepEqual(project.project({ type: "agent_settled" }), { type: "agent_settled" });
  assert.equal(project.project({ type: "auto_retry_start", attempt: 1, maxAttempts: 2, delayMs: 10, errorMessage: "credential" })?.text, "[REDACTED]");
});

test("tool events have public call identity, result and error status", () => {
  const project = new PublicEventProjector("run", ["credential"]);
  const result = project.project({ type: "tool_execution_end", toolCallId: "c1", toolName: "get_mission_state", result: { content: [{ type: "text", text: "credential" }], details: { private: "not-public" } }, isError: true });
  assert.equal(result?.tool_call_id, "c1");
  assert.equal(result?.text, "[REDACTED]");
  assert.equal(result?.is_error, true);
  assert.ok(!JSON.stringify(result).includes("not-public"));
});

test("credentials split across stream chunks are never forwarded", () => {
  const project = new PublicEventProjector("run", ["credential"]);
  project.project({ type: "message_start", message: structuredClone(assistant) });
  const first = project.project({ type: "message_update", message: structuredClone(assistant), assistantMessageEvent: { type: "text_delta", contentIndex: 0, delta: "cred", partial: structuredClone(assistant) } });
  const second = project.project({ type: "message_update", message: structuredClone(assistant), assistantMessageEvent: { type: "text_delta", contentIndex: 0, delta: "ential", partial: structuredClone(assistant) } });
  assert.equal(first, undefined);
  assert.equal(second?.text, "[REDACTED]");
});

test("feedback is acknowledged only after native enqueue and delivered once", async () => {
  const received: string[] = [];
  let reject = true;
  const delivery = new FeedbackDelivery({ steer: async (text: string) => { if (reject) throw new Error("busy"); received.push(text); }, followUp: async (text: string) => { received.push(text); } });
  const feedback = [{ id: "a", text: "steer", delivery: "steer" }, { id: "b", text: "later", delivery: "followUp" }];
  await assert.rejects(delivery.deliver(feedback));
  assert.deepEqual(delivery.acknowledgedIds(), []);
  reject = false;
  await delivery.deliver(feedback);
  await delivery.deliver(feedback);
  assert.deepEqual(received, ["steer", "later"]);
  assert.deepEqual(delivery.acknowledgedIds(), ["a", "b"]);
  delivery.confirmAcknowledged(["a"]);
  assert.deepEqual(delivery.acknowledgedIds(), ["b"]);
  await delivery.deliver(feedback);
  assert.deepEqual(received, ["steer", "later"]);
});

test("prompt completion waits for native idle before job completion", async () => {
  let release: (() => void) | undefined;
  const idle = new Promise<void>((resolve) => { release = resolve; });
  let complete = false;
  const running = runUntilSettled({ prompt: async () => {}, waitForIdle: () => idle }, "test").then(() => { complete = true; });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(complete, false);
  release?.();
  await running;
  assert.equal(complete, true);
});
