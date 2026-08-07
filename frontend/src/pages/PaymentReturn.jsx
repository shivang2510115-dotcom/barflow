import React, { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { api, currency } from "@/lib/api";
import CocktailLoader from "@/components/app/CocktailLoader";
import BillSuccess from "@/components/app/BillSuccess";
import { Wine } from "lucide-react";

/**
 * /t/:tableId?paid=1&session_id=... — Stripe return page.
 * Polls checkout status (Stripe emergent proxy is async) and shows Bill Success once paid.
 */
export default function PaymentReturn() {
  const { tableId } = useParams();
  const loc = useLocation();
  const nav = useNavigate();
  const [statusData, setStatusData] = useState(null);
  const [attempts, setAttempts] = useState(0);
  const [failed, setFailed] = useState(false);

  const qs = new URLSearchParams(loc.search);
  const paid = qs.get("paid");
  const sessionId = qs.get("session_id");

  useEffect(() => {
    if (paid !== "1" || !sessionId) return;
    let cancelled = false;

    const poll = async (n = 0) => {
      if (cancelled) return;
      if (n > 8) {
        setFailed(true);
        return;
      }
      try {
        const { data } = await api.get(`/payments/checkout/status/${sessionId}`);
        if (cancelled) return;
        setStatusData(data);
        setAttempts(n);
        if (data.payment_status === "paid") return;
        if (data.status === "expired") {
          setFailed(true);
          return;
        }
      } catch {
        // ignore transient errors
      }
      setTimeout(() => poll(n + 1), 1800);
    };
    poll(0);
    return () => {
      cancelled = true;
    };
  }, [paid, sessionId]);

  const paidOk = statusData?.payment_status === "paid";

  return (
    <div className="min-h-screen bg-stone-950 text-stone-100 flex items-center justify-center p-8 relative z-[2]">
      <div className="max-w-md w-full text-center">
        <div className="flex items-center justify-center gap-2 text-orange-500 text-[10px] uppercase tracking-[0.4em] font-mono mb-6">
          <Wine size={14} /> BarFlow · Table
        </div>

        {paid === "0" && (
          <>
            <div className="font-display uppercase text-4xl">Payment cancelled</div>
            <p className="text-stone-400 mt-4">
              No worries — nothing was charged. Try again or settle at the counter.
            </p>
            <button
              onClick={() => nav(`/t/${tableId}`)}
              className="mt-8 rounded-full bg-orange-600 hover:bg-orange-500 text-stone-950 px-6 py-3 text-[10px] font-mono uppercase tracking-widest"
              data-testid="pay-return-back"
            >
              Back to menu
            </button>
          </>
        )}

        {paid === "1" && !paidOk && !failed && (
          <>
            <CocktailLoader label={`Confirming payment · attempt ${attempts + 1}`} />
            <p className="text-stone-500 text-xs mt-6 font-mono uppercase tracking-widest">
              Hold tight — the ledger is settling.
            </p>
          </>
        )}

        {paid === "1" && failed && (
          <>
            <div className="font-display uppercase text-3xl">Still verifying</div>
            <p className="text-stone-400 mt-4">
              Stripe is taking longer than usual. If you were charged, staff will see the settled bill
              shortly. You can safely close this page.
            </p>
            <button
              onClick={() => nav(`/t/${tableId}`)}
              className="mt-8 rounded-full border border-stone-700 hover:border-orange-500 px-6 py-3 text-[10px] font-mono uppercase tracking-widest"
              data-testid="pay-return-back"
            >
              Return to table
            </button>
          </>
        )}

        <BillSuccess
          open={!!paidOk}
          amount={(statusData?.amount_total || 0) / 100}
          onClose={() => nav(`/t/${tableId}`)}
        />
      </div>
    </div>
  );
}
