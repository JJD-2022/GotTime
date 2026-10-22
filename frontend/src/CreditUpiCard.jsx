import { useState } from "react";
import { QrCode } from "lucide-react";

export default function CreditUpiCard({ packs, buyCredits }) {
  const [packageId, setPackageId] = useState(packs[0].id);
  const [opening, setOpening] = useState(false);
  const openCheckout = async (event) => {
    event.preventDefault();
    if (opening) return;
    setOpening(true);
    try { await buyCredits(packageId, { upiOnly: true }); }
    finally { setOpening(false); }
  };
  return <section className="content-card upi-qr-panel credit-upi-panel">
    <div><span className="eyebrow"><QrCode size={16} /> UPI / QR</span><h3>Scan &amp; buy credits</h3><p>Choose a pack to pay in secure Razorpay checkout.</p></div>
    <form className="credit-upi-controls" onSubmit={openCheckout}>
      <label>Credit pack<select value={packageId} disabled={opening} onChange={(event) => setPackageId(event.target.value)}>
        {packs.map((pack) => <option key={pack.id} value={pack.id}>{pack.credits} credits — ₹{pack.price}</option>)}
      </select></label>
      <button className="primary" type="submit" disabled={opening}><QrCode size={18} /> {opening ? "Opening…" : "Open UPI / QR"}</button>
    </form>
  </section>;
}
