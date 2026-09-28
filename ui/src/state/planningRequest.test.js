import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const server = await createServer({ root: fileURLToPath(new URL("../../", import.meta.url)), server: { middlewareMode: true, hmr: false, ws: false }, appType: "custom" });
test.after(() => server.close());

test("automatic coverage omits local bounds and members while explicit coverage retains both", async () => {
  const { planningRequest } = await server.ssrLoadModule("/src/components/AgentPanel.jsx");
  assert.equal(typeof planningRequest, "function");
  const bbox = [250, 250, 3750, 3750];
  assert.deepEqual(planningRequest("plan_search", true, ["UUV-1"], bbox, ""), { tool: "plan_search", standing_policy: true });
  assert.deepEqual(planningRequest("plan_search", false, ["UUV-1"], bbox, ""),
    { tool: "plan_search", members: ["UUV-1"], standing_policy: false, bbox });
  assert.deepEqual(planningRequest("plan_tracking", true, [], bbox, "CONTACT-1"),
    { tool: "plan_tracking", contact_id: "CONTACT-1" });
});

test("automatic whole-area planning does not expose ignored local boundary inputs", async () => {
  const { default: AgentPanel } = await server.ssrLoadModule("/src/components/AgentPanel.jsx");
  const html = renderToStaticMarkup(createElement(AgentPanel, { tab: "planning", frame: { episode_id: "e", uavs: [] },
    mission: { ready: true, state: {}, tasks: [], skills: [] }, selection: [1, 1, 2, 2] }));
  assert.ok(!html.includes("搜索边界"));
  assert.ok(!html.includes("采用地图框选"));
});
