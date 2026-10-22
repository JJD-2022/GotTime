import test from "node:test";
import assert from "node:assert/strict";
import { razorpayPaymentOptions } from "./razorpayCheckout.js";

test("normal checkout includes UPI alongside existing payment methods", () => {
  const options = razorpayPaymentOptions();
  assert.deepEqual(options.method, { upi: true, card: true, netbanking: true, wallet: true });
  assert.deepEqual(options.config.display.sequence, ["block.upi", "block.cards", "block.netbanking", "block.wallets"]);
  assert.deepEqual(options.config.display.blocks.upi.instruments, [{ method: "upi" }]);
});

test("checkout keeps UPI flows available without overriding payment verification", () => {
  const options = razorpayPaymentOptions();
  assert.equal(options.config.display.preferences.show_default_blocks, false);
  assert.deepEqual(options.config.display.blocks.upi.instruments, [{ method: "upi" }]);
  assert.equal("handler" in options, false);
  assert.equal("order_id" in options, false);
});

test("each checkout receives a fresh configuration", () => {
  const first = razorpayPaymentOptions();
  first.config.display.sequence.length = 0;
  assert.equal(razorpayPaymentOptions().config.display.sequence[0], "block.upi");
});
