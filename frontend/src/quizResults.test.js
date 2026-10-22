import test from "node:test";
import assert from "node:assert/strict";
import { quizOptionState } from "./quizResults.js";

test("a 3/5 result marks three selected answers correct and two incorrect", () => {
  const selected = ["A", "C", "B", "D", "A"];
  const correct = ["A", "A", "B", "C", "A"];
  const states = selected.flatMap((answer, index) =>
    [..."ABCD"].map(letter => quizOptionState(true, answer, correct[index], letter)));
  assert.equal(states.filter(state => state === "correct").length, 3);
  assert.equal(states.filter(state => state === "incorrect").length, 2);
  assert.equal(states.filter(state => state === "answer-key").length, 2);
});

test("wrong selection is distinct from the correct answer and unused options", () => {
  assert.equal(quizOptionState(true, "C", "A", "C"), "incorrect");
  assert.equal(quizOptionState(true, "C", "A", "A"), "answer-key");
  assert.equal(quizOptionState(true, "C", "A", "B"), "");
  assert.equal(quizOptionState(true, "A", "A", "A"), "correct");
});

test("no grading indicators before submission or without an answer key", () => {
  for (const letter of "ABCD") {
    assert.equal(quizOptionState(false, "C", "A", letter), "");
    assert.equal(quizOptionState(true, "C", undefined, letter), "");
  }
});
