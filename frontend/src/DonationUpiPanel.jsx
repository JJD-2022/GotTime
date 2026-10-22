import { useEffect, useState } from "react";
import { Coins, Sparkles } from "lucide-react";
import { loadDonationUpi } from "./donationUpi";

export default function DonationUpiPanel({ request }) {
  const [result, setResult] = useState({ status: "loading" });
  const [retry, setRetry] = useState(0);
  const [qrFailed, setQrFailed] = useState(false);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    // A stalled request must not leave the donation card loading forever.
    const timeout = setTimeout(() => controller.abort(), 15000);
    setResult({ status: "loading" });
    setQrFailed(false);
    loadDonationUpi(request, controller.signal).then((next) => {
      if (active) setResult(next);
    }).finally(() => clearTimeout(timeout));
    return () => { active = false; clearTimeout(timeout); controller.abort(); };
  }, [request, retry]);
  const details = result.details;
  return <section className="content-card upi-qr-panel">
    <div><span className="eyebrow"><Coins size={15} /> Scan &amp; pay</span><h3>Pay with any UPI app</h3><p>Scan the QR to donate directly.</p></div>
    {result.status === "ready" ? <div className="upi-qr-box">
      {qrFailed ? <div className="upi-qr-placeholder" role="status"><b>Couldn't load QR code</b><button type="button" className="soft-button" onClick={() => setRetry((value) => value + 1)}>Try again</button></div>
        : <img src={details.qr} alt="UPI donation QR code" onError={() => setQrFailed(true)} />}
    </div> : <div className="upi-qr-placeholder" role="status" aria-live="polite">
      <Sparkles size={28} />
      <b>{result.status === "loading" ? "Loading UPI details…" : result.status === "missing" ? "Direct UPI isn't available yet" : "Couldn't load UPI details"}</b>
      <span>{result.status === "loading" ? "Please wait while we fetch the payment details." : result.status === "missing" ? "You can still support GotTime using the payment buttons above." : result.message}</span>
      {result.status !== "loading" && <button type="button" className="soft-button" onClick={() => setRetry((value) => value + 1)}>Try again</button>}
    </div>}
  </section>;
}
