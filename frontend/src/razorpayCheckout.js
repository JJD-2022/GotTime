// Keep UPI in the explicit display sequence: hidden default blocks otherwise
// remove it even when the merchant has activated UPI in the dashboard.
export function razorpayPaymentOptions() {
  const blocks = {
    upi: { name: "UPI / QR", instruments: [{ method: "upi" }] },
    cards: { name: "Cards", instruments: [{ method: "card" }] },
    netbanking: { name: "Netbanking", instruments: [{ method: "netbanking" }] },
    wallets: { name: "Wallets", instruments: [{ method: "wallet" }] },
  };
  return {
    method: { upi: true, card: true, netbanking: true, wallet: true },
    config: { display: {
      blocks,
      sequence: Object.keys(blocks).map((name) => `block.${name}`),
      preferences: { show_default_blocks: false },
    } },
  };
}
