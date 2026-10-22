export function quizOptionState(attempted, selected, correct, letter) {
  if (!attempted || !["A", "B", "C", "D"].includes(correct)) return "";
  if (selected === letter) return selected === correct ? "correct" : "incorrect";
  return letter === correct ? "answer-key" : "";
}
