import test from "node:test";
import assert from "node:assert/strict";
import { checkBookingAvailability } from "./bookingAvailability.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 10));

test("availability returns clear or conflicting server results", async () => {
  for (const available of [true, false]) {
    let result;
    const cancel = checkBookingAvailability(async () => ({ available, message: "Session time" }), (value) => { result = value; }, 0);
    await tick();
    assert.deepEqual(result, { available, message: "Session time" });
    cancel();
  }
});

test("cancelled date edits do not make a request", async () => {
  let called = false;
  const cancel = checkBookingAvailability(() => { called = true; }, () => assert.fail("Stale result"), 0);
  cancel();
  await tick();
  assert.equal(called, false);
});

test("late responses cannot overwrite a newer date or time", async () => {
  let resolveOld, signal;
  const cancel = checkBookingAvailability((value) => { signal = value; return new Promise((resolve) => { resolveOld = resolve; }); }, () => assert.fail("Stale result"), 0);
  await tick();
  cancel();
  resolveOld({ available: true });
  await tick();
  assert.equal(signal.aborted, true);
});

test("failed checks block booking instead of reporting a free slot", async () => {
  let result;
  const cancel = checkBookingAvailability(async () => { throw new Error("Connection lost"); }, (value) => { result = value; }, 0);
  await tick();
  assert.deepEqual(result, { available: false, error: true, message: "Connection lost" });
  cancel();
});
