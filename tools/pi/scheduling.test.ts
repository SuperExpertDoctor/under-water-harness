import assert from "node:assert/strict";
import { test } from "node:test";
import { RoutineCooldown } from "./scheduling.ts";

test("successful job starts a fifteen-second routine-only cooldown", () => {
  const schedule = new RoutineCooldown();
  assert.deepEqual(schedule.nextRequest(100), { defer_routine: false });
  schedule.completed(100);
  assert.deepEqual(schedule.nextRequest(101), { defer_routine: true });
  assert.deepEqual(schedule.nextRequest(15099), { defer_routine: true });
  assert.deepEqual(schedule.nextRequest(15100), { defer_routine: false });
});

test("polling during cooldown never extends it or blocks an urgent-only poll", () => {
  const schedule = new RoutineCooldown();
  schedule.completed(0);
  for (let now = 1000; now < 15000; now += 1000) assert.deepEqual(schedule.nextRequest(now), { defer_routine: true });
  assert.deepEqual(schedule.nextRequest(15000), { defer_routine: false });
  schedule.completed(20000);
  assert.deepEqual(schedule.nextRequest(34999), { defer_routine: true });
  assert.deepEqual(schedule.nextRequest(35000), { defer_routine: false });
});
