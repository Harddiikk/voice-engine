"use client";

import {
  ArrowRight,
  CheckCircle2,
  Circle,
  Clock,
  Phone,
  PhoneOff,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { client } from "@/client/client.gen";
import { KycWizard } from "@/components/kyc/KycWizard";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";
import { getKycStatus, type KycStatus } from "@/lib/kyc";
import { cn } from "@/lib/utils";

interface Did {
  did_id?: number | null;
  did_number: number | string | null;
  type_label?: string | null;
  country_code?: number | string | null;
  source?: string | null; // "local" when healed from our own records
}

interface NumbersPayload {
  numbers?: Did[];
  price_inr?: number;
  setup_seconds?: number;
}

// TODO(support): wire a real "request manual review" backend endpoint. For now
// this opens the operator's inbox so the customer can flag a rejected KYC.
const MANUAL_REVIEW_EMAIL = "hardikagarwal@autosysai.dev";

type KycBanner =
  | "unconfigured"
  | "not_started"
  | "pending"
  | "rejected"
  | "verified";

/**
 * Map the `/kyc/status` payload to a banner state.
 *
 * False-"Verified" guard: a fresh org with no VoiceLink `client_id` gets the
 * RESELLER's KYC status back from `/kyc/status` (which would misleadingly show
 * "Verified"). `client_id_configured` is exactly the client-scoping signal the
 * backend already returns — it's `bool(client_id)`, and when there is no
 * client_id the upstream call is made WITHOUT a client_id (reseller-scoped). So
 * when `client_id_configured` is false we never trust a "verified" and default
 * to "not started". No backend change was needed.
 */
function deriveKycBanner(status: KycStatus | null): KycBanner {
  if (!status || !status.enabled) return "unconfigured";
  const clientScoped = status.client_id_configured === true;
  if (!clientScoped) return "not_started";
  if (status.is_complete) return "verified";
  const label = String(status.kyc_status ?? "").toLowerCase();
  if (
    label.includes("reject") ||
    label.includes("fail") ||
    label.includes("action")
  ) {
    return "rejected";
  }
  if (
    label.includes("review") ||
    label.includes("pending") ||
    label.includes("submit") ||
    label.includes("process")
  ) {
    return "pending";
  }
  return "not_started";
}

function failingDoc(status: KycStatus | null, isBusiness: boolean): string {
  if (!status?.pan_verified) return "PAN";
  if (!status?.aadhaar_verified) return "Aadhaar";
  if (isBusiness && !status?.gst_verified) return "GST";
  return "your documents";
}

/** Compact horizontal stepper for the "under review" banner. */
function KycStepper({
  status,
  isBusiness,
}: {
  status: KycStatus | null;
  isBusiness: boolean;
}) {
  const steps: { label: string; done: boolean }[] = [
    { label: "PAN", done: Boolean(status?.pan_verified) },
    { label: "Aadhaar", done: Boolean(status?.aadhaar_verified) },
    ...(isBusiness
      ? [{ label: "GST", done: Boolean(status?.gst_verified) }]
      : []),
    { label: "Review", done: Boolean(status?.is_complete) },
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      {steps.map((s, i) => (
        <div key={s.label} className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1 text-xs">
            {s.done ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
            ) : (
              <Circle className="h-3.5 w-3.5 text-muted-foreground" />
            )}
            <span
              className={cn(
                s.done ? "text-foreground" : "text-muted-foreground",
              )}
            >
              {s.label}
            </span>
          </span>
          {i < steps.length - 1 && (
            <span className="text-muted-foreground/50">·</span>
          )}
        </div>
      ))}
    </div>
  );
}

// Buys a phone number from the reseller pool (KYC-gated at the point of
// purchase), charged to the credit balance. Calls go through the shared
// hey-api client; KYC status drives an inline gate/wizard so the "Buy" button
// is never a dead disabled control — an unverified click opens the wizard.
export function PhoneNumbersSection() {
  const { user, getAccessToken, loading: authLoading } = useAuth();
  const [available, setAvailable] = useState<Did[]>([]);
  const [owned, setOwned] = useState<Did[]>([]);
  const [priceInr, setPriceInr] = useState<number | null>(null);
  const [setupSeconds, setSetupSeconds] = useState<number | null>(null);
  const [balanceSeconds, setBalanceSeconds] = useState<number | null>(null);
  const [unlimited, setUnlimited] = useState(false);
  const [kyc, setKyc] = useState<KycStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [buying, setBuying] = useState<number | null>(null);
  const [pendingBuy, setPendingBuy] = useState<Did | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const hasFetched = useRef(false);

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) return;
    hasFetched.current = true;
    void refresh();
    void refreshKyc();
    void refreshBalance();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, user]);

  async function refresh() {
    try {
      const [a, m] = await Promise.all([
        client.get({ url: "/api/v1/telephony/marketplace/numbers" }),
        client.get({ url: "/api/v1/telephony/marketplace/my-numbers" }),
      ]);
      const payload = a.data as NumbersPayload | undefined;
      setAvailable(payload?.numbers ?? []);
      setPriceInr(payload?.price_inr ?? null);
      setSetupSeconds(payload?.setup_seconds ?? null);
      setOwned((m.data as { numbers?: Did[] })?.numbers ?? []);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }

  // KYC status drives the banner + the buy gate. Fetched separately so it can
  // be re-pulled after the wizard closes without re-listing numbers.
  async function refreshKyc() {
    try {
      const token = await getAccessToken();
      setKyc(await getKycStatus(token));
    } catch {
      /* banner falls back to "not started" when status can't be trusted */
    }
  }

  // Credit balance is additive UI — never block numbers on it.
  async function refreshBalance() {
    try {
      const res = await client.get({ url: "/api/v1/billing/balance" });
      const data = res.data as
        | { balance_seconds?: number | null; unlimited?: boolean }
        | undefined;
      setBalanceSeconds(data?.balance_seconds ?? null);
      setUnlimited(Boolean(data?.unlimited));
    } catch {
      /* omit balance gracefully */
    }
  }

  async function buy(did: Did) {
    if (did.did_id == null) return;
    setBuying(did.did_id);
    try {
      const res = await client.post({
        url: "/api/v1/telephony/marketplace/buy",
        body: { did_id: did.did_id },
      });
      if (res.error) {
        const status = res.response?.status;
        const detail = (res.error as { detail?: string })?.detail ?? "";
        if (status === 403 || detail.includes("KYC")) {
          // Backend fail-closed KYC gate — fold the user straight into the
          // wizard rather than a dead-end error.
          toast.error("Complete KYC before buying a number", {
            action: {
              label: "Complete KYC",
              onClick: () => setWizardOpen(true),
            },
          });
        } else if (status === 502 && detail.includes("kyc_status_unavailable")) {
          toast.error(
            "KYC check temporarily unavailable — try again in a minute",
          );
        } else if (detail.includes("not_provisioned")) {
          toast.error(
            "Your telephony account isn't set up yet — contact support",
          );
        } else if (detail.includes("insufficient")) {
          toast.error("Not enough credits — top up first");
        } else {
          toast.error("Couldn't buy this number");
        }
        return;
      }
      toast.success(`Number ${did.did_number} is yours!`);
      await refresh();
      await refreshBalance();
    } catch {
      toast.error("Couldn't buy this number");
    } finally {
      setBuying(null);
    }
  }

  // Point-of-purchase gate: verified → confirm + buy; otherwise open the wizard.
  function onBuyClick(did: Did) {
    if (kycVerified) {
      setPendingBuy(did);
    } else {
      setWizardOpen(true);
    }
  }

  if (loading) {
    return (
      <div className="space-y-5">
        <Skeleton className="h-16 w-full rounded-2xl" />
        <div className="space-y-2">
          <Skeleton className="h-4 w-28" />
          <div className="flex flex-wrap gap-2">
            <Skeleton className="h-8 w-40 rounded-md" />
            <Skeleton className="h-8 w-40 rounded-md" />
          </div>
        </div>
        <div className="space-y-2">
          <Skeleton className="h-4 w-32" />
          <div className="grid gap-2 sm:grid-cols-2">
            <Skeleton className="h-16 w-full rounded-md" />
            <Skeleton className="h-16 w-full rounded-md" />
          </div>
        </div>
      </div>
    );
  }

  const banner = deriveKycBanner(kyc);
  const kycVerified = banner === "verified";
  const isBusiness = kyc?.account_type === "business";
  const costCredits =
    setupSeconds != null ? Math.round(setupSeconds / 60) : null;
  const balanceCredits =
    balanceSeconds != null ? Math.floor(balanceSeconds / 60) : null;

  return (
    <div className="space-y-6">
      {/* ── KYC gate banner (hidden only when verified is handled by a slim row) ── */}
      {banner === "not_started" && (
        <div className="flex flex-col gap-3 rounded-2xl border border-amber-300/60 bg-amber-50 p-4 shadow-[var(--shadow-card)] dark:border-amber-500/30 dark:bg-amber-500/10 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400" />
            <div>
              <p className="text-label font-semibold text-amber-900 dark:text-amber-200">
                Verify your business to activate numbers
              </p>
              <p className="text-sm text-amber-800/80 dark:text-amber-200/70">
                Indian telephony rules require identity verification before you
                can buy a number or place calls.
              </p>
            </div>
          </div>
          <Button
            className="shrink-0 bg-cta text-cta-foreground hover:brightness-[1.04] active:brightness-95"
            onClick={() => setWizardOpen(true)}
          >
            Verify
            <ArrowRight className="ml-2 h-4 w-4" />
          </Button>
        </div>
      )}

      {banner === "pending" && (
        <div className="flex flex-col gap-3 rounded-2xl border border-blue-300/60 bg-blue-50 p-4 shadow-[var(--shadow-card)] dark:border-blue-500/30 dark:bg-blue-500/10">
          <div className="flex items-start gap-3">
            <Clock className="mt-0.5 h-5 w-5 shrink-0 text-blue-600 dark:text-blue-400" />
            <div className="space-y-2">
              <div>
                <p className="text-label font-semibold text-blue-900 dark:text-blue-200">
                  Under review — typically 24–72h
                </p>
                <p className="text-sm text-blue-800/80 dark:text-blue-200/70">
                  Your documents are with our telephony partner. We&apos;ll
                  activate numbers as soon as it clears.
                </p>
              </div>
              <KycStepper status={kyc} isBusiness={isBusiness} />
            </div>
          </div>
        </div>
      )}

      {banner === "rejected" && (
        <div className="flex flex-col gap-3 rounded-2xl border border-red-300/60 bg-red-50 p-4 shadow-[var(--shadow-card)] dark:border-red-500/30 dark:bg-red-500/10 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-red-600 dark:text-red-400" />
            <div>
              <p className="text-label font-semibold text-red-900 dark:text-red-200">
                {failingDoc(kyc, isBusiness)} verification needs attention
              </p>
              <p className="text-sm text-red-800/80 dark:text-red-200/70">
                Re-upload the document to continue, or request a manual review.
              </p>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button
              variant="outline"
              className="border-red-300 text-red-700 hover:bg-red-100 dark:border-red-500/40 dark:text-red-200 dark:hover:bg-red-500/20"
              onClick={() => setWizardOpen(true)}
            >
              Re-upload
            </Button>
            <Button
              variant="ghost"
              className="text-red-700 hover:bg-red-100 dark:text-red-200 dark:hover:bg-red-500/20"
              onClick={() => {
                // TODO(support): replace mailto with a real manual-review
                // endpoint once available on the backend.
                if (typeof window !== "undefined") {
                  window.location.href = `mailto:${MANUAL_REVIEW_EMAIL}?subject=${encodeURIComponent(
                    "KYC manual review request",
                  )}`;
                }
                toast.message("We'll review manually", {
                  description:
                    "Send us your details and our team will verify by hand.",
                });
              }}
            >
              Request manual review
            </Button>
          </div>
        </div>
      )}

      {banner === "verified" && (
        <div className="flex items-center gap-2 rounded-xl border border-emerald-300/60 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300">
          <ShieldCheck className="h-4 w-4 shrink-0" />
          <span className="font-medium">Business verified</span>
          <CheckCircle2 className="h-4 w-4" />
        </div>
      )}

      {/* ── Your numbers ── */}
      <div className="space-y-2">
        <p className="text-sm font-medium">Your numbers</p>
        {owned.length === 0 ? (
          <EmptyState
            icon={PhoneOff}
            title="No numbers yet"
            description="Buy one below to start placing outbound calls."
          />
        ) : (
          <div className="divide-y rounded-2xl border border-border/60">
            {owned.map((d) => (
              <div
                key={d.did_id ?? `local-${d.did_number}`}
                className="flex flex-wrap items-center justify-between gap-3 p-3"
              >
                <div className="flex items-center gap-2">
                  <Phone className="h-4 w-4 text-muted-foreground" />
                  <span className="font-mono text-sm">{d.did_number}</span>
                  {/* TODO: `telephony_phone_numbers.label` is not exposed by
                      /my-numbers and has no PATCH endpoint yet — render the
                      provider's type label read-only until one exists. */}
                  {d.type_label ? (
                    <span className="text-xs text-muted-foreground">
                      {d.type_label}
                    </span>
                  ) : null}
                </div>
                <Badge
                  variant="secondary"
                  className={cn(
                    "gap-1",
                    kycVerified
                      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300"
                      : "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300",
                  )}
                >
                  {kycVerified ? (
                    <CheckCircle2 className="h-3 w-3" />
                  ) : (
                    <Clock className="h-3 w-3" />
                  )}
                  {kycVerified ? "Compliant" : "Pending"}
                </Badge>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Available to buy ── */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <p className="text-sm font-medium">Available to buy</p>
          <p className="text-xs text-muted-foreground">
            {costCredits != null && (
              <>Cost per number: ~{costCredits} credits</>
            )}
            {costCredits != null && priceInr != null && ` · ₹${priceInr}`}
            {(unlimited || balanceCredits != null) && (
              <>
                {costCredits != null || priceInr != null ? " · " : ""}
                Your balance:{" "}
                {unlimited
                  ? "Unlimited"
                  : `${balanceCredits?.toLocaleString()} credits`}
              </>
            )}
          </p>
        </div>
        {available.length === 0 ? (
          <EmptyState
            icon={Phone}
            title="No numbers available right now"
            description="Check back soon — the pool refills regularly."
          />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {available.map((d) => (
              <div
                key={d.did_id ?? `pool-${d.did_number}`}
                className="flex items-center justify-between rounded-xl border border-border/60 p-3"
              >
                <div>
                  <p className="font-mono text-sm">{d.did_number}</p>
                  <p className="text-xs text-muted-foreground">
                    {d.type_label ? `${d.type_label} · ` : ""}
                    {priceInr != null ? `₹${priceInr}` : ""}
                    {costCredits != null ? ` · ~${costCredits} credits` : ""}
                  </p>
                </div>
                <Button
                  size="sm"
                  disabled={buying === d.did_id}
                  onClick={() => onBuyClick(d)}
                >
                  {buying === d.did_id ? "Buying..." : "Buy"}
                </Button>
              </div>
            ))}
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          {kycVerified
            ? "Charged to your call-credit balance."
            : "Requires completed KYC. Charged to your call-credit balance."}
        </p>
      </div>

      {/* ── Buy confirmation (verified path only) ── */}
      <AlertDialog
        open={pendingBuy !== null}
        onOpenChange={(open) => {
          if (!open) setPendingBuy(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Buy {pendingBuy?.did_number}
              {priceInr != null ? ` for ₹${priceInr}` : ""}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              {costCredits != null
                ? `${costCredits} credits will be deducted from your call-credit balance.`
                : "The number will be charged to your call-credit balance."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                const did = pendingBuy;
                setPendingBuy(null);
                if (did) void buy(did);
              }}
            >
              Buy number
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* ── Inline KYC wizard (point-of-purchase gate) ── */}
      <Dialog
        open={wizardOpen}
        onOpenChange={(open) => {
          setWizardOpen(open);
          // Re-pull status on close so the banner reflects any progress made.
          if (!open) void refreshKyc();
        }}
      >
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Verify your business</DialogTitle>
            <DialogDescription>
              Complete KYC to activate numbers and calling. Indian telephony
              regulations require identity verification.
            </DialogDescription>
          </DialogHeader>
          <KycWizard
            compact
            onComplete={() => {
              setWizardOpen(false);
              void refreshKyc();
              void refresh();
            }}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
