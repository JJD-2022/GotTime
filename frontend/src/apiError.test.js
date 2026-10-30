import test from "node:test";
import assert from "node:assert/strict";
import { apiErrorMessage } from "./apiError.js";

test("missing backend routes explain local restart versus hosted deployment", () => {
  assert.match(apiErrorMessage(404, {}, "localhost"), /Restart/);
  assert.match(apiErrorMessage(404, {}, "127.0.0.1"), /Restart/);
  assert.match(apiErrorMessage(404, {}, "gottime.onrender.com"), /Deploy/);
});

test("specific API errors are preserved and failed requests never become empty results", () => {
  assert.equal(apiErrorMessage(404, { error: "This profile is unavailable." }), "This profile is unavailable.");
  assert.match(apiErrorMessage(500, {}), /server could not load/);
  assert.match(apiErrorMessage(401, {}), /sign in/);
  assert.match(apiErrorMessage(400, { error: {} }), /could not be completed/);
});
