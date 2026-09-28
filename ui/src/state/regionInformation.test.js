import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const server = await createServer({ root: fileURLToPath(new URL("../../", import.meta.url)), server: { middlewareMode: true, hmr: false, ws: false }, appType: "custom" });
test.after(() => server.close());

test("region table derives independent information means from authoritative cell fields", async () => {
  const { RegionTab } = await server.ssrLoadModule("/src/components/BottomDrawer.jsx");
  assert.equal(typeof RegionTab, "function");
  const frame = { info_matrix: [[1, .5]], target_info_matrix: [[.8, 0]],
    search_regions: [{ id: "r", cells: [[0, 0], [0, 1]], avg_info: 0, info_value: 0 }] };
  const html = renderToStaticMarkup(createElement(RegionTab, { frame }));
  for (const label of ["平均新鲜度", "平均线索", "75.0%", "40.0%"])
    assert.ok(html.includes(label), label);
  const missing = renderToStaticMarkup(createElement(RegionTab, { frame: { search_regions: frame.search_regions } }));
  assert.ok(missing.includes("--"));
});
