const key = (userId) => `gt-birthday-notice:${userId}`;

export function birthdayNoticeSeen(storage, userId, day) {
  try { return storage.getItem(key(userId)) === day; } catch { return false; }
}

export function rememberBirthdayNotice(storage, userId, day) {
  try { storage.setItem(key(userId), day); } catch { /* In-memory dismissal still works. */ }
}
