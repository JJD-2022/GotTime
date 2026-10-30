import test from "node:test";
import assert from "node:assert/strict";
import { monthCells, monthLabel, shiftMonth } from "./activityCalendar.js";

test("calendar starts on Monday and has complete weeks", () => {
  const cells = monthCells("2026-10");
  assert.deepEqual(cells.slice(0, 4), [null, null, null, "2026-10-01"]);
  assert.equal(cells.filter(Boolean).length, 31);
  assert.equal(cells.length, 42);
});
test("every month reserves six rows so navigating never changes calendar height", () => {
  for (const month of ["2026-08", "2026-09", "2026-10", "2027-02", "2028-02"]) {
    assert.equal(monthCells(month).length, 42);
  }
});
test("leap years and year boundaries are handled without local timezone drift", () => {
  assert.equal(monthCells("2028-02").filter(Boolean).length, 29);
  assert.equal(monthCells("2027-02").filter(Boolean).length, 28);
  assert.equal(shiftMonth("2026-12", 1), "2027-01");
  assert.equal(shiftMonth("2026-01", -1), "2025-12");
  assert.equal(monthLabel("2026-10"), "October 2026");
});
