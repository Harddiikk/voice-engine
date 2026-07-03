"use client";

import { Phone } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { client } from "@/client/client.gen";
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
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";

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

// Buys a phone number from the reseller pool (KYC-gated on the backend),
// charged to the credit balance. Calls go through the shared hey-api client.
export function PhoneNumbersSection() {
  const { user, loading: authLoading } = useAuth();
  const router = useRouter();
  const [available, setAvailable] = useState<Did[]>([]);
  const [owned, setOwned] = useState<Did[]>([]);
  const [priceInr, setPriceInr] = useState<number | null>(null);
  const [setupSeconds, setSetupSeconds] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [buying, setBuying] = useState<number | null>(null);
  const [pendingBuy, setPendingBuy] = useState<Did | null>(null);
  const hasFetched = useRef(false);

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) return;
    hasFetched.current = true;
    refresh();
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
      setOwned(((m.data as { numbers?: Did[] })?.numbers) ?? []);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
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
          toast.error("Complete KYC before buying a number", {
            action: {
              label: "Complete KYC",
              onClick: () => router.push("/kyc"),
            },
          });
        } else if (status === 502 && detail.includes("kyc_status_unavailable")) {
          toast.error("KYC check temporarily unavailable — try again in a minute");
        } else if (detail.includes("not_provisioned")) {
          toast.error("Your telephony account isn't set up yet — contact support");
        } else if (detail.includes("insufficient")) {
          toast.error("Not enough credits — top up first");
        } else {
          toast.error("Couldn't buy this number");
        }
        return;
      }
      toast.success(`Number ${did.did_number} is yours!`);
      await refresh();
    } catch {
      toast.error("Couldn't buy this number");
    } finally {
      setBuying(null);
    }
  }

  if (loading) {
    return (
      <div className="space-y-5">
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

  const credits = setupSeconds != null ? Math.round(setupSeconds / 60) : null;

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <p className="text-sm font-medium">Your numbers</p>
        {owned.length === 0 ? (
          <p className="text-sm text-muted-foreground">You don&apos;t own any numbers yet.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {owned.map((d) => (
              <span
                key={d.did_id ?? `local-${d.did_number}`}
                className="inline-flex items-center gap-1 rounded-md border px-3 py-1 text-sm"
              >
                <Phone className="h-3.5 w-3.5" /> {d.did_number}
                {d.type_label ? (
                  <span className="text-muted-foreground">· {d.type_label}</span>
                ) : null}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-2">
        <p className="text-sm font-medium">Available to buy</p>
        {available.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No numbers available right now — check back soon.
          </p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {available.map((d) => (
              <div
                key={d.did_id ?? `pool-${d.did_number}`}
                className="flex items-center justify-between rounded-md border p-3"
              >
                <div>
                  <p className="font-mono text-sm">{d.did_number}</p>
                  <p className="text-xs text-muted-foreground">
                    {d.type_label ? `${d.type_label} · ` : ""}
                    {priceInr != null ? `₹${priceInr}` : ""}
                  </p>
                </div>
                <Button
                  size="sm"
                  disabled={buying === d.did_id}
                  onClick={() => setPendingBuy(d)}
                >
                  {buying === d.did_id ? "Buying..." : "Buy"}
                </Button>
              </div>
            ))}
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          Requires completed KYC. Charged to your call-credit balance.
        </p>
      </div>

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
              {credits != null
                ? `${credits} credits will be deducted from your call-credit balance.`
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
    </div>
  );
}
