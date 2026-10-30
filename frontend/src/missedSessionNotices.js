function storedIds(storage, key) {
  try {
    const value = JSON.parse(storage?.getItem(key) || "[]");
    return Array.isArray(value) ? value.map(String) : [];
  } catch {
    return [];
  }
}

export function createMissedSessionNotices(storage, legacyStorage) {
  const memory = new Map();
  const confirmed = new Map();
  const pending = new Set();
  return {
    async claimUnseen(sessions, userId, claim) {
      if (userId == null) return [];
      const key = `gt-missed-popup-dismissed-${userId}`;
      if (pending.has(key)) return [];
      const synced = confirmed.get(key) || new Set();
      const candidates = [...new Map((sessions || [])
        .filter((item) => item.status === "missed" && Number.isSafeInteger(Number(item.id)) && Number(item.id) > 0 && !synced.has(String(item.id)))
        .map((item) => [String(item.id), item])).values()].slice(0, 500);
      if (!candidates.length) return [];
      const seen = new Set([...(memory.get(key) || []), ...storedIds(storage, key), ...storedIds(legacyStorage, key)]);
      pending.add(key);
      try {
        // Only the server can authorize displaying a notice, across all devices.
        const response = await claim({
          session_ids: candidates.map((item) => Number(item.id)),
          previously_seen: candidates.filter((item) => seen.has(String(item.id))).map((item) => Number(item.id)),
        });
        if (!Array.isArray(response.session_ids)) throw new Error("Could not confirm missed-session notices.");
        candidates.forEach((item) => synced.add(String(item.id)));
        confirmed.set(key, synced);
        const allowed = new Set(response.session_ids.map(String));
        return this.takeUnseen(candidates.filter((item) => allowed.has(String(item.id))), userId);
      } finally { pending.delete(key); }
    },
    takeUnseen(sessions, userId) {
      if (userId == null) return [];
      const key = `gt-missed-popup-dismissed-${userId}`;
      const seen = new Set([
        ...(memory.get(key) || []),
        ...storedIds(storage, key),
        ...storedIds(legacyStorage, key),
      ]);
      const unseen = [];
      for (const item of sessions || []) {
        if (item.status !== "missed" || item.id == null || seen.has(String(item.id))) continue;
        unseen.push(item);
        seen.add(String(item.id));
      }
      // Remember on display, not on dismissal: reopening must not repeat it.
      // Memory also prevents repeat polling notices if browser storage is blocked.
      memory.set(key, seen);
      try { storage?.setItem(key, JSON.stringify([...seen])); } catch { /* Storage may be unavailable. */ }
      return unseen;
    },
  };
}
