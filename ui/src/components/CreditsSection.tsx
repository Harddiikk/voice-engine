"use client";

import { Check } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { useLeadForms } from "@/context/LeadFormsContext";
import { useAuth } from "@/lib/auth";
import { BOOK_A_MEETING_URL } from "@/lib/brand";
import { cn } from "@/lib/utils";

interface PackFeatures {
  api: boolean;
  mcp: boolean;
  build_with_ai?: boolean;
}
interface Pack {
  id: string;
  label: string;
  minutes: number;
  price_inr: number;
  per_credit_inr?: number;
  features?: PackFeatures;
  // Richer plan detail now returned by /billing/balance. Cast onto the
  // generated Balance shape (client isn't regenerated).
  max_agents?: number;
  max_concurrent_calls?: number;
  includes?: string[];
  highlight?: boolean; // "Most popular"
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
  // ₹ money view at the client's effective per-minute rate. Cast onto the
  // generated Balance shape (client isn't regenerated). money_left_inr is
  // null when the org is unlimited.
  per_minute_inr?: number;
  money_left_inr?: number | null;
  money_spent_inr?: number;
  money_spent_today_inr?: number;
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
    <div className="space-y-6">
      <div className="rounded-2xl border border-border/60 bg-card p-5 shadow-[var(--shadow-card)]">
        <div className="flex items-center justify-between gap-3">
          <p className="text-small text-muted-foreground">Current balance</p>
          {!data.unlimited && (
            <span className="rounded-full border border-primary/30 bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
              {planLabel} plan
            </span>
          )}
        </div>
        <p className="mt-1 text-3xl font-semibold tabular text-foreground">
          {data.unlimited
            ? "Unlimited"
            : `${minutes?.toLocaleString()} credits`}
        </p>
        {/* ₹ worth of the remaining balance at the client's effective rate. */}
        {!data.unlimited && data.money_left_inr != null && (
          <p className="mt-0.5 text-sm text-muted-foreground tabular">
            ≈ ₹{data.money_left_inr.toLocaleString("en-IN")}
            {data.per_minute_inr != null &&
              ` at ₹${data.per_minute_inr.toLocaleString("en-IN")}/min`}
          </p>
        )}
        {!data.unlimited && (
          <p className="mt-1 text-xs text-muted-foreground">
            1 credit = 1 minute of calling.
          </p>
        )}
        {/* Today's spend (calendar day, IST), with all-time as a secondary. */}
        {!data.unlimited && data.money_spent_today_inr != null && (
          <p className="mt-2 text-xs text-muted-foreground">
            Spent today:{" "}
            <span className="font-semibold tabular text-foreground">
              ₹{data.money_spent_today_inr.toLocaleString("en-IN")}
            </span>
            {data.money_spent_inr != null && (
              <span className="text-muted-foreground">
                {" "}
                · ₹{data.money_spent_inr.toLocaleString("en-IN")} all-time
              </span>
            )}
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
        <div>
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="text-label font-semibold text-foreground">
              Top up your credits
            </h2>
            <span className="text-xs text-muted-foreground">
              Secure checkout via PayU
            </span>
          </div>
          <div className="mt-3 grid items-stretch gap-4 sm:grid-cols-3">
            {data.packs.map((pack) => {
              const isCurrent = pack.id === data.plan;
              const isHighlight = pack.highlight === true;
              return (
                <div
                  key={pack.id}
                  className={cn(
                    "relative flex flex-col rounded-2xl border bg-card p-5 text-card-foreground shadow-[var(--shadow-card)] transition-[box-shadow,transform,border-color] duration-200 ease-[var(--ease-out)]",
                    isHighlight
                      ? "border-primary/50 ring-1 ring-primary/20 shadow-[var(--shadow-pop)]"
                      : "border-border/60 hover:-translate-y-0.5 hover:shadow-[var(--shadow-pop)]",
                  )}
                >
                  {isHighlight && (
                    <span className="absolute -top-2.5 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-primary px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary-foreground shadow-[var(--shadow-card)]">
                      Most popular
                    </span>
                  )}
                  <div className="flex flex-1 flex-col">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-label font-semibold text-foreground">
                        {pack.label}
                      </p>
                      {isCurrent && (
                        <span className="shrink-0 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                          Current
                        </span>
                      )}
                    </div>

                    <p className="mt-3 text-2xl font-semibold tabular text-foreground">
                      ₹{pack.price_inr.toLocaleString("en-IN")}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground tabular">
                      {pack.minutes.toLocaleString()} credits
                      {pack.per_credit_inr != null &&
                        ` · ₹${pack.per_credit_inr}/credit`}
                    </p>

                    {pack.includes && pack.includes.length > 0 ? (
                      <ul className="mt-4 space-y-2 border-t border-border/60 pt-4 text-[13px] leading-snug">
                        {pack.includes.map((item) => (
                          <li
                            key={item}
                            className="flex items-start gap-2 text-muted-foreground"
                          >
                            <Check
                              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary"
                              aria-hidden="true"
                            />
                            <span>{item}</span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      pack.features && (
                        <ul className="mt-4 space-y-2 border-t border-border/60 pt-4 text-[13px]">
                          {(
                            [
                              ["API access", pack.features.api],
                              ["MCP server", pack.features.mcp],
                            ] as const
                          ).map(([featLabel, on]) => (
                            <li
                              key={featLabel}
                              className={cn(
                                "flex items-center gap-2",
                                on
                                  ? "text-muted-foreground"
                                  : "text-muted-foreground/40 line-through",
                              )}
                            >
                              <Check
                                className={cn(
                                  "h-3.5 w-3.5 shrink-0",
                                  on ? "text-primary" : "text-muted-foreground/40",
                                )}
                                aria-hidden="true"
                              />
                              <span>{featLabel}</span>
                            </li>
                          ))}
                        </ul>
                      )
                    )}
                  </div>

                  <Button
                    className="mt-5 w-full"
                    variant={isCurrent ? "outline" : "brand"}
                    disabled={busy === pack.id}
                    onClick={() => buy(pack)}
                  >
                    {busy === pack.id
                      ? "Opening…"
                      : isCurrent
                        ? "Add more"
                        : "Choose plan"}
                  </Button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Enterprise — custom pricing / committed volume: contact + book a meeting */}
      <div className="rounded-2xl border border-primary/30 bg-primary/5 p-5 shadow-[var(--shadow-card)]">
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
        <div className="rounded-2xl border border-border/60 bg-card p-5 shadow-[var(--shadow-card)]">
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
