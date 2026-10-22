import test from "node:test";
import assert from "node:assert/strict";
import { donationUpiDetails, loadDonationUpi } from "./donationUpi.js";

test("UPI configuration is trimmed and encoded without changing the recipient", () => {
  const details = donationUpiDetails({ upiId: " demo@bank ", payeeName: " GotTime & Friends " });
  const link = new URL(details.link);
  assert.equal(link.protocol, "upi:");
  assert.equal(link.searchParams.get("pa"), "demo@bank");
  assert.equal(link.searchParams.get("pn"), "GotTime & Friends");
  assert.equal(link.searchParams.get("cu"), "INR");
  assert.equal(new URL(details.qr).searchParams.get("data"), details.link);
  assert.equal(donationUpiDetails({ upiId: "demo@bank", payeeName: " " }).payeeName, "GotTime");
});

test("only an explicitly empty UPI setting is treated as missing", async () => {
  for (const upiId of ["", "   "]) {
    assert.equal((await loadDonationUpi(async () => ({ upiId }))).status, "missing");
  }
  for (const config of [null, {}, { upiId: 123 }, { upiId: "not-an-address" }, { upiId: "demo @bank" }]) {
    assert.equal((await loadDonationUpi(async () => config)).status, "error");
  }
});

test("network, server and login failures never claim the UPI ID is missing", async () => {
  for (const status of [undefined, 401, 500]) {
    const result = await loadDonationUpi(async () => { throw Object.assign(new Error("private server detail"), { status }); });
    assert.equal(result.status, "error");
    assert.equal(result.message.includes("private server detail"), false);
    if (status === 401) assert.match(result.message, /sign in again/);
  }
});

test("retry can recover and the request receives its cancellation signal", async () => {
  const controller = new AbortController();
  const result = await loadDonationUpi(async (signal) => {
    assert.equal(signal, controller.signal);
    return { upiId: "demo@bank" };
  }, controller.signal);
  assert.equal(result.status, "ready");
  assert.equal(result.details.upiId, "demo@bank");
});
