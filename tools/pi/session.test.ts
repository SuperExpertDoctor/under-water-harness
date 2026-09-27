import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { createAgentSession, DefaultResourceLoader, SessionManager, SettingsManager } from "@earendil-works/pi-coding-agent";
import { TOOL_NAMES } from "./config.ts";
import { missionExtension } from "./extension.ts";

test("real SDK exposes exactly nine extension tools and no coding tools", async () => {
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
  } finally {
    session?.dispose();
    await rm(directory, { recursive: true, force: true });
  }
});
