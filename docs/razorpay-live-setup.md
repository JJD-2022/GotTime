# Razorpay live setup

Approval of your Razorpay account does not change the keys in Render. Test and live
payments are separate. Never put a secret in frontend code, GitHub, screenshots,
or chat. This setup affects both credit purchases and Razorpay donations.

## Before accepting real payments

1. Deploy the payment-verification changes in this repository. They add
   `POST /api/payments/razorpay/webhook`; opening this URL in a browser is not a
   valid webhook test (the endpoint accepts signed POSTs, not GETs).
2. First test with Razorpay **Test Mode** keys and a test webhook on a separate
   staging service/database. A test payment can grant app credits, so do not mix
   test transactions with real customer balances. Complete a test checkout,
   check the wallet, and confirm webhook delivery succeeds. Replay the event and
   verify no extra credits are added. Also test a failed payment.
3. In Razorpay, switch to **Live Mode**, then **Accounts & Settings → API Keys**.
   Generate/copy the live Key ID and Key Secret. Keep existing live keys if
   already configured correctly; do not rotate them unnecessarily.
4. Create a separate long random webhook secret in a password manager. In Render
   **GotTime service → Environment**, set:

   | Key | Value |
   | --- | --- |
   | `RAZORPAY_KEY_ID` | Your live Key ID, starting `rzp_live_` |
   | `RAZORPAY_KEY_SECRET` | The matching live Key Secret |
   | `RAZORPAY_WEBHOOK_SECRET` | Your separate random webhook secret |

   Save and redeploy. Do not overwrite your database, Google, or `SECRET_KEY`
   settings. New live checkouts are refused if the webhook secret is missing;
   the presence of the variable does **not** prove the dashboard webhook exists.
5. In the Razorpay **Live Mode** dashboard, open **Accounts & Settings → Webhooks
   → Add New Webhook**:
   - URL: `https://gottime.onrender.com/api/payments/razorpay/webhook`
   - Secret: the exact same value as `RAZORPAY_WEBHOOK_SECRET` in Render.
   - Active events: `payment.captured` and `order.paid`.
   - Alert email: an address you monitor.
   - Create and enable the webhook.
6. Configure **automatic payment capture** in Razorpay's payment capture settings.
   An authorized payment is not a captured payment. GotTime waits for capture
   before adding credits or confirming a donation.
7. Once all of the above is deployed, perform a small real checkout yourself.
   This charges real money; it is not an automated test. Verify the payment is
   **Captured** in Razorpay, the expected credits appear **once** after reload,
   the local order is paid, and webhook deliveries receive HTTP 200. A donation
   checks the donation flow, not the credit-wallet flow. Do not ask customers to
   retry a charge just because confirmation is delayed.

## What the application checks

- Checkout HMAC signature, signed-in order owner, and stored order ID.
- Provider payment fetched with the server-side API keys; payment ID, order ID,
  exact amount in paise, currency, captured status, and no prior refund.
- Webhook HMAC over the original request bytes using the separate secret.
- A database row lock and one transaction for paid status, wallet, ledger, and
  notification. Browser callbacks and repeated/out-of-order webhook deliveries
  cannot grant the same order twice. Donations never add wallet credits.
- Pending capture returns a clear message. Transient provider failures return
  a non-2xx webhook response so Razorpay retries instead of silently losing it.

## Operational limits and recovery

Razorpay expects a 2xx webhook response within five seconds and retries failed
deliveries for up to 24 hours before disabling a failing webhook. Keep the app
and database reachable, monitor latency and alert emails, and check delivery
logs after deployments. This implementation does synchronous provider/DB work;
it is not a durable background queue or a guarantee of five-second delivery.

If a captured payment is missing credits, look up the order/payment in Razorpay
and the matching `credit_payments` row. Fix the underlying configuration or
connectivity problem, then use the available webhook retry/replay facilities or
an authenticated checkout verification retry. Check whether it was already
fulfilled before any manual adjustment. Never edit a balance blindly or rerun
a charge. Re-enable a disabled webhook after fixing it.

Refunds, disputes, and chargebacks **after fulfillment are not automatically
reconciled** with the credit wallet by this change. Handle them manually with
an audit trail; define the refund policy before launch. Payments already
marked paid by older code are not retroactively re-verified. Public pricing,
contact/support, privacy, terms, and refund policies remain business launch
requirements, not something account approval or these tests proves complete.

Do not rotate webhook secrets while old deliveries are pending without planning
the transition: this implementation accepts only the currently configured secret.

## References

- [Razorpay API keys](https://razorpay.com/docs/payments/dashboard/account-settings/api-keys/)
- [Set up and edit webhooks](https://razorpay.com/docs/webhooks/setup-edit-payments/)
- [Validate and test webhooks](https://razorpay.com/docs/webhooks/validate-test/)
- [Checkout integration and capture](https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/)
