import assert from "node:assert/strict";
import test from "node:test";
import { websocketUrl } from "./websocketApi.js";

test("configured websocket origin preserves distinct live and state paths", () => {
  assert.equal(websocketUrl("/ws/live", "wss://localhost:9000/ws/live"), "wss://localhost:9000/ws/live");
  assert.equal(websocketUrl("/ws/state", "wss://localhost:9000/ws/live"), "wss://localhost:9000/ws/state");
});
