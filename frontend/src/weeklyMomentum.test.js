import test from "node:test";
import assert from "node:assert/strict";
import { weeklyMomentum } from "./weeklyMomentum.js";

test("momentum covers exactly the last seven days across year boundaries", () => {
  const days = weeklyMomentum([], "2027-01-03");
  assert.equal(days.length, 7);
  assert.equal(days[0].date, "2026-12-28");
  assert.equal(days[0].label, "Mon");
  assert.equal(days[6].date, "2027-01-03");
  assert.ok(days.every((day) => day.hours === 0));
});

test("daily bars add completed session durations, ignoring other dates and statuses", () => {
  const session = (scheduled_for, duration, status = "completed") => ({ scheduled_for, duration, status });
  const days = weeklyMomentum([
    session("2026-10-30 18:00:00", 90),
    session("Fri, 30 Oct 2026 23:30:00 GMT", 30),
    session("2026-10-29T10:00:00", undefined),
    session("2026-10-30 19:00:00", 60, "missed"),
    session("2026-10-30 19:00:00", 60, "scheduled"),
    session("2026-10-23 10:00:00", 60),
    session("2026-10-31 10:00:00", 60),
    session(null, 60), session("invalid", 60),
  ], "2026-10-30");
  assert.equal(days[6].hours, 2);
  assert.equal(days[5].hours, 1);
  assert.equal(days.reduce((sum, day) => sum + day.hours, 0), 3);
});
