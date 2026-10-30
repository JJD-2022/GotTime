export function refreshProfileStats(profile, data) {
  if (!profile) return profile;
  const sources = data.user?.id === profile.id ? [data.user] : [
    data.mentors?.find((person) => person.id === profile.id),
    data.leaderboard?.find((person) => person.id === profile.id),
  ];
  const refreshed = { ...profile };
  // Keep the selected skill/banner, but don't freeze the impact snapshot.
  for (const key of ["rating", "rating_count", "review_count", "sessions_completed", "credits", "streak"]) {
    const source = sources.find((person) => person?.[key] != null);
    if (source) refreshed[key] = source[key];
  }
  return refreshed;
}

export function formatRating(value, empty = "-") {
  const rating = Number(value);
  return Number.isFinite(rating) && rating > 0 ? rating.toFixed(1) : empty;
}
