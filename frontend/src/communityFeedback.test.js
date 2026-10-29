import test from "node:test";
import assert from "node:assert/strict";
import { feedbackDraft, clearFeedbackDraft, mergeFeedbackStories } from "./communityFeedback.js";

test("recover only the signed-in user's browser-saved feedback as a draft", () => {
  const storage = { getItem: () => JSON.stringify([{ userId: 2, message: "private draft" }, { userId: 1, message: "my story" }]) };
  assert.equal(feedbackDraft(storage, 1), "my story");
  assert.equal(feedbackDraft(storage, 3), "");
  assert.equal(feedbackDraft({ getItem: () => "broken" }, 1), "");
});

test("successful submissions remove only the matching draft, not other accounts or stories", () => {
  let data = JSON.stringify([{ userId: 1, message: "story" }, { userId: 2, message: "story" }, { userId: 1, message: "another" }]);
  const storage = { getItem: () => data, setItem: (_, value) => { data = value; } };
  clearFeedbackDraft(storage, 1, " story ");
  assert.deepEqual(JSON.parse(data), [{ userId: 2, message: "story" }, { userId: 1, message: "another" }]);
});

test("a late list response preserves a newly submitted story without duplicates", () => {
  assert.deepEqual(mergeFeedbackStories([{ id: 3 }, { id: 2 }], [{ id: 2 }, { id: 1 }]), [{ id: 3 }, { id: 2 }, { id: 1 }]);
});
