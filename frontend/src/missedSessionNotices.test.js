import test from "node:test";
import assert from "node:assert/strict";
import { createMissedSessionNotices } from "./missedSessionNotices.js";

function storage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
}
const missed = (id) => ({ id, status: "missed" });

test("a shown session stays seen after reopening without dismissing", () => {
  const saved = storage();
  const first = createMissedSessionNotices(saved, storage());
  assert.deepEqual(first.takeUnseen([missed(1)], 7), [missed(1)]);
  assert.deepEqual(first.takeUnseen([missed(1)], 7), []);
  const reopened = createMissedSessionNotices(saved, storage());
  assert.deepEqual(reopened.takeUnseen([missed(1)], 7), []);
  assert.deepEqual(reopened.takeUnseen([missed(1), missed(2)], 7), [missed(2)]);
});

test("accounts have independent notice histories and IDs are normalized", () => {
  const notices = createMissedSessionNotices(storage(), storage());
  assert.equal(notices.takeUnseen([missed(1), missed("1")], 7).length, 1);
  assert.equal(notices.takeUnseen([missed(1)], 8).length, 1);
  assert.deepEqual(notices.takeUnseen([missed("1")], "7"), []);
});

test("existing tab dismissals migrate to persistent storage", () => {
  const saved = storage(), legacy = storage();
  legacy.setItem("gt-missed-popup-dismissed-7", '[1,"2"]');
  assert.deepEqual(createMissedSessionNotices(saved, legacy).takeUnseen([missed(1), missed(2)], 7), []);
  assert.deepEqual(createMissedSessionNotices(saved, storage()).takeUnseen([missed(1), missed(2)], 7), []);
});

test("non-missed sessions are not marked seen before they become missed", () => {
  const notices = createMissedSessionNotices(storage(), storage());
  assert.deepEqual(notices.takeUnseen([{ id: 1, status: "scheduled" }], 7), []);
  assert.deepEqual(notices.takeUnseen([missed(1)], 7), [missed(1)]);
  assert.deepEqual(notices.takeUnseen([missed(2)], null), []);
});

test("malformed or blocked storage cannot crash notices or repeat them during polling", () => {
  for (const value of ['{broken', '{}', 'null']) {
    const saved = storage();
    saved.setItem("gt-missed-popup-dismissed-7", value);
    assert.equal(createMissedSessionNotices(saved).takeUnseen([missed(1)], 7).length, 1);
  }
  const blocked = { getItem() { throw Error("blocked"); }, setItem() { throw Error("blocked"); } };
  const notices = createMissedSessionNotices(blocked, blocked);
  assert.equal(notices.takeUnseen([missed(1)], 7).length, 1);
  assert.deepEqual(notices.takeUnseen([missed(1)], 7), []);
});
