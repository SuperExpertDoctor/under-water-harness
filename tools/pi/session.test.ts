import assert from "node:assert/strict";
import { mkdtemp, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";
import { createAgentSession, DefaultResourceLoader, SessionManager, SettingsManager } from "@earendil-works/pi-coding-agent";
import { TOOL_NAMES } from "./config.ts";
import { missionExtension } from "./extension.ts";
import { Value } from "typebox/value";
import { createMissionResources, readTrustedSkill } from "./resources.ts";
import { fauxAssistantMessage, fauxToolCall } from "@earendil-works/pi-ai";
import { createHarness, getUserTexts } from "../../packages/coding-agent/test/suite/harness.ts";
import { FeedbackDelivery, PublicEventProjector, runUntilSettled, type PublicSessionEvent } from "./bridge.ts";

test("real SDK exposes ten independent strict mission tools and no coding tools", async () => {
  const directory = await mkdtemp(join(tmpdir(), "uuv-sdk-test-"));
  let session;
  try {
    const settingsManager = SettingsManager.inMemory();
    const resourceLoader = new DefaultResourceLoader({ cwd: directory, agentDir: directory, settingsManager,
      noExtensions: true, noSkills: true, noThemes: true, noContextFiles: true, noPromptTemplates: true,
      extensionFactories: [missionExtension(async () => ({ status: "test" }))] });
    await resourceLoader.reload();
    ({ session } = await createAgentSession({ cwd: directory, agentDir: directory, resourceLoader, settingsManager,
      tools: [...TOOL_NAMES], sessionManager: SessionManager.inMemory(directory) }));
    assert.deepEqual(session.getActiveToolNames().sort(), [...TOOL_NAMES].sort());
    assert.ok(!session.getActiveToolNames().includes("bash"));
    assert.equal(session.getActiveToolNames().length, 10);
    const tools = session.agent.state.tools;
    const schema = (name: string) => {
      const found = tools.find((tool) => tool.name === name);
      assert.ok(found);
      assert.equal(found.parameters.additionalProperties, false);
      return found.parameters;
    };
    assert.equal(Value.Check(schema("get_mission_state"), { members: ["UUV-1"] }), false);
    assert.equal(Value.Check(schema("plan_search"), { standing_policy: true }), true);
    assert.equal(Value.Check(schema("plan_search"), { members: Array.from({ length: 8 }, (_, i) => `UUV-${i + 1}`) }), true);
    assert.equal(Value.Check(schema("plan_tracking"), {}), false);
    assert.equal(Value.Check(schema("plan_tracking"), { contact_id: "C-1" }), true);
    assert.equal(Value.Check(schema("plan_tracking"), { contact_id: "C-1", members: ["1", "2", "3", "4"] }), false);
    assert.equal(Value.Check(schema("submit_mission_plan"), { result_id: "r" }), false);
    assert.equal(Value.Check(schema("plan_path"), { members: ["1", "2"], goal: [1, 2, 0] }), false);
    assert.equal(Value.Check(schema("plan_path"), { members: ["1"], goal: [1, 2, 0], algorithm_id: "dubins_hybrid" }), true);
    assert.equal(Value.Check(schema("plan_path"), { members: ["1"], goal: [1, 2, 0], algorithm_id: "strip_coverage" }), false);
  } finally {
    session?.dispose();
    await rm(directory, { recursive: true, force: true });
  }
});

test("native session streams tools and drains steer and followUp before settled", async () => {
  let feedback: FeedbackDelivery | undefined;
  const harness = await createHarness({
    settings: { compaction: { enabled: false }, retry: { enabled: false } },
    initialActiveToolNames: [...TOOL_NAMES], allowedToolNames: [...TOOL_NAMES],
    extensionFactories: [missionExtension(async () => {
      await feedback?.deliver([{ id: "s", text: "operator steering", delivery: "steer" }, { id: "f", text: "operator follow-up", delivery: "followUp" }]);
      return { status: "succeeded", episode_id: "offline" };
    })],
  });
  try {
    feedback = new FeedbackDelivery(harness.session);
    harness.setResponses([
      fauxAssistantMessage(fauxToolCall("get_mission_state", {}, { id: "mission-read" }), { stopReason: "toolUse" }),
      fauxAssistantMessage("steering handled"),
      fauxAssistantMessage("follow-up handled"),
    ]);
    const project = new PublicEventProjector("offline", []);
    const projected: PublicSessionEvent[] = [];
    harness.session.subscribe((event) => { const value = project.project(event); if (value) projected.push(value); });
    await runUntilSettled(harness.session, "inspect state");
    assert.deepEqual(getUserTexts(harness), ["inspect state", "operator steering", "operator follow-up"]);
    assert.equal(harness.session.getLastAssistantText(), "follow-up handled");
    assert.equal(harness.session.isIdle, true);
    assert.equal(harness.session.pendingMessageCount, 0);
    assert.ok(projected.some((event) => event.type === "message_update" && event.text));
    assert.ok(projected.some((event) => event.type === "tool_execution_end" && event.tool_call_id === "mission-read"));
    assert.equal(projected.at(-1)?.type, "agent_settled");
  } finally {
    harness.cleanup();
  }
});

test("native loader discovers only trusted skill and restricted read performs actual file reads", async () => {
  const directory = await mkdtemp(join(tmpdir(), "uuv-trusted-skill-test-"));
  const skillDirectory = resolve(".pi/skills/multi-uuv-recon-tracking");
  let session;
  try {
    const settingsManager = SettingsManager.inMemory();
    const resourceLoader = createMissionResources(directory, skillDirectory, settingsManager, async () => ({}));
    await resourceLoader.reload();
    assert.deepEqual(resourceLoader.getSkills().skills.map((skill) => skill.name), ["multi-uuv-recon-tracking"]);
    ({ session } = await createAgentSession({ cwd: directory, agentDir: directory, resourceLoader, settingsManager, tools: [...TOOL_NAMES, "read"], sessionManager: SessionManager.inMemory(directory) }));
    assert.deepEqual(session.getActiveToolNames().sort(), [...TOOL_NAMES, "read"].sort());
    const tool = session.agent.state.tools.find((entry) => entry.name === "read");
    assert.ok(tool);
    const result = await tool.execute("read-1", { path: join(skillDirectory, "SKILL.md") });
    assert.ok(JSON.stringify(result).includes("Multi-UUV Mission Control"));
    await assert.rejects(tool.execute("read-2", { path: resolve("package.json") }), /untrusted_skill_path/);
    assert.ok((await readTrustedSkill(skillDirectory, "references/tracking.md")).includes("bearing"));
    await assert.rejects(readTrustedSkill(skillDirectory, "../../../../package.json"), /untrusted_skill_path/);
    await assert.rejects(readTrustedSkill(skillDirectory, resolve("package.json")), /untrusted_skill_path/);
    await symlink(resolve("package.json"), join(directory, "SKILL.md"));
    await assert.rejects(readTrustedSkill(directory, "SKILL.md"), /untrusted_skill_path/);
  } finally {
    session?.dispose();
    await rm(directory, { recursive: true, force: true });
  }
});
