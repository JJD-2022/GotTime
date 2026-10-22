import test from "node:test";
import assert from "node:assert/strict";
import { createCheckoutFeedback } from "./checkoutFeedback.js";

test("dismissal reports once without claiming that no money was charged", () => {
  const messages = [];
  const feedback = createCheckoutFeedback(message => messages.push(message));
  assert.equal(feedback.modal.confirm_close, true);
  feedback.modal.ondismiss();
  feedback.modal.ondismiss();
  assert.equal(messages.length, 1);
  assert.match(messages[0], /If money was debited, do not pay again/);
});

test("successful callback suppresses cancellation feedback during verification", () => {
  const messages = [];
  const feedback = createCheckoutFeedback(message => messages.push(message));
  feedback.paymentReceived();
  feedback.modal.ondismiss();
  assert.deepEqual(messages, []);
});

test("each checkout has independent dismissal state", () => {
  const messages = [];
  createCheckoutFeedback(message => messages.push(message)).paymentReceived();
  createCheckoutFeedback(message => messages.push(message)).modal.ondismiss();
  assert.equal(messages.length, 1);
});
