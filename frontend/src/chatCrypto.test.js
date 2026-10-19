import test from "node:test";
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { createChatCrypto } from "./chatCrypto.js";

function browser(storage = new Map(), locks) {
  return {
    storage,
    ...createChatCrypto({ crypto: webcrypto, locks, storage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
    } }),
  };
}
function saveCopy(message, recoveryCopy) {
  const copy = structuredClone(message);
  const key = recoveryCopy.receiverPublicKey;
  copy.encrypted_content.recoveryCopies ??= {};
  copy.encrypted_content.recoveryCopies[`${recoveryCopy.recipientId}:${key.x}.${key.y}`] = recoveryCopy;
  return copy;
}

test("concurrent first use retains one key pair", async () => {
  const device = browser();
  const keys = await Promise.all(Array.from({ length: 20 }, () => device.getMessageKeys(1)));
  assert.equal(new Set(keys.map((key) => key.publicJwk.x)).size, 1);
  assert.deepEqual((await device.getMessageKeys(1)).publicJwk, keys[0].publicJwk);
});

test("Web Lock serializes first use across separate tabs", async () => {
  let tail = Promise.resolve();
  const locks = { request: (_name, callback) => { const next = tail.then(callback); tail = next.catch(() => {}); return next; } };
  const storage = new Map();
  const [one, two] = await Promise.all([browser(storage, locks).getMessageKeys(1), browser(storage, locks).getMessageKeys(1)]);
  assert.deepEqual(one.publicJwk, two.publicJwk);
});

test("learner recovers both directions of old chat for mentor, preserving originals", async () => {
  const learner = browser(), oldMentor = browser(), newMentor = browser();
  const learnerKey = (await learner.getMessageKeys(1)).publicJwk;
  const oldMentorKey = (await oldMentor.getMessageKeys(2)).publicJwk;
  const newMentorKey = (await newMentor.getMessageKeys(2)).publicJwk;
  for (const senderId of [1, 2]) {
    const text = senderId === 1 ? "I'd like to book a session" : "Here is the meeting link";
    const sender = senderId === 1 ? learner : oldMentor;
    const message = { encrypted_content: await sender.buildEncryptedMessage({ senderId, receiverId: senderId === 1 ? 2 : 1, receiverPublicKey: senderId === 1 ? oldMentorKey : learnerKey, content: text }) };
    const original = structuredClone(message.encrypted_content);
    assert.equal(await learner.decryptMessage(message, 1), text);
    await assert.rejects(newMentor.decryptMessage(message, 2));
    const recovery = await learner.buildRecoveryCopy(message, 1, 2, newMentorKey);
    assert.ok(recovery);
    assert.ok(!JSON.stringify(recovery).includes(text));
    const repaired = saveCopy(message, recovery);
    assert.deepEqual(repaired.encrypted_content.copies, original.copies);
    assert.equal(await newMentor.decryptMessage(repaired, 2), text);
    assert.equal(await oldMentor.decryptMessage(repaired, 2), text);
    assert.equal(await learner.decryptMessage(repaired, "1"), text);
    assert.equal(await learner.buildRecoveryCopy(repaired, 1, 2, newMentorKey), null);
  }
  const fresh = { encrypted_content: await newMentor.buildEncryptedMessage({ senderId: 2, receiverId: 1, receiverPublicKey: learnerKey, content: "hello" }) };
  assert.equal(await learner.decryptMessage(fresh, 1), "hello");
  assert.equal(await newMentor.decryptMessage(fresh, 2), "hello");
});

test("unreadable data cannot be recovered as placeholder text or generate keys", async () => {
  const learner = browser(), mentor = browser(), empty = browser();
  const mentorKey = (await mentor.getMessageKeys(2)).publicJwk;
  const message = { encrypted_content: await learner.buildEncryptedMessage({ senderId: 1, receiverId: 2, receiverPublicKey: mentorKey, content: "private" }) };
  const replacementKey = (await browser().getMessageKeys(2)).publicJwk;
  await assert.rejects(empty.buildRecoveryCopy(message, 1, 2, replacementKey));
  assert.equal(empty.storage.size, 0);
});

test("corrupt and incomplete storage are not silently replaced", async () => {
  for (const saved of ["not-json", JSON.stringify({ x: "wrong" })]) {
    const storage = new Map([["gt-e2ee-private-1", saved]]);
    const device = browser(storage);
    await assert.rejects(device.getMessageKeys(1));
    assert.equal(storage.get("gt-e2ee-private-1"), saved);
  }
});
