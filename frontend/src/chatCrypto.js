// Keys stay in the browser. Recovery adds a new encrypted copy without removing
// the original, so devices which can already read a message keep that access.
export function createChatCrypto({ crypto, storage, locks }) {
  const algorithm = { name: "ECDH", namedCurve: "P-256" };
  const pendingKeys = new Map();
  const toBase64 = (bytes) => btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(""));
  const fromBase64 = (text) => Uint8Array.from(atob(text), (char) => char.charCodeAt(0));
  const parsePayload = (message) => typeof message.encrypted_content === "string" ? JSON.parse(message.encrypted_content) : message.encrypted_content;
  const sameKey = (a, b) => Boolean(a && b && a.x === b.x && a.y === b.y && a.crv === b.crv);
  const copyId = (userId, key) => `${userId}:${key.x}.${key.y}`;

  async function readKeys(userId) {
    const saved = storage.getItem(`gt-e2ee-private-${userId}`);
    const savedPublic = storage.getItem(`gt-e2ee-public-${userId}`);
    if (!saved || !savedPublic) return null;
    const jwk = JSON.parse(saved);
    const publicJwk = JSON.parse(savedPublic);
    if (!sameKey(jwk, publicJwk)) throw new Error("Message keys do not match. Keep this browser's data for recovery.");
    const privateKey = await crypto.subtle.importKey("jwk", jwk, algorithm, true, ["deriveKey"]);
    return { privateKey, publicJwk };
  }

  async function getMessageKeys(userId) {
    const id = String(userId);
    if (pendingKeys.has(id)) return pendingKeys.get(id);
    const initialize = async () => {
      const existing = await readKeys(id);
      if (existing) return existing;
      // Do not overwrite partial/corrupt key material: it may still be recoverable.
      if (storage.getItem(`gt-e2ee-private-${id}`) || storage.getItem(`gt-e2ee-public-${id}`)) {
        throw new Error("Message key storage is incomplete. Do not clear browser data.");
      }
      const pair = await crypto.subtle.generateKey(algorithm, true, ["deriveKey"]);
      const privateJwk = await crypto.subtle.exportKey("jwk", pair.privateKey);
      const publicJwk = await crypto.subtle.exportKey("jwk", pair.publicKey);
      storage.setItem(`gt-e2ee-private-${id}`, JSON.stringify(privateJwk));
      storage.setItem(`gt-e2ee-public-${id}`, JSON.stringify(publicJwk));
      return { privateKey: pair.privateKey, publicJwk };
    };
    // Single-flight within a tab; Web Locks serialize initialization across tabs.
    const promise = locks ? locks.request(`gt-message-keys-${id}`, initialize) : initialize();
    pendingKeys.set(id, promise);
    try { return await promise; }
    finally { if (pendingKeys.get(id) === promise) pendingKeys.delete(id); }
  }

  async function deriveKey(privateKey, publicJwk) {
    const publicKey = await crypto.subtle.importKey("jwk", publicJwk, algorithm, false, []);
    return crypto.subtle.deriveKey({ name: "ECDH", public: publicKey }, privateKey, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  async function encryptFor(publicJwk, privateKey, text) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const key = await deriveKey(privateKey, publicJwk);
    const data = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(text));
    return { iv: toBase64(iv), data: toBase64(new Uint8Array(data)) };
  }
  async function decryptCopy(copy, privateKey, publicJwk) {
    const key = await deriveKey(privateKey, publicJwk);
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: fromBase64(copy.iv) }, key, fromBase64(copy.data));
    return new TextDecoder().decode(plain);
  }
  async function decryptMessage(message, userId) {
    if (!message.encrypted_content) return message.content;
    const payload = parsePayload(message);
    // Reading a message must never create or replace a key.
    const keys = await readKeys(userId);
    if (!keys) throw new Error("Original message key is unavailable on this browser.");
    const copy = payload?.copies?.[userId];
    if (copy) {
      const otherPublic = String(payload.sender) === String(userId) ? payload.receiverPublicKey : payload.senderPublicKey;
      try { return await decryptCopy(copy, keys.privateKey, otherPublic); }
      catch { /* Try an additive recovery copy for this browser's current key. */ }
    }
    const recovered = payload?.recoveryCopies?.[copyId(userId, keys.publicJwk)];
    if (recovered && sameKey(recovered.receiverPublicKey, keys.publicJwk)) {
      return decryptCopy(recovered, keys.privateKey, recovered.senderPublicKey);
    }
    throw new Error("Message needs recovery from a device that can read it.");
  }
  async function buildEncryptedMessage({ senderId, receiverId, receiverPublicKey, content }) {
    if (!crypto?.subtle || !receiverPublicKey) return null;
    if (typeof receiverPublicKey === "string") receiverPublicKey = JSON.parse(receiverPublicKey);
    const { privateKey, publicJwk } = await getMessageKeys(senderId);
    return {
      v: 1, alg: "ECDH-P256/AES-GCM", sender: senderId, receiver: receiverId,
      senderPublicKey: publicJwk, receiverPublicKey,
      copies: {
        [senderId]: await encryptFor(receiverPublicKey, privateKey, content),
        [receiverId]: await encryptFor(receiverPublicKey, privateKey, content),
      },
    };
  }
  async function buildRecoveryCopy(message, userId, contactId, contactPublicKey) {
    if (!message.encrypted_content || !contactPublicKey) return null;
    if (typeof contactPublicKey === "string") contactPublicKey = JSON.parse(contactPublicKey);
    const payload = parsePayload(message);
    const originalPeerKey = String(payload.sender) === String(contactId) ? payload.senderPublicKey : payload.receiverPublicKey;
    if (sameKey(originalPeerKey, contactPublicKey) || payload.recoveryCopies?.[copyId(contactId, contactPublicKey)]) return null;
    // Throws on failure: never encrypt a placeholder as if it were message text.
    const text = await decryptMessage(message, userId);
    const { privateKey, publicJwk } = await getMessageKeys(userId);
    return {
      recipientId: contactId, senderPublicKey: publicJwk, receiverPublicKey: contactPublicKey,
      ...await encryptFor(contactPublicKey, privateKey, text),
    };
  }
  return { getMessageKeys, decryptMessage, buildEncryptedMessage, buildRecoveryCopy };
}
