export function feedbackDraft(storage, userId) {
  try {
    const items = JSON.parse(storage.getItem("gt-feedback") || "[]");
    if (!Array.isArray(items)) return "";
    const item = items.find((entry) => entry && String(entry.userId) === String(userId) && typeof entry.message === "string");
    return item?.message || "";
  } catch { return ""; }
}

export function clearFeedbackDraft(storage, userId, message) {
  try {
    const items = JSON.parse(storage.getItem("gt-feedback") || "[]");
    if (!Array.isArray(items)) return;
    storage.setItem("gt-feedback", JSON.stringify(items.filter((entry) =>
      !entry || String(entry.userId) !== String(userId) || entry.message?.trim() !== message.trim())));
  } catch { /* A saved server submission must not fail because browser storage is unavailable. */ }
}

export function mergeFeedbackStories(current, incoming) {
  const stories = new Map(current.map((story) => [story.id, story]));
  for (const story of incoming) stories.set(story.id, story);
  return [...stories.values()].sort((a, b) => b.id - a.id).slice(0, 50);
}
