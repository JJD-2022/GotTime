import test from "node:test";
import assert from "node:assert/strict";
import { birthdayNoticeSeen, rememberBirthdayNotice } from "./birthdayNotice.js";

test("birthday dismissal lasts for one day and is separate for each signed-in user", () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
  assert.equal(birthdayNoticeSeen(storage, 1, "2026-10-30"), false);
  rememberBirthdayNotice(storage, 1, "2026-10-30");
  assert.equal(birthdayNoticeSeen(storage, 1, "2026-10-30"), true);
  assert.equal(birthdayNoticeSeen(storage, 2, "2026-10-30"), false);
  assert.equal(birthdayNoticeSeen(storage, 1, "2026-10-31"), false);
  assert.equal(birthdayNoticeSeen(storage, 1, "2027-10-30"), false);
});

test("blocked browser storage does not crash birthday greetings", () => {
  const storage = { getItem() { throw new Error("Blocked"); }, setItem() { throw new Error("Blocked"); } };
  assert.equal(birthdayNoticeSeen(storage, 1, "2026-10-30"), false);
  assert.doesNotThrow(() => rememberBirthdayNotice(storage, 1, "2026-10-30"));
});
