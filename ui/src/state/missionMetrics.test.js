import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";

const server = await createServer({ root: fileURLToPath(new URL("../../", import.meta.url)), server: { middlewareMode: true, hmr: false, ws: false }, appType: "custom" });
test.after(() => server.close());

test("mission metrics distinguish unknown rates from zero and label their denominators", async () => {
  const { MetricsTab } = await server.ssrLoadModule("/src/components/BottomDrawer.jsx");
  assert.equal(typeof MetricsTab, "function");
  const html = renderToStaticMarkup(createElement(MetricsTab, { frame: { contacts: [{ contact_id: "c1" }], mission_metrics: {
    unscanned_cells: 1584, recent_coverage_pct: 0, revisit_timeliness_pct: null,
    handoff_attempts: null, handoff_count: 0, handoff_success_rate: null,
  } } }));
  assert.match(html, /<dt>持续区域覆盖率/);
  assert.match(html, /<dd>0%<\/dd>/);
  assert.match(html, /历史记录不完整/);
  assert.doesNotMatch(html, /未扫描积压|重访及时率|累计覆盖/);
  assert.equal((html.match(/<dt>/g) || []).length, 4);
  assert.ok(!html.includes("null"));
});

test("mission metrics retain genuine zero success and round measured rates", async () => {
  const { MetricsTab } = await server.ssrLoadModule("/src/components/BottomDrawer.jsx");
  assert.equal(typeof MetricsTab, "function");
  const html = renderToStaticMarkup(createElement(MetricsTab, { frame: { contacts: [{ contact_id: "c1" }], mission_metrics: {
    handoff_attempts: 1, handoff_count: 0, single_tracking_seconds: 13.6, current_lost_seconds: null,
  } } }));
  assert.match(html, /0 \/ 1/);
  assert.match(html, /13.6 s/);
  assert.match(html, /未失联/);
});
