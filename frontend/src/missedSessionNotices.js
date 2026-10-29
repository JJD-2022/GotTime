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
  return {
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
