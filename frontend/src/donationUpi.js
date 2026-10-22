export function donationUpiDetails(config) {
  if (!config || typeof config.upiId !== "string") throw new Error("Invalid UPI configuration response.");
  const upiId = config.upiId.trim();
  if (!upiId) return null;
  if (!/^[^\s@]+@[^\s@]+$/.test(upiId)) throw new Error("Invalid UPI address configuration.");
  const payeeName = typeof config.payeeName === "string" && config.payeeName.trim() ? config.payeeName.trim() : "GotTime";
  const link = `upi://pay?${new URLSearchParams({ pa: upiId, pn: payeeName, cu: "INR", tn: "GotTime donation" })}`;
  return { upiId, payeeName, link, qr: `https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=${encodeURIComponent(link)}` };
}

export async function loadDonationUpi(request, signal) {
  try {
    const details = donationUpiDetails(await request(signal));
    return { status: details ? "ready" : "missing", details };
  } catch (error) {
    return { status: "error", message: error.status === 401
      ? "Your session expired. Please sign in again to load UPI details."
      : "We couldn't load the UPI details. Please try again." };
  }
}
