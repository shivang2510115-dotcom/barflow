import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import axios from "axios";
import { BellRing, Check, Sparkles } from "lucide-react";

import { API, formatApiErrorDetail } from "@/lib/api";

/**
 * The card beside the kettle: a guest scans it and asks for their room to be dealt with.
 *
 * No account, exactly like the table QR that already ships, and scoped the same way — the
 * room id in the URL is the only thing that names the hotel, so nothing a guest can type
 * reaches another room or another property. `publicApi` is a bare axios client rather than
 * `@/lib/api`'s, for the same reason `CustomerMenu` has one: the shared client attaches
 * whatever token is in this browser's local storage, and a guest has none — but a member
 * of staff scanning the card in a room they are standing in *does*, and an expired one
 * would turn this page into a 401 for them.
 *
 * **It shows nothing else.** Not the room's status, not whether anybody has already asked
 * for something, not who is staying anywhere. Two fields come back from the server and
 * both are printed on the card the guest is holding: the hotel's name and the room number.
 * The response to a request is the same whether it raised a new one or merged into one
 * already open — telling the guest "we already have one of those" would be handing them a
 * fact about the hotel's operations.
 */
const publicApi = axios.create({ baseURL: API });

export default function GuestRoomRequest() {
  const { roomId } = useParams();
  // null while loading, false when the code is no good, the card otherwise — the three
  // states `CustomerMenu` uses, for the same reason: "still asking" and "this QR is not
  // valid" must not render the same screen.
  const [card, setCard] = useState(null);
  const [reason, setReason] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [refused, setRefused] = useState("");

  useEffect(() => {
    let live = true;
    publicApi
      .get(`/housekeeping/room/${roomId}/public`)
      .then((r) => live && setCard(r.data))
      .catch(() => live && setCard(false));
    return () => {
      live = false;
    };
  }, [roomId]);

  const send = async () => {
    setSending(true);
    setRefused("");
    try {
      await publicApi.post(`/housekeeping/room/${roomId}/requests`, { reason });
      setSent(true);
    } catch (e) {
      // The 429 the rate limiter answers with names the front desk, which is the useful
      // next step for the guest whose phone retried. Shown on the page rather than in a
      // toast: this is the only thing on the screen they are waiting for.
      setRefused(
        formatApiErrorDetail(e.response?.data?.detail) ||
          "That did not go through. Please call the front desk.",
      );
    } finally {
      setSending(false);
    }
  };

  if (card === null)
    return (
      <div className="min-h-screen bg-stone-950 text-stone-500 flex items-center justify-center font-mono text-xs uppercase tracking-widest">
        Loading…
      </div>
    );

  if (card === false)
    return (
      <div className="min-h-screen bg-stone-950 text-stone-100 flex items-center justify-center p-8">
        <div className="text-center max-w-xs">
          <div className="text-[10px] font-mono uppercase tracking-[0.4em] text-orange-500">
            Housekeeping
          </div>
          <h1 className="mt-4 font-display uppercase text-2xl">This code is not valid</h1>
          <p className="mt-3 text-sm text-stone-400">
            Please call the front desk and they will help straight away.
          </p>
        </div>
      </div>
    );

  return (
    <div className="min-h-screen bg-stone-950 text-stone-100 flex flex-col">
      <header className="px-6 pt-12 pb-8">
        <div className="flex items-center gap-2 text-orange-500 text-[10px] uppercase tracking-[0.4em] font-mono">
          <Sparkles size={14} /> {card.property_name || "Housekeeping"}
        </div>
        <h1 className="mt-4 font-display uppercase text-4xl leading-none tracking-tight">
          Room {card.room_number}
        </h1>
      </header>

      {sent ? (
        /* Plain confirmation and nothing else. No job number — there is no page a guest
           could take one to — and no word about what happens next beyond the true one. */
        <main className="flex-1 px-6" data-testid="guest-request-sent">
          <div className="border border-emerald-500/40 bg-emerald-500/5 rounded p-6 text-center">
            <Check className="mx-auto text-emerald-400" size={28} aria-hidden="true" />
            <p className="mt-4 text-lg">Housekeeping has been told.</p>
            <p className="mt-2 text-sm text-stone-400">
              Somebody will be along shortly. You can close this page.
            </p>
          </div>
          <button
            type="button"
            data-testid="guest-request-again"
            onClick={() => {
              setSent(false);
              setReason("");
            }}
            className="mt-6 w-full border border-stone-700 text-stone-400 hover:text-stone-200 rounded-full py-3 text-xs font-mono uppercase tracking-widest"
          >
            Ask for something else
          </button>
        </main>
      ) : (
        <main className="flex-1 px-6">
          <label className="block text-[10px] font-mono uppercase tracking-widest text-stone-500">
            What do you need?
            <textarea
              value={reason}
              rows={4}
              data-testid="guest-reason"
              placeholder="Fresh towels, please"
              onChange={(e) => setReason(e.target.value)}
              className="block w-full mt-3 bg-stone-900 border border-stone-700 text-stone-100 text-base p-4 rounded focus:border-orange-500 outline-none"
            />
          </label>
          <p className="mt-2 text-xs text-stone-500">
            You can leave this empty if it is easier — we will come and see.
          </p>

          {refused && (
            <p className="mt-4 text-sm text-orange-300" data-testid="guest-request-refused">
              {refused}
            </p>
          )}

          {/* Full width, at the bottom of the page, tall enough for a thumb: this is read
              standing up, on a phone, in a room. */}
          <button
            type="button"
            data-testid="guest-request-send"
            disabled={sending}
            onClick={send}
            className="mt-6 w-full flex items-center justify-center gap-2 bg-orange-600 hover:bg-orange-500
                       disabled:opacity-50 text-white rounded-full py-4 text-sm font-mono uppercase tracking-widest"
          >
            <BellRing size={16} aria-hidden="true" />
            {sending ? "Sending…" : "Ask housekeeping"}
          </button>
        </main>
      )}

      <footer className="p-6 text-center text-[10px] font-mono uppercase tracking-widest text-stone-600">
        {card.property_name}
      </footer>
    </div>
  );
}
