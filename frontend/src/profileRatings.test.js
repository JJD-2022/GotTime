import test from "node:test";
import assert from "node:assert/strict";
import { refreshProfileStats, formatRating } from "./profileRatings.js";

test("rating and written-review counts refresh with the average, including zero", () => {
  const result = refreshProfileStats({ id: 2, rating: 5, rating_count: 1, review_count: 1 },
    { mentors: [{ id: 2, rating: 4, rating_count: 2, review_count: 0 }] });
  assert.equal(result.rating, 4);
  assert.equal(result.rating_count, 2);
  assert.equal(result.review_count, 0);
});

test("own profile gets its freshly returned average", () => {
  assert.equal(refreshProfileStats({ id: 1, rating: 0 }, { user: { id: 1, rating: "4.5" } }).rating, "4.5");
});

test("open mentor profile refreshes without replacing its selected skill", () => {
  const selected = { id: 2, skill_id: 9, primary_skill: "Singing", rating: 0 };
  const data = { user: { id: 1 }, mentors: [{ id: 2, skill_id: 8, primary_skill: "Guitar", rating: "4.0" }] };
  const refreshed = refreshProfileStats(selected, data);
  assert.equal(refreshed.rating, "4.0");
  assert.equal(refreshed.skill_id, 9);
  assert.equal(refreshed.primary_skill, "Singing");
  assert.equal(selected.rating, 0);
});

test("leaderboard profiles refresh and unrelated profiles keep their rating", () => {
  const data = { user: { id: 1 }, leaderboard: [{ id: 2, rating: "3.5" }] };
  assert.equal(refreshProfileStats({ id: 2, rating: 0 }, data).rating, "3.5");
  assert.equal(refreshProfileStats({ id: 3, rating: 5 }, data).rating, 5);
  assert.equal(refreshProfileStats(null, data), null);
});

test("ratings format consistently while unrated users keep the placeholder", () => {
  assert.equal(formatRating("4.5000"), "4.5");
  assert.equal(formatRating(5), "5.0");
  for (const value of [0, null, undefined, "invalid"]) assert.equal(formatRating(value), "-");
  assert.equal(formatRating(0, "New"), "New");
});

test("impact statistics refresh together without inventing missing values", () => {
  const selected = { id: 2, credits: 10, sessions_completed: 0, streak: 0, rating: 0 };
  const result = refreshProfileStats(selected, {
    mentors: [{ id: 2, rating: "4.5", sessions_completed: 3 }],
    leaderboard: [{ id: 2, credits: 12, sessions_completed: 3, streak: 0, rating: "4.5" }],
  });
  assert.deepEqual(result, { id: 2, credits: 12, sessions_completed: 3, streak: 0, rating: "4.5" });
  assert.equal(refreshProfileStats(selected, { mentors: [{ id: 2, rating: 4 }] }).credits, 10);
});
