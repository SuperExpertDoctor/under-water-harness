import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";

const server = await createServer({ root: fileURLToPath(new URL("../../", import.meta.url)), server: { middlewareMode: true, hmr: false, ws: false }, appType: "custom" });
test.after(() => server.close());

test("permission selector names active approval level and scopes full access to mission plans", async () => {
  const { default: MissionControl } = await server.ssrLoadModule("/src/view/components/MissionControl.jsx");
  const html = renderToStaticMarkup(createElement(MissionControl, {
    mission: { ready: true, busy: false, state: { autonomy_mode: "assisted", agent: { status: "idle" } } },
    frame: { runtime_status: "ready" }, readOnly: false,
  }));
  assert.match(html, /审批模式/);
  assert.match(html, /帮我批准/);
  assert.match(html, /aria-expanded="false"/);
  assert.doesNotMatch(html, /无限制访问电脑|自动批准文件修改/);
});
