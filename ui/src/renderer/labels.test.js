import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const server = await createServer({ root: fileURLToPath(new URL("../../", import.meta.url)), server: { middlewareMode: true, hmr: false, ws: false }, appType: "custom" });
test.after(() => server.close());

function context() {
  const drawn = [];
  return { drawn, save() {}, restore() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, strokeRect() {}, fillRect() {}, setLineDash() {},
    measureText: (value) => ({ width: [...value].length * 6 }),
    fillText(value, x, y) { drawn.push({ value, x, y: y - 9, width: [...value].length * 6, height: 12 }); } };
}

function overlaps(a, b) {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

test("mobile region and entity labels share nonoverlapping space outside the task header", async () => {
  const { drawSearchRegions, drawLabels } = await server.ssrLoadModule("/src/renderer/layers.js");
  const frame = {
    uavs: Array.from({ length: 8 }, (_, i) => ({ id: `UUV-${i + 1}`, position: [i * 2 + 2, 3 + i], status: "searching" })),
    search_regions: Array.from({ length: 8 }, (_, i) => ({ id: `region-${i}`, assigned_uav_id: `UUV-${i + 1}`, cells: [[i * 5, 0]] })),
    contacts: [{ contact_id: "CONTACT-1", estimated_position: [35, 17], state: "tracking" }],
  };
  const ctx = context();
  drawSearchRegions(ctx, frame.search_regions, 5, 170, 20);
  const placed = drawLabels(ctx, frame, 5, 170, 20, { x: 0, y: 0, width: 370, height: 220 }, null, "CONTACT-1", null, []);
  assert.ok(placed.some((label) => label.id === "contact:CONTACT-1" && !label.hidden));
  const header = { x: 175, y: 25, width: 158, height: 15 };
  for (const [index, label] of ctx.drawn.entries()) {
    assert.ok(!overlaps(label, header), `${label.value} obscures task-area header`);
    for (const other of ctx.drawn.slice(index + 1)) {
      assert.ok(!overlaps(label, other), `${label.value} overlaps ${other.value}`);
    }
  }
  const second = context();
  drawSearchRegions(second, frame.search_regions, 5, 170, 20);
  drawLabels(second, frame, 5, 170, 20, { x: 0, y: 0, width: 370, height: 220 }, null, "CONTACT-1", null, []);
  assert.deepEqual(second.drawn, ctx.drawn);
});

test("tracking contact label states tracking rather than classification pending", async () => {
  const { drawLabels } = await server.ssrLoadModule("/src/renderer/layers.js");
  const ctx = context();
  drawLabels(ctx, { contacts: [{ contact_id: "CONTACT-1", estimated_position: [10, 10], state: "tracking" }] },
    10, 0, 0, { x: 0, y: 0, width: 400, height: 400 }, null, "CONTACT-1", null, []);
  assert.ok(ctx.drawn.some((label) => label.value === "CONTACT-1 持续跟踪"));
});

for (const cellSize of [5, 12, 40]) {
  test(`labels avoid later team markers at ${cellSize}px cells`, async () => {
    const { drawLabels } = await server.ssrLoadModule("/src/renderer/layers.js");
    const position = (x, y) => [x / cellSize - .5, y / cellSize - .5];
    const frame = {
      uavs: [
        { id: "UUV-5", position: position(100, 110), status: "tracking" },
        { id: "UUV-6", position: position(128, 93), status: "tracking" },
      ],
      teams: [{ members: ["UUV-5", "UUV-6"] }],
    };
    const bounds = { x: 0, y: 0, width: cellSize === 5 ? 370 : 850, height: 300 };
    const placed = drawLabels(context(), frame, cellSize, 0, 0, bounds, "UUV-5", null, null, []);
    assert.ok(placed.some((label) => label.id === "uav:UUV-5" && !label.hidden));
    for (const boat of frame.uavs) {
      const radius = Math.max(8, cellSize * .6) + 1;
      const marker = { x: (boat.position[0] + .5) * cellSize - radius,
        y: (boat.position[1] + .5) * cellSize - radius, width: radius * 2, height: radius * 2 };
      for (const label of placed.filter((item) => !item.hidden)) {
        assert.ok(!overlaps(label, marker), `${label.id} overlaps ${boat.id} team marker`);
      }
    }
  });
}

test("labels reserve contact, scenario, and base markers before priority placement", async () => {
  const { drawLabels } = await server.ssrLoadModule("/src/renderer/layers.js");
  for (const kind of ["contact", "scenario", "base"]) {
    const frame = { uavs: [{ id: "UUV-1", position: [9.5, 10.5], status: "tracking" }] };
    if (kind === "contact") frame.contacts = [{ contact_id: "CONTACT-1", estimated_position: [12.3, 8.8], state: "confirmed" }];
    if (kind === "scenario") frame.scenario_vessels = [{ scenario_entity_id: "VESSEL-1", position: [12.3, 8.8] }];
    if (kind === "base") frame.bases = [{ id: "BASE-1" }];
    const placed = drawLabels(context(), frame, 10, 0, 0, { x: 0, y: 0, width: 500, height: 300 },
      "UUV-1", null, null, kind === "base" ? [{ x: 128, y: 93 }] : [], true);
    const selected = placed.find((label) => label.id === "uav:UUV-1");
    assert.ok(!selected.hidden);
    assert.ok(!overlaps(selected, { x: 120, y: 85, width: 16, height: 16 }), `Selected label overlaps ${kind} marker`);
  }
});
