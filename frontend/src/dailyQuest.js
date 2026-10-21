export function questDay(now = new Date()) {
  // ISO day in IST, independent of the browser's locale or time zone.
  return new Date(now.getTime() + 330 * 60_000).toISOString().slice(0, 10);
}

export function questIsCurrent(quest, now = new Date()) {
  return Boolean(quest?.date && quest.date === questDay(now));
}

export function questCount(data, viewedMentors = []) {
  const quest = data?.dailyQuest;
  if (!quest) return 0;
  if (data.dailyQuestClaimed) return Number(quest.target);
  const count = quest.kind === 'mentor_views'
    ? new Set(viewedMentors).size
    : Number(data.dailyQuestProgress || 0);
  return Math.min(Number(quest.target), Math.max(0, count));
}

export function questResetDelay(quest, now = new Date()) {
  const reset = Date.parse(quest?.resets_at);
  return Number.isFinite(reset) ? Math.max(0, reset - now.getTime()) + 100 : null;
}
