import assert from "node:assert/strict";
import { mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { Value } from "typebox/value";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import { fauxAssistantMessage, fauxToolCall } from "@earendil-works/pi-ai";
import { createHarness } from "../../packages/coding-agent/test/suite/harness.ts";
import { AdversaryBudget, ADVERSARY_TOOLS, createAdversarySession, pruneAdversarySessions } from "./adversary.ts";

test("native adversary session has only two restrictive tools and no filesystem or friendly tools", async () => {
  const directory = await mkdtemp(join(tmpdir(), "uuv-enemy-test-"));
  let session;
  try {
    session = await createAdversarySession(directory, async () => ({}), new AdversaryBudget(), { sessionManager: SessionManager.inMemory(directory) });
    assert.deepEqual(session.getActiveToolNames().sort(), [...ADVERSARY_TOOLS].sort());
    const tools = session.agent.state.tools;
    const observation = tools.find((tool) => tool.name === "get_adversary_observation");
    const parameters = tools.find((tool) => tool.name === "set_evasion_parameters");
    assert.ok(observation && parameters);
    assert.equal(Value.Check(observation.parameters, {}), true);
    assert.equal(Value.Check(observation.parameters, { truth: true }), false);
    const valid = { speed_mps: 2.5, turn_bias: 0, duration_s: 30, reason: "Move away from detected sonar" };
    assert.equal(Value.Check(parameters.parameters, valid), true);
    for (const invalid of [{ ...valid, target_position: [1, 2] }, { ...valid, speed_mps: 5 }, { ...valid, duration_s: 61 }, { ...valid, turn_bias: -2 }, { speed_mps: 2.5, turn_bias: 0, duration_s: 30 }]) {
      assert.equal(Value.Check(parameters.parameters, invalid), false);
    }
    for (let index = 0; index < 4; index++) await observation.execute(`call-${index}`, {});
    await assert.rejects(observation.execute("overflow", {}), /tool_budget_exceeded/);
  } finally {
    session?.dispose();
    await rm(directory, { recursive: true, force: true });
  }
});

test("adversary budget bounds provider turns independently from tool calls", () => {
  const budget = new AdversaryBudget();
  for (let index = 0; index < 3; index++) budget.provider();
  assert.throws(() => budget.provider(), /model_budget_exceeded/);
});

test("native adversary loop stops before a fourth faux-provider request", async () => {
  const harness = await createHarness();
  let session;
  let observations = 0;
  try {
    session = await createAdversarySession(harness.tempDir, async () => {
      observations++;
      return { episode_id: "test", own_pose: [1000, 1000, 0], detections: [], history: [] };
    }, new AdversaryBudget(), { modelRuntime: harness.session.modelRuntime, model: harness.getModel(), sessionManager: SessionManager.inMemory(harness.tempDir) });
    harness.setResponses(Array.from({ length: 4 }, (_, index) => fauxAssistantMessage(
      fauxToolCall("get_adversary_observation", {}, { id: `observation-${index}` }), { stopReason: "toolUse" })));
    await session.prompt("Read noisy observations only.");
    assert.equal(observations, 3);
    assert.equal(harness.getPendingResponseCount(), 1);
    const last = session.messages.at(-1);
    assert.ok(last?.role === "assistant");
    assert.equal(last.stopReason, "error");
    assert.match(last.errorMessage || "", /adversary_model_budget_exceeded/);
  } finally {
    session?.dispose();
    harness.cleanup();
  }
});

test("private retention bounds completed logs across episodes without removing the active log or config", async () => {
  const directory = await mkdtemp(join(tmpdir(), "uuv-enemy-retention-"));
  try {
    const first = join(directory, "mission-first");
    const second = join(directory, "mission-second");
    await mkdir(first);
    await mkdir(second);
    const active = join(first, "active.jsonl");
    await writeFile(active, "active");
    await writeFile(join(first, "keep.json"), "{}");
    for (let index = 0; index < 105; index++) {
      await writeFile(join(index < 50 ? first : second, `${index}.jsonl`), "session");
    }
    await pruneAdversarySessions(directory, active);
    const remaining = [...await readdir(first), ...await readdir(second)];
    assert.equal(remaining.filter((name) => name.endsWith(".jsonl")).length, 101);
    assert.ok(remaining.includes("active.jsonl"));
    assert.ok(remaining.includes("keep.json"));
    await pruneAdversarySessions(directory);
    assert.equal([...await readdir(first), ...await readdir(second)].filter((name) => name.endsWith(".jsonl")).length, 100);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
