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
  const html = renderToStaticMarkup(createElement(MetricsTab, { frame: { mission_metrics: {
    unscanned_cells: 1584, recent_coverage_pct: 0, revisit_timeliness_pct: null,
    handoff_attempts: null, handoff_count: 0, handoff_success_rate: null,
  } } }));
  assert.match(html, /<dt>未扫描积压 \/ 格<\/dt><dd>1584<\/dd>/);
  assert.match(html, /<dt>近30分钟覆盖率 \/ %<\/dt><dd>0<\/dd>/);
  assert.match(html, /<dt>30分钟重访及时率 \/ %<\/dt><dd>无数据<\/dd>/);
  assert.match(html, /<dt>已发起接替<\/dt><dd>无数据<\/dd>/);
  assert.match(html, /<dt>接替成功率 \/ %<\/dt><dd>无数据<\/dd>/);
  assert.ok(!html.includes("null"));
});

test("mission metrics retain genuine zero success and round measured rates", async () => {
  const { MetricsTab } = await server.ssrLoadModule("/src/components/BottomDrawer.jsx");
  assert.equal(typeof MetricsTab, "function");
  const html = renderToStaticMarkup(createElement(MetricsTab, { frame: { mission_metrics: {
    handoff_attempts: 1, handoff_success_rate: 0, revisit_timeliness_pct: 100/3,
  } } }));
  assert.match(html, /<dt>接替成功率 \/ %<\/dt><dd>0<\/dd>/);
  assert.match(html, /<dt>30分钟重访及时率 \/ %<\/dt><dd>33.33<\/dd>/);
});
