import { questDay } from "./dailyQuest.js";

export function weeklyMomentum(sessions, today = questDay()) {
  const end = new Date(`${today}T00:00:00Z`);
  const days = Array.from({ length: 7 }, (_, index) => {
    const date = new Date(end.getTime() - (6 - index) * 86400000);
    return { date: date.toISOString().slice(0, 10), label: date.toLocaleDateString("en", { weekday: "short", timeZone: "UTC" }), hours: 0 };
  });
  const byDate = new Map(days.map((day) => [day.date, day]));
  for (const session of sessions || []) {
    if (session.status !== "completed" || !session.scheduled_for) continue;
    const value = String(session.scheduled_for);
    // SQL/ISO timestamps and Flask's RFC date serialization carry the scheduled day.
    const parsed = new Date(value);
    const key = /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10)
      : Number.isNaN(parsed.valueOf()) ? null : parsed.toISOString().slice(0, 10);
    const minutes = Number(session.duration ?? 60);
    if (byDate.has(key) && Number.isFinite(minutes) && minutes > 0) byDate.get(key).hours += minutes / 60;
  }
  return days;
}
