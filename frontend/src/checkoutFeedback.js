// Closing Checkout is not proof that a payment failed or that nothing was debited.
export function createCheckoutFeedback(notify) {
  let paymentReceived = false;
  let dismissalReported = false;
  return {
    paymentReceived() { paymentReceived = true; },
    modal: {
      confirm_close: true,
      ondismiss() {
        if (paymentReceived || dismissalReported) return;
        dismissalReported = true;
        notify("Checkout closed. If money was debited, do not pay again; check back shortly or contact support.");
      },
    },
  };
}
