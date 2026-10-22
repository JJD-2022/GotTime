export function guardianNotice(user, mode) {
  const limit = mode === "offline" ? 16 : 13;
  if (user.age == null || user.needs_birthdate) return "Add your birthdate before joining a session.";
  return Number(user.age) < limit ? `Because you are under ${limit}, your parent or guardian must be present during this ${mode} session.` : "";
}

export function sessionChatContact(item, userId) {
  if (!["accepted", "scheduled", "awaiting_confirmation", "completed", "missed"].includes(item.status)) return null;
  if (item.mentor_id === userId) return item.learner_id;
  if (item.learner_id === userId) return item.mentor_id;
  return null;
}

export function meetingUrl(raw) {
  try {
    const input = String(raw || "").trim();
    const url = new URL(input.includes("://") ? input : `https://${input}`);
    const hosts = ["meet.google.com", "zoom.us", "teams.microsoft.com", "teams.live.com", "meet.jit.si"];
    if (url.protocol !== "https:" || url.username || url.password) return "";
    return hosts.some((host) => url.hostname === host || url.hostname.endsWith(`.${host}`)) ? url.href : "";
  } catch { return ""; }
}

export function nextLearnerQuiz(data, shown) {
  return data?.sessions?.find((s) => s.learner_id === data.user.id && s.has_quiz && !s.quiz_submitted
    && ["awaiting_confirmation", "completed"].includes(s.status) && !shown.has(`${data.user.id}:${s.id}`));
}
