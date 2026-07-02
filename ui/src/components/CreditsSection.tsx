"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { useLeadForms } from "@/context/LeadFormsContext";
import { useAuth } from "@/lib/auth";
import { BOOK_A_MEETING_URL } from "@/lib/brand";

interface PackFeatures {
  api: boolean;
  mcp: boolean;
}
interface Pack {
  id: string;
  label: string;
  minutes: number;
  price_inr: number;
  per_credit_inr?: number;
  features?: PackFeatures;
}
interface Balance {
  balance_seconds: number | null;
  unlimited: boolean;
  configured: boolean;
  packs: Pack[];
  plan?: string;
  features?: PackFeatures;
  // Sum of active per-call reservations (each live call briefly holds up to
  // 10 credits; the unused part returns when the call settles).
  on_hold_seconds?: number;
}

interface LedgerEntry {
  id: number;
  kind: string;
  delta_seconds: number;
  balance_after: number | null;
  description: string | null;
  workflow_run_id: number | null;
  created_at: string;
}

// reserve/release pairs are bookkeeping noise for most users — the net
// "settle_charge" row is the real story of a call. Hidden behind a toggle.
const HOLD_KINDS = new Set(["reserve", "settle_release"]);

const KIND_LABELS: Record<string, string> = {
  reserve: "Hold",
  settle_release: "Hold released",
  settle_charge: "Call",
  topup: "Top-up",
  grant: "Granted",
  number_purchase: "Number purchase",
  refund: "Refund",
  adjustment: "Adjustment",
  leak_sweep: "Hold recovered",
};

// PayU Hosted Checkout is a redirect flow: the backend returns the PayU payment
// URL + a server-signed set of form fields, we auto-POST them, and PayU redirects
// back to /credits?payment=success|failed. The credited amount is decided
// server-side from the stored transaction; the SALT never reaches the browser.
function submitToPayU(paymentUrl: string, params: Record<string, string>) {
  const form = document.createElement("form");
  form.method = "POST";
  form.action = paymentUrl;
  for (const [name, value] of Object.entries(params)) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value ?? "";
    form.appendChild(input);
  }
  document.body.appendChild(form);
  form.submit();
}

export function CreditsSection() {
  const { user, loading: authLoading } = useAuth();
  const { openEnterprise } = useLeadForms();
  const [data, setData] = useState<Balance | null>(null);
  const [ledger, setLedger] = useState<LedgerEntry[] | null>(null);
  const [showHolds, setShowHolds] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const hasFetched = useRef(false);

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) return;
    hasFetched.current = true;
    refresh();
  }, [authLoading, user]);

  // Handle the PayU return: /credits?payment=success|failed → toast, refresh
  // the balance, and strip the query param.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const outcome = new URLSearchParams(window.location.search).get("payment");
    if (!outcome) return;
    if (outcome === "success") toast.success("Payment successful — credits added!");
    else toast.error("Payment failed or was cancelled.");
    window.history.replaceState({}, "", "/credits");
    refresh();
  }, []);

  async function refresh() {
    try {
      const res = await client.get({ url: "/api/v1/billing/balance" });
      setData(res.data as Balance);
    } catch {
      /* ignore */
    }
    try {
      const res = await client.get({ url: "/api/v1/billing/ledger?limit=100" });
      if (Array.isArray(res.data)) setLedger(res.data as LedgerEntry[]);
    } catch {
      /* ledger is additive UI — never block the balance on it */
    }
  }

  async function buy(pack: Pack) {
    setBusy(pack.id);
    try {
      const res = await client.post({
        url: "/api/v1/billing/payu/initiate",
        body: { pack_id: pack.id },
      });
      if (res.error) throw new Error("initiate_failed");
      const { payment_url, params } = res.data as {
        payment_url: string;
        params: Record<string, string>;
      };
      // Redirects the browser to PayU; on success it returns to
      // /credits?payment=success and the effect below toasts + refreshes.
      submitToPayU(payment_url, params);
    } catch {
      toast.error("Couldn't start checkout");
      setBusy(null); // on redirect success the page navigates away
    }
  }

  if (!data) return <p className="text-sm text-muted-foreground">Loading...</p>;

  const minutes =
    data.balance_seconds == null ? null : Math.floor(data.balance_seconds / 60);
  const planLabel =
    data.packs.find((p) => p.id === data.plan)?.label ??
    (data.plan && data.plan !== "trial" ? data.plan : "Trial");

  return (
    <div className="space-y-5">
      <div className="rounded-md border p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">Current balance</p>
          {!data.unlimited && (
            <span className="rounded-full border border-primary/30 bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
              {planLabel} plan
            </span>
          )}
        </div>
        <p className="text-2xl font-bold tabular">
          {data.unlimited
            ? "Unlimited"
            : `${minutes?.toLocaleString()} credits`}
        </p>
        {!data.unlimited && (
          <p className="mt-1 text-xs text-muted-foreground">
            1 credit = 1 minute of calling.
          </p>
        )}
        {!data.unlimited && (data.on_hold_seconds ?? 0) > 0 && (
          <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
            On hold: {(Math.round(((data.on_hold_seconds ?? 0) / 60) * 10) / 10).toLocaleString()}{" "}
            credits — each active call briefly holds up to 10 credits; the
            unused part returns when the call ends.
          </p>
        )}
      </div>

      {data.unlimited ? (
        <p className="text-sm text-muted-foreground">
          Your account has unlimited calling — no top-up needed.
        </p>
      ) : !data.configured ? (
        <p className="text-sm text-muted-foreground">
          Top-ups aren&apos;t enabled yet. Once the payment gateway is connected
          you&apos;ll be able to buy more minutes here.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-3">
          {data.packs.map((pack) => {
            const isCurrent = pack.id === data.plan;
            return (
              <div
                key={pack.id}
                className={`relative flex flex-col justify-between rounded-xl border p-4 transition-shadow ${
                  isCurrent
                    ? "border-primary/50 shadow-[var(--shadow-card)]"
                    : "hover:shadow-[var(--shadow-card)]"
                }`}
              >
                {isCurrent && (
                  <span className="absolute -top-2 right-3 rounded-full bg-primary px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary-foreground">
                    Current
                  </span>
                )}
                <div>
                  <p className="font-semibold">{pack.label}</p>
                  <p className="text-sm text-muted-foreground">
                    {pack.minutes.toLocaleString()} credits
                  </p>
                  <p className="mt-2 text-lg font-bold tabular">
                    ₹{pack.price_inr.toLocaleString()}
                  </p>
                  {pack.per_credit_inr != null && (
                    <p className="text-xs text-muted-foreground">
                      ₹{pack.per_credit_inr}/credit
                    </p>
                  )}
                  {pack.features && (
                    <ul className="mt-3 space-y-1 text-xs">
                      <li className={pack.features.api ? "text-foreground" : "text-muted-foreground/50"}>
                        {pack.features.api ? "✓" : "✕"} API access
                      </li>
                      <li className={pack.features.mcp ? "text-foreground" : "text-muted-foreground/50"}>
                        {pack.features.mcp ? "✓" : "✕"} MCP server
                      </li>
                    </ul>
                  )}
                </div>
                <Button
                  className="mt-3"
                  variant={isCurrent ? "outline" : "brand"}
                  disabled={busy === pack.id}
                  onClick={() => buy(pack)}
                >
                  {busy === pack.id
                    ? "Opening..."
                    : isCurrent
                      ? "Add more"
                      : "Choose plan"}
                </Button>
              </div>
            );
          })}
        </div>
      )}

      {/* Enterprise — custom pricing / committed volume: contact + book a meeting */}
      <div className="rounded-xl border border-primary/30 bg-primary/5 p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="font-semibold">Enterprise</p>
            <p className="text-sm text-muted-foreground">
              Custom pricing for committed volume — dedicated numbers, priority
              support, SLAs, and volume discounts.
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Talk to us:{" "}
              <a
                href="mailto:hardikagarwal@autosysai.dev?subject=Enterprise%20plan%20—%20Auto4You"
                className="underline underline-offset-4 hover:text-foreground"
              >
                hardikagarwal@autosysai.dev
              </a>
            </p>
          </div>
          <div className="flex shrink-0 flex-col gap-2 sm:items-end">
            <Button variant="brand" className="w-full sm:w-auto" asChild>
              <a href={BOOK_A_MEETING_URL} target="_blank" rel="noopener noreferrer">
                Book a meeting
              </a>
            </Button>
            <button
              type="button"
              className="text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
              onClick={() => openEnterprise("billing_custom_pricing")}
            >
              Or send us your details
            </button>
          </div>
        </div>
      </div>

      {/* Billing history — every credit movement, newest first. Hold/release
          bookkeeping pairs are collapsed behind a toggle; the net per-call
          charge row is what most users want to see. */}
      {!data.unlimited && ledger !== null && (
        <div className="rounded-md border p-4">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-semibold">Billing history</p>
            <button
              type="button"
              className="text-xs text-muted-foreground underline-offset-2 hover:underline"
              onClick={() => setShowHolds((v) => !v)}
            >
              {showHolds ? "Hide holds" : "Show holds"}
            </button>
          </div>
          {ledger.length === 0 ? (
            <p className="mt-3 text-sm text-muted-foreground">
              No billing activity yet — calls, top-ups, and number purchases
              will appear here.
            </p>
          ) : (
            <ul className="mt-3 divide-y">
              {ledger
                .filter((e) => showHolds || !HOLD_KINDS.has(e.kind))
                .map((e) => {
                  const credits = Math.round((e.delta_seconds / 60) * 10) / 10;
                  const positive = e.delta_seconds > 0;
                  return (
                    <li
                      key={e.id}
                      className="flex items-center justify-between gap-3 py-2"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm">
                          {e.description || KIND_LABELS[e.kind] || e.kind}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {KIND_LABELS[e.kind] || e.kind} ·{" "}
                          {new Date(e.created_at).toLocaleString()}
                        </p>
                      </div>
                      <span
                        className={`shrink-0 text-sm font-medium tabular ${
                          positive
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-muted-foreground"
                        }`}
                      >
                        {positive ? "+" : ""}
                        {credits.toLocaleString()} credits
                      </span>
                    </li>
                  );
                })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
