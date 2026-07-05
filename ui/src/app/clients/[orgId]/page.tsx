"use client";

import {
  ArrowLeft,
  Coins,
  Copy,
  ExternalLink,
  Eye,
  KeyRound,
  Loader2,
  Phone,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  UserPlus,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

import { KycStatusBadge, PlanBadge, SuspendedBadge } from "@/components/admin/AdminBadges";
import {
  formatCredits,
  formatInr,
  formatMoneyBalance,
  formatTimestamp,
  planLabel,
} from "@/components/admin/adminFormat";
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
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  addClientNote,
  ADMIN_PLANS,
  type AdminAuditEntry,
  type AdminClientDetail,
  type AdminClientKycStatus,
  type AdminProfilePatch,
  assignDidToClient,
  chargeSetupFee,
  type ClientPasswordResult,
  createClientForOrg,
  getAdminClientDetail,
  getClientKycStatus,
  getClientPassword,
  grantCreditsToClient,
  listAdminAudit,
  NO_STORED_PASSWORD,
  recordClientPassword,
  retryProvisionClient,
  setClientCredits,
  updateAdminProfile,
} from "@/lib/adminClients";
import { useAuth } from "@/lib/auth";
import { impersonateAsSuperadmin } from "@/lib/utils";

const DERIVED = "__derived__";

/** A label/value row used across the overview cards. */
function InfoRow({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-right text-sm font-medium">{children}</span>
    </div>
  );
}

function auditDetailText(detail: unknown): string {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}

export default function ClientDetailPage() {
  const { user, getAccessToken, loading: authLoading } = useAuth();
  const params = useParams();
  const orgId = Number(params.orgId);
  const validOrg = Number.isInteger(orgId) && orgId > 0;

  const hasFetched = useRef(false);

  const [detail, setDetail] = useState<AdminClientDetail | null>(null);
  const [audit, setAudit] = useState<AdminAuditEntry[]>([]);
  const [kyc, setKyc] = useState<AdminClientKycStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [savingPlan, setSavingPlan] = useState(false);
  const [kycLoading, setKycLoading] = useState(false);

  // Grant credits dialog
  const [grantOpen, setGrantOpen] = useState(false);
  const [grantMinutes, setGrantMinutes] = useState("");

  // Set balance dialog (sets the exact balance, up or down; by ₹ or minutes)
  const [setBalOpen, setSetBalOpen] = useState(false);
  const [setBalMinutes, setSetBalMinutes] = useState("");
  const [setBalRupees, setSetBalRupees] = useState("");
  const [setBalSource, setSetBalSource] = useState<"minutes" | "rupees">(
    "minutes",
  );

  // Show-Dograh-voice toggle (per-client; default = Gemini voices only)
  const [savingDograhToggle, setSavingDograhToggle] = useState(false);

  // Per-client Gemini key override (blank = use shared platform key)
  const [geminiKeyInput, setGeminiKeyInput] = useState("");
  const [savingGeminiKey, setSavingGeminiKey] = useState(false);

  // Plan-card designer (what the client sees on their Credits page)
  const [planTitle, setPlanTitle] = useState("");
  const [planPrice, setPlanPrice] = useState("");
  const [planMinutes, setPlanMinutes] = useState("");
  const [planFeatures, setPlanFeatures] = useState("");
  const [planExpiry, setPlanExpiry] = useState("");
  const [savingPlanCard, setSavingPlanCard] = useState(false);

  // Pricing form
  const [perMinute, setPerMinute] = useState("");
  const [numberPrice, setNumberPrice] = useState("");
  const [setupFee, setSetupFee] = useState("");

  // Charge setup fee confirm
  const [chargeOpen, setChargeOpen] = useState(false);

  // Notes
  const [noteText, setNoteText] = useState("");

  // Suspend confirm
  const [suspendOpen, setSuspendOpen] = useState(false);

  // VoiceLink: assign DID
  const [assignOpen, setAssignOpen] = useState(false);
  const [didNumber, setDidNumber] = useState("");
  const [assignClientId, setAssignClientId] = useState("");

  // VoiceLink: retry provision
  const [retryOpen, setRetryOpen] = useState(false);
  const [retryPassword, setRetryPassword] = useState("");
  const [creating, setCreating] = useState(false);

  // VoiceLink: password reveal / record
  const [revealed, setRevealed] = useState<ClientPasswordResult | null>(null);
  const [revealLoading, setRevealLoading] = useState(false);
  const [recordOpen, setRecordOpen] = useState(false);
  const [recordPassword, setRecordPassword] = useState("");

  const getToken = useCallback(async () => {
    const token = await getAccessToken();
    if (!token) throw new Error("Missing access token");
    return token;
  }, [getAccessToken]);

  const fetchAll = useCallback(
    async (showSpinner = false) => {
      if (!validOrg) {
        setError("Invalid organization id");
        setLoading(false);
        return;
      }
      if (showSpinner) setRefreshing(true);
      try {
        const token = await getToken();
        const [d, a] = await Promise.all([
          getAdminClientDetail(token, orgId),
          listAdminAudit(token, orgId, 8).catch(
            () => [] as AdminAuditEntry[],
          ),
        ]);
        setDetail(d);
        setAudit(a);
        setError(null);
        // KYC returns a well-formed status (no_client / disabled) rather than
        // throwing, but tolerate failure so the page still renders.
        try {
          setKyc(await getClientKycStatus(token, orgId));
        } catch {
          /* leave prior KYC state */
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load client");
      } finally {
        setLoading(false);
        if (showSpinner) setRefreshing(false);
      }
    },
    [getToken, orgId, validOrg],
  );

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) return;
    hasFetched.current = true;
    fetchAll();
  }, [authLoading, user, fetchAll]);

  // Derived pricing state -----------------------------------------------------
  const pricing = detail?.pricing;
  const custom = pricing?.custom ?? {};
  const initialPerMinute = useMemo(
    () =>
      custom.per_minute_inr && pricing?.per_minute_inr != null
        ? String(pricing.per_minute_inr)
        : "",
    [custom.per_minute_inr, pricing?.per_minute_inr],
  );
  const initialNumberPrice = useMemo(
    () =>
      custom.number_price_inr && pricing?.number_price_inr != null
        ? String(pricing.number_price_inr)
        : "",
    [custom.number_price_inr, pricing?.number_price_inr],
  );
  const initialSetupFee = useMemo(
    () =>
      custom.setup_fee_inr && pricing?.setup_fee_inr != null
        ? String(pricing.setup_fee_inr)
        : "",
    [custom.setup_fee_inr, pricing?.setup_fee_inr],
  );

  // Re-sync pricing inputs whenever the detail (and therefore the overrides)
  // changes — including after a save reloads the record.
  useEffect(() => {
    setPerMinute(initialPerMinute);
    setNumberPrice(initialNumberPrice);
    setSetupFee(initialSetupFee);
  }, [initialPerMinute, initialNumberPrice, initialSetupFee]);

  const copyToClipboard = async (value: string, label: string) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(`${label} copied to clipboard`);
    } catch {
      toast.error("Failed to copy to clipboard");
    }
  };

  // Mutations -----------------------------------------------------------------
  const grantMinutesNumber = Number(grantMinutes);
  const grantMinutesValid =
    Number.isInteger(grantMinutesNumber) &&
    grantMinutesNumber >= 1 &&
    grantMinutesNumber <= 100000;

  const onGrantCredits = async () => {
    if (!grantMinutesValid) return;
    setSubmitting(true);
    try {
      const token = await getToken();
      const result = await grantCreditsToClient(token, orgId, grantMinutesNumber);
      toast.success(
        `Granted ${grantMinutesNumber} minute${grantMinutesNumber === 1 ? "" : "s"} — balance ${formatCredits(result.credits_seconds_remaining)}`,
      );
      setGrantOpen(false);
      setGrantMinutes("");
      await fetchAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to grant credits");
    } finally {
      setSubmitting(false);
    }
  };

  // Per-minute rate for the ₹⇄minutes conversion (0/absent = ₹ input disabled).
  const setBalRate = detail?.money?.per_minute_inr ?? 0;

  // Linked inputs: editing one derives the other via the rate. The last-edited
  // field is the "source" sent to the backend (server does the authoritative
  // conversion for ₹).
  const onSetBalMinutesChange = (v: string) => {
    setSetBalSource("minutes");
    setSetBalMinutes(v);
    const n = Number(v);
    if (v.trim() === "") setSetBalRupees("");
    else if (setBalRate > 0 && Number.isFinite(n))
      setSetBalRupees(String(Math.round(n * setBalRate * 100) / 100));
  };
  const onSetBalRupeesChange = (v: string) => {
    setSetBalSource("rupees");
    setSetBalRupees(v);
    const n = Number(v);
    if (v.trim() === "") setSetBalMinutes("");
    else if (setBalRate > 0 && Number.isFinite(n))
      setSetBalMinutes(String(Math.round(n / setBalRate)));
  };

  const setBalValid =
    setBalSource === "rupees"
      ? setBalRupees.trim() !== "" &&
        Number.isFinite(Number(setBalRupees)) &&
        Number(setBalRupees) >= 0 &&
        Number(setBalRupees) <= 10_000_000
      : setBalMinutes.trim() !== "" &&
        Number.isInteger(Number(setBalMinutes)) &&
        Number(setBalMinutes) >= 0 &&
        Number(setBalMinutes) <= 100_000;

  const onSetCredits = async () => {
    if (!setBalValid) return;
    setSubmitting(true);
    try {
      const token = await getToken();
      const amount =
        setBalSource === "rupees"
          ? { rupees: Number(setBalRupees) }
          : { minutes: Number(setBalMinutes) };
      const result = await setClientCredits(token, orgId, amount);
      toast.success(`Balance set — now ${formatCredits(result.credits_seconds_remaining)}`);
      setSetBalOpen(false);
      setSetBalMinutes("");
      setSetBalRupees("");
      await fetchAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to set balance");
    } finally {
      setSubmitting(false);
    }
  };

  // Sync the plan-card form whenever the detail reloads.
  useEffect(() => {
    const card = detail?.plan_card;
    setPlanTitle(card?.title ?? "");
    setPlanPrice(card?.price_inr != null ? String(card.price_inr) : "");
    setPlanMinutes(
      card?.included_minutes != null ? String(card.included_minutes) : "",
    );
    setPlanFeatures((card?.features ?? []).join("\n"));
    setPlanExpiry(
      detail?.plan_expires_at ? detail.plan_expires_at.slice(0, 10) : "",
    );
  }, [detail?.plan_card, detail?.plan_expires_at]);

  const planPriceNumber = Number(planPrice);
  const planCardValid =
    planTitle.trim().length > 0 &&
    Number.isFinite(planPriceNumber) &&
    planPriceNumber > 0 &&
    (planMinutes.trim() === "" ||
      (Number.isInteger(Number(planMinutes)) && Number(planMinutes) >= 0));

  const onSavePlanCard = async () => {
    if (!planCardValid) return;
    setSavingPlanCard(true);
    try {
      const token = await getToken();
      await updateAdminProfile(token, orgId, {
        plan_card: {
          title: planTitle.trim(),
          price_inr: planPriceNumber,
          included_minutes:
            planMinutes.trim() === "" ? 0 : Number(planMinutes),
          features: planFeatures
            .split("\n")
            .map((f) => f.trim())
            .filter(Boolean),
          enabled: true,
        },
      });
      toast.success("Plan card saved — the client now sees this card");
      await fetchAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save plan card");
    } finally {
      setSavingPlanCard(false);
    }
  };

  const onRemovePlanCard = async () => {
    setSavingPlanCard(true);
    try {
      const token = await getToken();
      await updateAdminProfile(token, orgId, { plan_card: null });
      toast.success("Plan card removed — the client sees the standard Credits page");
      await fetchAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to remove plan card");
    } finally {
      setSavingPlanCard(false);
    }
  };

  const onSavePlanExpiry = async () => {
    setSavingPlanCard(true);
    try {
      const token = await getToken();
      await updateAdminProfile(token, orgId, {
        plan_expires_at: planExpiry
          ? new Date(`${planExpiry}T23:59:59Z`).toISOString()
          : null,
      });
      toast.success(
        planExpiry ? `Plan expiry set to ${planExpiry}` : "Plan expiry cleared",
      );
      await fetchAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to set expiry");
    } finally {
      setSavingPlanCard(false);
    }
  };

  const onSaveGeminiKey = async (clear = false) => {
    const key = clear ? "" : geminiKeyInput.trim();
    if (!clear && !key) return;
    setSavingGeminiKey(true);
    try {
      const token = await getToken();
      await updateAdminProfile(token, orgId, { gemini_api_key: key });
      toast.success(
        clear
          ? "Custom Gemini key cleared — using the shared platform key"
          : "Custom Gemini key saved for this client",
      );
      setGeminiKeyInput("");
      await fetchAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save Gemini key");
    } finally {
      setSavingGeminiKey(false);
    }
  };

  const onToggleDograhVoice = async (show: boolean) => {
    setSavingDograhToggle(true);
    try {
      const token = await getToken();
      await updateAdminProfile(token, orgId, { show_dograh_voice: show });
      toast.success(
        show
          ? "Dograh voice enabled for this client"
          : "Dograh voice hidden — Gemini voices only",
      );
      await fetchAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update voice option");
    } finally {
      setSavingDograhToggle(false);
    }
  };

  const onChangePlan = async (value: string) => {
    const plan_override = value === DERIVED ? null : value;
    setSavingPlan(true);
    try {
      const token = await getToken();
      await updateAdminProfile(token, orgId, { plan_override });
      toast.success(
        plan_override
          ? `Plan override set to ${planLabel(plan_override)}`
          : "Plan override cleared — now derived",
      );
      await fetchAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to change plan");
    } finally {
      setSavingPlan(false);
    }
  };

  const pricingDirty =
    perMinute !== initialPerMinute ||
    numberPrice !== initialNumberPrice ||
    setupFee !== initialSetupFee;

  const onSavePricing = async () => {
    // For each field: unchanged -> skip; blank -> null (reset to default);
    // otherwise a validated non-negative number. `invalid` aborts the save.
    const parseField = (
      val: string,
      init: string,
    ): { skip: boolean; value: number | null; invalid: boolean } => {
      if (val === init) return { skip: true, value: null, invalid: false };
      if (val.trim() === "") return { skip: false, value: null, invalid: false };
      const n = Number(val);
      if (!Number.isFinite(n) || n < 0)
        return { skip: false, value: null, invalid: true };
      return { skip: false, value: n, invalid: false };
    };
    const pm = parseField(perMinute, initialPerMinute);
    const np = parseField(numberPrice, initialNumberPrice);
    const sf = parseField(setupFee, initialSetupFee);
    if (pm.invalid || np.invalid || sf.invalid) {
      toast.error("Prices must be non-negative numbers");
      return;
    }
    const patch: AdminProfilePatch = {};
    if (!pm.skip) patch.per_minute_inr = pm.value;
    if (!np.skip) patch.number_price_inr = np.value;
    if (!sf.skip) patch.setup_fee_inr = sf.value;
    if (Object.keys(patch).length === 0) return;
    setSubmitting(true);
    try {
      const token = await getToken();
      await updateAdminProfile(token, orgId, patch);
      toast.success("Custom pricing saved");
      await fetchAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save pricing");
    } finally {
      setSubmitting(false);
    }
  };

  const onChargeSetupFee = async () => {
    setSubmitting(true);
    try {
      const token = await getToken();
      const result = await chargeSetupFee(token, orgId);
      toast.success(
        result.balance != null
          ? `Setup fee charged — balance ${formatInr(result.balance)}`
          : "Setup fee charged",
      );
      setChargeOpen(false);
      await fetchAll();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to charge setup fee",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const onAddNote = async () => {
    const text = noteText.trim();
    if (!text) return;
    setSubmitting(true);
    try {
      const token = await getToken();
      const notes = await addClientNote(token, orgId, text);
      setDetail((prev) => (prev ? { ...prev, notes } : prev));
      setNoteText("");
      toast.success("Note added");
      // Refresh audit too (adding a note is an auditable action).
      listAdminAudit(token, orgId, 8)
        .then(setAudit)
        .catch(() => {});
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to add note");
    } finally {
      setSubmitting(false);
    }
  };

  const onToggleSuspend = async () => {
    const next = !detail?.suspended;
    setSubmitting(true);
    try {
      const token = await getToken();
      await updateAdminProfile(token, orgId, { suspended: next });
      toast.success(next ? "Client suspended" : "Client unsuspended");
      setSuspendOpen(false);
      await fetchAll();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to update suspension",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const onAssignDid = async () => {
    if (!didNumber.trim()) return;
    setSubmitting(true);
    try {
      const token = await getToken();
      const result = await assignDidToClient(token, orgId, {
        did_number: didNumber.trim(),
        ...(assignClientId.trim() ? { client_id: assignClientId.trim() } : {}),
      });
      toast.success(
        result.created
          ? "VoiceLink telephony configuration created with the DID"
          : "DID updated on the existing VoiceLink configuration",
      );
      setAssignOpen(false);
      await fetchAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to assign DID");
    } finally {
      setSubmitting(false);
    }
  };

  const onRetryProvision = async () => {
    if (retryPassword.length < 8) return;
    setSubmitting(true);
    try {
      const token = await getToken();
      const result = await retryProvisionClient(token, orgId, retryPassword);
      if (result.voicelink_status === "provisioned") {
        toast.success("VoiceLink client provisioned");
      } else {
        toast.error(result.voicelink_error || "Provisioning is still pending");
      }
      setRetryOpen(false);
      setRetryPassword("");
      await fetchAll();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to retry provisioning",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const onCreateVoiceLink = async () => {
    setCreating(true);
    try {
      const token = await getToken();
      const result = await createClientForOrg(token, orgId);
      if (result.voicelink_status === "provisioned") {
        toast.success(
          result.action === "linked"
            ? "Linked the existing VoiceLink client"
            : "VoiceLink client created",
        );
      } else {
        toast.error(result.voicelink_error || "Provisioning is still pending");
      }
      await fetchAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create client");
    } finally {
      setCreating(false);
    }
  };

  const onRevealPassword = async () => {
    setRevealLoading(true);
    try {
      const token = await getToken();
      setRevealed(await getClientPassword(token, orgId));
    } catch (err) {
      if (err instanceof Error && err.message === NO_STORED_PASSWORD) {
        toast.error("No password stored for this client");
      } else {
        toast.error(
          err instanceof Error ? err.message : "Failed to fetch the password",
        );
      }
    } finally {
      setRevealLoading(false);
    }
  };

  const onRecordPassword = async () => {
    if (recordPassword.length < 8) return;
    setSubmitting(true);
    try {
      const token = await getToken();
      await recordClientPassword(token, orgId, recordPassword);
      toast.success(
        "Portal password recorded — this does not change it on VoiceLink",
      );
      setRecordOpen(false);
      setRecordPassword("");
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to record the password",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const onCheckKyc = async () => {
    setKycLoading(true);
    try {
      const token = await getToken();
      setKyc(await getClientKycStatus(token, orgId));
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to fetch KYC status",
      );
    } finally {
      setKycLoading(false);
    }
  };

  const onImpersonate = async () => {
    const providerId = detail?.owner_provider_id;
    if (!providerId) {
      toast.error("This organization has no owner user to impersonate");
      return;
    }
    try {
      const token = await getToken();
      await impersonateAsSuperadmin({
        accessToken: token,
        providerUserId: providerId,
        redirectPath: "/model-configurations",
        openInNewTab: true,
      });
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to impersonate user",
      );
    }
  };

  // Render --------------------------------------------------------------------
  const money = detail?.money;
  const moneyUnlimited = money?.unlimited === true || money?.money_left_inr === null;
  const planOverridden = detail?.plan_override != null;
  const vlStatus = detail?.voicelink?.status ?? null;
  const vlActive = vlStatus === "provisioned" || vlStatus === "active";
  const notes = detail?.notes ?? [];

  return (
    <div className="min-h-screen bg-background">
      <div className="stagger container mx-auto max-w-5xl px-4 py-10">
        <Link
          href="/clients"
          className="mb-6 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> Back to clients
        </Link>

        {loading ? (
          <div className="space-y-4">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-9 w-80" />
            <Skeleton className="h-64 w-full" />
          </div>
        ) : error ? (
          <div className="flex flex-col items-center gap-3 rounded-2xl border border-border/60 bg-card px-6 py-16 text-center shadow-[var(--shadow-card)]">
            <p className="text-label text-foreground">Could not load client</p>
            <p className="text-body max-w-sm text-muted-foreground">{error}</p>
            <Button variant="outline" size="sm" onClick={() => fetchAll(true)}>
              <RefreshCw className="mr-2 h-4 w-4" /> Retry
            </Button>
          </div>
        ) : detail ? (
          <>
            {/* Header */}
            <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <p className="text-eyebrow text-primary">
                  Client #{detail.organization_id}
                </p>
                <h1 className="text-h1 mt-1">{detail.organization_name}</h1>
                <p className="text-body mt-1 text-muted-foreground">
                  {detail.owner_email ?? "No owner email"}
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <PlanBadge plan={detail.plan} overridden={planOverridden} />
                  <SuspendedBadge suspended={detail.suspended} />
                  {detail.features?.api && <Badge variant="outline">API</Badge>}
                  {detail.features?.mcp && <Badge variant="outline">MCP</Badge>}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={onImpersonate}
                  disabled={!detail.owner_provider_id}
                >
                  <ExternalLink className="mr-2 h-4 w-4" /> Impersonate
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => fetchAll(true)}
                  disabled={refreshing}
                >
                  <RefreshCw
                    className={`mr-2 h-4 w-4 ${refreshing ? "animate-spin" : ""}`}
                  />
                  Refresh
                </Button>
              </div>
            </div>

            <Tabs defaultValue="overview">
              <TabsList className="mb-4">
                <TabsTrigger value="overview">Overview</TabsTrigger>
                <TabsTrigger value="billing">Billing</TabsTrigger>
                <TabsTrigger value="voicelink">VoiceLink</TabsTrigger>
                <TabsTrigger value="notes">
                  Notes{notes.length ? ` (${notes.length})` : ""}
                </TabsTrigger>
                <TabsTrigger value="danger">Danger</TabsTrigger>
              </TabsList>

              {/* OVERVIEW ------------------------------------------------- */}
              <TabsContent value="overview">
                <div className="grid gap-4 md:grid-cols-2">
                  <Card>
                    <CardHeader>
                      <CardTitle>Identity</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <InfoRow label="Organization">
                        #{detail.organization_id}
                      </InfoRow>
                      <InfoRow label="Name">{detail.organization_name}</InfoRow>
                      <InfoRow label="Owner">
                        {detail.owner_email ?? "—"}
                      </InfoRow>
                      <InfoRow label="Plan">
                        <PlanBadge
                          plan={detail.plan}
                          overridden={planOverridden}
                        />
                      </InfoRow>
                      <InfoRow label="Features">
                        {detail.features?.api || detail.features?.mcp ? (
                          <span className="flex justify-end gap-1.5">
                            {detail.features?.api && (
                              <Badge variant="outline">API</Badge>
                            )}
                            {detail.features?.mcp && (
                              <Badge variant="outline">MCP</Badge>
                            )}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">None</span>
                        )}
                      </InfoRow>
                      <InfoRow label="Status">
                        <SuspendedBadge suspended={detail.suspended} />
                      </InfoRow>
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader>
                      <CardTitle>Money</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <InfoRow label="Balance">
                        {moneyUnlimited ? (
                          <span className="text-muted-foreground">Unlimited</span>
                        ) : (
                          formatMoneyBalance(money?.money_left_inr)
                        )}
                      </InfoRow>
                      <InfoRow label="Spent">
                        {formatInr(money?.money_spent_inr)}
                      </InfoRow>
                      <InfoRow label="Balance (minutes)">
                        {moneyUnlimited
                          ? "Unlimited"
                          : formatCredits(money?.balance_seconds)}
                      </InfoRow>
                      <InfoRow label="Per-minute rate">
                        {formatInr(money?.per_minute_inr)}
                      </InfoRow>
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader>
                      <CardTitle>KYC</CardTitle>
                    </CardHeader>
                    <CardContent>
                      {kyc ? (
                        <InfoRow label="Status">
                          <KycStatusBadge status={kyc} />
                        </InfoRow>
                      ) : (
                        <p className="text-sm text-muted-foreground">
                          KYC status unavailable.
                        </p>
                      )}
                      {detail.kyc?.account_type && (
                        <InfoRow label="Account type">
                          {detail.kyc.account_type}
                        </InfoRow>
                      )}
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader>
                      <CardTitle>VoiceLink</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <InfoRow label="Status">
                        {vlStatus ? (
                          <Badge
                            className={
                              vlActive
                                ? "bg-emerald-600 hover:bg-emerald-600"
                                : undefined
                            }
                            variant={vlActive ? undefined : "secondary"}
                          >
                            {vlStatus}
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </InfoRow>
                      <InfoRow label="Client ID">
                        <span className="font-mono tabular-nums">
                          {detail.voicelink?.client_id ?? "—"}
                        </span>
                      </InfoRow>
                      <InfoRow label="Username">
                        {detail.voicelink?.username ?? "—"}
                      </InfoRow>
                      <InfoRow label="DID">
                        <span className="font-mono tabular-nums">
                          {detail.voicelink?.did_number ?? "—"}
                        </span>
                      </InfoRow>
                    </CardContent>
                  </Card>

                  {detail.usage && (
                    <Card>
                      <CardHeader>
                        <CardTitle>Usage</CardTitle>
                      </CardHeader>
                      <CardContent>
                        <InfoRow label="Total calls">
                          {detail.usage.total_calls ?? "—"}
                        </InfoRow>
                        <InfoRow label="Total minutes">
                          {detail.usage.total_minutes ?? "—"}
                        </InfoRow>
                      </CardContent>
                    </Card>
                  )}
                </div>
              </TabsContent>

              {/* BILLING -------------------------------------------------- */}
              <TabsContent value="billing">
                <div className="grid gap-4 md:grid-cols-2">
                  <Card>
                    <CardHeader>
                      <CardTitle>Credits</CardTitle>
                      <CardDescription>
                        Current balance:{" "}
                        {moneyUnlimited
                          ? "Unlimited"
                          : formatMoneyBalance(money?.money_left_inr)}{" "}
                        ·{" "}
                        {moneyUnlimited
                          ? "unmetered"
                          : formatCredits(money?.balance_seconds)}
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          onClick={() => {
                            setGrantMinutes("");
                            setGrantOpen(true);
                          }}
                          disabled={moneyUnlimited}
                        >
                          <Coins className="mr-2 h-4 w-4" /> Grant credits
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => {
                            setSetBalSource("minutes");
                            setSetBalMinutes(
                              money?.balance_seconds != null
                                ? String(
                                    Math.floor(money.balance_seconds / 60),
                                  )
                                : "",
                            );
                            setSetBalRupees(
                              money?.money_left_inr != null
                                ? String(money.money_left_inr)
                                : "",
                            );
                            setSetBalOpen(true);
                          }}
                          disabled={moneyUnlimited}
                        >
                          Set balance
                        </Button>
                      </div>
                      {moneyUnlimited && (
                        <p className="mt-2 text-xs text-muted-foreground">
                          This org is unmetered — granting credits would start
                          metering it.
                        </p>
                      )}
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader>
                      <CardTitle>Plan</CardTitle>
                      <CardDescription>
                        {planOverridden
                          ? `Override active: ${planLabel(detail.plan_override)}`
                          : `Derived: ${planLabel(detail.plan)}`}
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-2">
                      <Select
                        value={detail.plan_override ?? DERIVED}
                        onValueChange={onChangePlan}
                        disabled={savingPlan}
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value={DERIVED}>
                            Derived (no override)
                          </SelectItem>
                          {ADMIN_PLANS.map((p) => (
                            <SelectItem key={p} value={p}>
                              {planLabel(p)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <p className="text-xs text-muted-foreground">
                        Selecting a plan sets a per-client override; choose
                        &ldquo;Derived&rdquo; to clear it.
                      </p>
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader>
                      <CardTitle>Voice options</CardTitle>
                      <CardDescription>
                        By default clients see only the Gemini voices in the
                        model/voice editor. Turn this on to also show the Dograh
                        managed voice + BYOK for this client.
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <div className="flex items-center justify-between gap-4">
                        <Label htmlFor="show-dograh-voice" className="text-sm">
                          Show Dograh voice
                        </Label>
                        <Switch
                          id="show-dograh-voice"
                          checked={detail.show_dograh_voice === true}
                          onCheckedChange={onToggleDograhVoice}
                          disabled={savingDograhToggle}
                        />
                      </div>
                      <p className="mt-2 text-xs text-muted-foreground">
                        {detail.show_dograh_voice
                          ? "This client sees Gemini + Dograh + BYOK."
                          : "This client sees Gemini voices only (all plans)."}
                      </p>

                      <Separator className="my-4" />

                      <Label
                        htmlFor="gemini-key"
                        className="text-sm font-medium"
                      >
                        Gemini API key
                      </Label>
                      <p className="mb-2 mt-1 text-xs text-muted-foreground">
                        {detail.has_gemini_key
                          ? "A custom key is set for this client."
                          : "Using the shared platform Gemini key."}
                      </p>
                      <div className="flex flex-wrap items-center gap-2">
                        <Input
                          id="gemini-key"
                          type="password"
                          autoComplete="off"
                          value={geminiKeyInput}
                          onChange={(e) => setGeminiKeyInput(e.target.value)}
                          placeholder={
                            detail.has_gemini_key
                              ? "Enter a new key to replace"
                              : "Paste a client-specific Gemini key"
                          }
                          className="max-w-xs flex-1"
                        />
                        <Button
                          size="sm"
                          onClick={() => onSaveGeminiKey(false)}
                          disabled={savingGeminiKey || !geminiKeyInput.trim()}
                        >
                          Save
                        </Button>
                        {detail.has_gemini_key && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => onSaveGeminiKey(true)}
                            disabled={savingGeminiKey}
                          >
                            Clear
                          </Button>
                        )}
                      </div>
                    </CardContent>
                  </Card>

                  <Card className="md:col-span-2">
                    <CardHeader>
                      <CardTitle>Client plan card</CardTitle>
                      <CardDescription>
                        Design the plan this client sees on their Credits page.
                        When set, they see ONLY this card — name, price,
                        features, expiry and a Purchase/Renew button (PayU).
                        Expiry auto-suspends outbound calls until renewal.
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <div className="grid gap-4 sm:grid-cols-3">
                        <div className="space-y-2">
                          <Label htmlFor="plan-title">Plan name</Label>
                          <Input
                            id="plan-title"
                            value={planTitle}
                            onChange={(e) => setPlanTitle(e.target.value)}
                            placeholder="e.g. Enterprise"
                          />
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="plan-price">Price (₹/month)</Label>
                          <Input
                            id="plan-price"
                            type="number"
                            min={1}
                            step="any"
                            value={planPrice}
                            onChange={(e) => setPlanPrice(e.target.value)}
                            placeholder="e.g. 25000"
                          />
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="plan-minutes">
                            Included minutes / month
                          </Label>
                          <Input
                            id="plan-minutes"
                            type="number"
                            min={0}
                            step={1}
                            value={planMinutes}
                            onChange={(e) => setPlanMinutes(e.target.value)}
                            placeholder="e.g. 3000"
                          />
                        </div>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="plan-features">
                          Features (one per line)
                        </Label>
                        <Textarea
                          id="plan-features"
                          rows={3}
                          value={planFeatures}
                          onChange={(e) => setPlanFeatures(e.target.value)}
                          placeholder={"Unlimited agents\nPriority support\nDedicated number"}
                        />
                      </div>
                      <div className="flex flex-wrap items-end gap-3">
                        <Button
                          onClick={onSavePlanCard}
                          disabled={savingPlanCard || !planCardValid}
                        >
                          {savingPlanCard && (
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          )}
                          {detail.plan_card ? "Update plan card" : "Set plan card"}
                        </Button>
                        {detail.plan_card && (
                          <Button
                            variant="outline"
                            onClick={onRemovePlanCard}
                            disabled={savingPlanCard}
                          >
                            Remove card
                          </Button>
                        )}
                        <div className="ml-auto flex items-end gap-2">
                          <div className="space-y-2">
                            <Label htmlFor="plan-expiry">Plan expires</Label>
                            <Input
                              id="plan-expiry"
                              type="date"
                              value={planExpiry}
                              onChange={(e) => setPlanExpiry(e.target.value)}
                              className="w-[160px]"
                            />
                          </div>
                          <Button
                            variant="outline"
                            onClick={onSavePlanExpiry}
                            disabled={savingPlanCard}
                          >
                            Set expiry
                          </Button>
                        </div>
                      </div>
                      {detail.plan_expires_at && (
                        <p className="text-xs text-muted-foreground">
                          Current expiry: {formatTimestamp(detail.plan_expires_at)}{" "}
                          — renewal payments extend it by 30 days.
                        </p>
                      )}
                    </CardContent>
                  </Card>

                  <Card className="md:col-span-2">
                    <CardHeader>
                      <CardTitle>Custom pricing</CardTitle>
                      <CardDescription>
                        Leave a field blank to use the plan default. Placeholder
                        shows the current effective value.
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <div className="grid gap-4 sm:grid-cols-3">
                        <PricingField
                          id="price-per-minute"
                          label="Per minute (₹)"
                          value={perMinute}
                          onChange={setPerMinute}
                          isCustom={!!custom.per_minute_inr}
                          placeholder={pricing?.per_minute_inr}
                        />
                        <PricingField
                          id="price-number"
                          label="Number price (₹)"
                          value={numberPrice}
                          onChange={setNumberPrice}
                          isCustom={!!custom.number_price_inr}
                          placeholder={pricing?.number_price_inr}
                        />
                        <PricingField
                          id="price-setup"
                          label="Setup fee (₹)"
                          value={setupFee}
                          onChange={setSetupFee}
                          isCustom={!!custom.setup_fee_inr}
                          placeholder={pricing?.setup_fee_inr}
                        />
                      </div>
                      <Button
                        onClick={onSavePricing}
                        disabled={submitting || !pricingDirty}
                      >
                        {submitting && (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        )}
                        Save pricing
                      </Button>
                    </CardContent>
                  </Card>

                  <Card className="md:col-span-2">
                    <CardHeader>
                      <CardTitle>Setup fee</CardTitle>
                      <CardDescription>
                        Charge the configured one-time setup fee (
                        {formatInr(pricing?.setup_fee_inr)}) against the
                        client&apos;s balance.
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <Button
                        variant="outline"
                        onClick={() => setChargeOpen(true)}
                        disabled={
                          pricing?.setup_fee_inr == null ||
                          pricing?.setup_fee_inr === 0
                        }
                      >
                        Charge setup fee ({formatInr(pricing?.setup_fee_inr)})
                      </Button>
                    </CardContent>
                  </Card>
                </div>
              </TabsContent>

              {/* VOICELINK ------------------------------------------------ */}
              <TabsContent value="voicelink">
                <div className="grid gap-4 md:grid-cols-2">
                  <Card>
                    <CardHeader>
                      <CardTitle>Provisioning</CardTitle>
                      <CardDescription>
                        Status: {vlStatus ?? "not configured"}
                        {detail.voicelink?.client_id
                          ? ` · client ${detail.voicelink.client_id}`
                          : ""}
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="flex flex-wrap gap-2">
                      {!vlActive && (
                        <Button onClick={onCreateVoiceLink} disabled={creating}>
                          {creating ? (
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          ) : (
                            <UserPlus className="mr-2 h-4 w-4" />
                          )}
                          Create
                        </Button>
                      )}
                      <Button
                        variant="outline"
                        onClick={() => {
                          setRetryPassword("");
                          setRetryOpen(true);
                        }}
                      >
                        <RotateCcw className="mr-2 h-4 w-4" /> Retry provision
                      </Button>
                      <Button
                        variant="outline"
                        onClick={() => {
                          setDidNumber(detail.voicelink?.did_number ?? "");
                          setAssignClientId(detail.voicelink?.client_id ?? "");
                          setAssignOpen(true);
                        }}
                      >
                        <Phone className="mr-2 h-4 w-4" /> Assign DID
                      </Button>
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader>
                      <CardTitle>Portal password</CardTitle>
                      <CardDescription>
                        Reveal the stored copy or record a new one (does not
                        change it on VoiceLink).
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <div className="flex flex-wrap gap-2">
                        <Button
                          variant="outline"
                          onClick={onRevealPassword}
                          disabled={revealLoading}
                        >
                          {revealLoading ? (
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          ) : (
                            <Eye className="mr-2 h-4 w-4" />
                          )}
                          Reveal
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => {
                            setRecordPassword("");
                            setRecordOpen(true);
                          }}
                        >
                          <KeyRound className="mr-2 h-4 w-4" /> Record
                        </Button>
                      </div>
                      {revealed && (
                        <div className="space-y-2 rounded-lg border border-border/60 bg-muted/40 p-3">
                          {revealed.username && (
                            <div className="flex items-center gap-2">
                              <span className="w-20 text-xs text-muted-foreground">
                                Username
                              </span>
                              <code className="flex-1 truncate rounded bg-background px-2 py-1 font-mono text-sm">
                                {revealed.username}
                              </code>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() =>
                                  copyToClipboard(revealed.username!, "Username")
                                }
                              >
                                <Copy className="h-3.5 w-3.5" />
                              </Button>
                            </div>
                          )}
                          <div className="flex items-center gap-2">
                            <span className="w-20 text-xs text-muted-foreground">
                              Password
                            </span>
                            <code className="flex-1 truncate rounded bg-background px-2 py-1 font-mono text-sm">
                              {revealed.password}
                            </code>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() =>
                                copyToClipboard(revealed.password, "Password")
                              }
                            >
                              <Copy className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </div>
                      )}
                    </CardContent>
                  </Card>

                  <Card className="md:col-span-2">
                    <CardHeader>
                      <CardTitle>KYC</CardTitle>
                    </CardHeader>
                    <CardContent className="flex items-center gap-3">
                      {kyc ? (
                        <KycStatusBadge status={kyc} />
                      ) : (
                        <span className="text-sm text-muted-foreground">
                          Not checked
                        </span>
                      )}
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={onCheckKyc}
                        disabled={kycLoading}
                      >
                        {kycLoading ? (
                          <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <ShieldCheck className="mr-2 h-3.5 w-3.5" />
                        )}
                        Refresh KYC
                      </Button>
                    </CardContent>
                  </Card>
                </div>
              </TabsContent>

              {/* NOTES ---------------------------------------------------- */}
              <TabsContent value="notes">
                <Card>
                  <CardHeader>
                    <CardTitle>Ops notes</CardTitle>
                    <CardDescription>
                      Internal log for this client. Notes are visible to admins
                      only.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="space-y-2">
                      <Textarea
                        value={noteText}
                        onChange={(e) => setNoteText(e.target.value)}
                        placeholder="Add a note…"
                        rows={3}
                      />
                      <Button
                        onClick={onAddNote}
                        disabled={submitting || !noteText.trim()}
                        size="sm"
                      >
                        {submitting && (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        )}
                        <Plus className="mr-1 h-4 w-4" /> Add note
                      </Button>
                    </div>
                    <Separator />
                    {notes.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        No notes yet.
                      </p>
                    ) : (
                      <ul className="space-y-3">
                        {[...notes].reverse().map((note, i) => (
                          <li
                            key={`${note.at ?? ""}-${i}`}
                            className="rounded-lg border border-border/60 bg-muted/30 p-3"
                          >
                            <p className="whitespace-pre-wrap text-sm">
                              {note.text}
                            </p>
                            <p className="mt-1.5 text-xs text-muted-foreground">
                              {note.by ? `${note.by} · ` : ""}
                              {formatTimestamp(note.at)}
                            </p>
                          </li>
                        ))}
                      </ul>
                    )}
                  </CardContent>
                </Card>
              </TabsContent>

              {/* DANGER --------------------------------------------------- */}
              <TabsContent value="danger">
                <Card className="border-destructive/40">
                  <CardHeader>
                    <CardTitle>
                      {detail.suspended ? "Unsuspend client" : "Suspend client"}
                    </CardTitle>
                    <CardDescription>
                      Suspending blocks the client from dialing (outbound calls
                      are rejected) until unsuspended. Existing configuration is
                      preserved.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <Button
                      variant={detail.suspended ? "outline" : "destructive"}
                      onClick={() => setSuspendOpen(true)}
                    >
                      {detail.suspended ? "Unsuspend client" : "Suspend client"}
                    </Button>
                  </CardContent>
                </Card>
              </TabsContent>
            </Tabs>

            {/* Audit strip */}
            <Card className="mt-6">
              <CardHeader>
                <CardTitle>Recent admin activity</CardTitle>
                <CardDescription>
                  Latest actions taken on this client.
                </CardDescription>
              </CardHeader>
              <CardContent>
                {audit.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No recorded activity.
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {audit.map((entry) => (
                      <li
                        key={entry.id}
                        className="flex items-start justify-between gap-4 border-b border-border/40 pb-2 text-sm last:border-0 last:pb-0"
                      >
                        <div className="min-w-0">
                          <span className="font-medium">{entry.action}</span>
                          {auditDetailText(entry.detail) && (
                            <span className="ml-2 text-muted-foreground">
                              {auditDetailText(entry.detail)}
                            </span>
                          )}
                        </div>
                        <span className="shrink-0 text-xs text-muted-foreground">
                          {formatTimestamp(entry.created_at)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </>
        ) : null}
      </div>

      {/* Grant credits dialog */}
      <Dialog open={grantOpen} onOpenChange={setGrantOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Grant credits</DialogTitle>
            <DialogDescription>
              Adds call credits to {detail?.owner_email ?? "this organization"}{" "}
              (1 credit = 1 minute).
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="detail-grant-minutes">Minutes</Label>
            <Input
              id="detail-grant-minutes"
              type="number"
              min={1}
              max={100000}
              step={1}
              value={grantMinutes}
              onChange={(e) => setGrantMinutes(e.target.value)}
              placeholder="e.g. 60"
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setGrantOpen(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button
              onClick={onGrantCredits}
              disabled={submitting || !grantMinutesValid}
            >
              {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Grant
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Set balance dialog — sets the exact balance (up or down) */}
      <Dialog open={setBalOpen} onOpenChange={setSetBalOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Set balance</DialogTitle>
            <DialogDescription>
              Sets {detail?.owner_email ?? "this organization"}&apos;s balance to
              an exact value — by money (₹) or minutes. Editing one updates the
              other at {setBalRate > 0 ? `₹${setBalRate}/min` : "the client rate"}.
              Corrects up or down (e.g. 9,000 → 6,000); 0 zeroes it.
            </DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="detail-setbal-rupees">Money (₹)</Label>
              <Input
                id="detail-setbal-rupees"
                type="number"
                min={0}
                step="any"
                value={setBalRupees}
                onChange={(e) => onSetBalRupeesChange(e.target.value)}
                placeholder="e.g. 48000"
                disabled={setBalRate <= 0}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="detail-setbal-minutes">Minutes</Label>
              <Input
                id="detail-setbal-minutes"
                type="number"
                min={0}
                max={100000}
                step={1}
                value={setBalMinutes}
                onChange={(e) => onSetBalMinutesChange(e.target.value)}
                placeholder="e.g. 6000"
              />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            {setBalMinutes.trim() !== "" && setBalRate > 0 ? (
              <>
                {Number(setBalMinutes)} min = {formatInr(Number(setBalRupees))} ·{" "}
              </>
            ) : null}
            Current balance: {formatCredits(money?.balance_seconds)}
            {money?.money_left_inr != null
              ? ` (${formatInr(money.money_left_inr)})`
              : ""}
            {setBalRate <= 0
              ? " — no per-minute rate set, use minutes"
              : ""}
          </p>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setSetBalOpen(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button onClick={onSetCredits} disabled={submitting || !setBalValid}>
              {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Set balance
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Assign DID dialog */}
      <Dialog open={assignOpen} onOpenChange={setAssignOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Assign DID</DialogTitle>
            <DialogDescription>
              Creates or updates the org&apos;s VoiceLink telephony
              configuration with this DID and marks it default for outbound.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="assign-did-number">DID number</Label>
              <Input
                id="assign-did-number"
                value={didNumber}
                onChange={(e) => setDidNumber(e.target.value)}
                placeholder="919484959244"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="assign-client-id">
                VoiceLink client ID (optional)
              </Label>
              <Input
                id="assign-client-id"
                value={assignClientId}
                onChange={(e) => setAssignClientId(e.target.value)}
                placeholder={detail?.voicelink?.client_id ?? "e.g. 474"}
              />
              <p className="text-xs text-muted-foreground">
                Defaults to the org&apos;s provisioned client ID when empty.
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setAssignOpen(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button onClick={onAssignDid} disabled={submitting || !didNumber.trim()}>
              {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Assign DID
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Retry provision dialog */}
      <Dialog open={retryOpen} onOpenChange={setRetryOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Retry VoiceLink provisioning</DialogTitle>
            <DialogDescription>
              Re-runs client creation using the stored username
              {detail?.voicelink?.username
                ? ` (${detail.voicelink.username})`
                : ""}
              . The password below is set on the new VoiceLink client and an
              encrypted copy is kept as the org&apos;s password record.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="retry-password">New VoiceLink password</Label>
            <Input
              id="retry-password"
              type="password"
              value={retryPassword}
              onChange={(e) => setRetryPassword(e.target.value)}
              placeholder="Minimum 8 characters"
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRetryOpen(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button
              onClick={onRetryProvision}
              disabled={submitting || retryPassword.length < 8}
            >
              {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Retry provisioning
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Record password dialog */}
      <Dialog open={recordOpen} onOpenChange={setRecordOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Record portal password</DialogTitle>
            <DialogDescription>
              Records the portal password for reference — it does not change it
              on VoiceLink. Set the actual password in the portal, then record
              it here.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="record-password">Portal password</Label>
            <Input
              id="record-password"
              type="password"
              value={recordPassword}
              onChange={(e) => setRecordPassword(e.target.value)}
              placeholder="Minimum 8 characters"
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRecordOpen(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button
              onClick={onRecordPassword}
              disabled={submitting || recordPassword.length < 8}
            >
              {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Record password
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Charge setup fee confirm */}
      <AlertDialog open={chargeOpen} onOpenChange={setChargeOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Charge setup fee?</AlertDialogTitle>
            <AlertDialogDescription>
              This deducts the configured setup fee (
              {formatInr(pricing?.setup_fee_inr)}) from{" "}
              {detail?.organization_name ?? "this client"}&apos;s balance.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={submitting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                onChargeSetupFee();
              }}
              disabled={submitting}
            >
              {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Charge fee
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Suspend confirm */}
      <AlertDialog open={suspendOpen} onOpenChange={setSuspendOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {detail?.suspended ? "Unsuspend client?" : "Suspend client?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {detail?.suspended
                ? "The client will be able to dial again immediately."
                : "The client will be blocked from dialing (outbound calls rejected) until unsuspended."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={submitting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                onToggleSuspend();
              }}
              disabled={submitting}
            >
              {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {detail?.suspended ? "Unsuspend" : "Suspend"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** A single custom-pricing input with a default/custom indicator. */
function PricingField({
  id,
  label,
  value,
  onChange,
  isCustom,
  placeholder,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  isCustom: boolean;
  placeholder: number | null | undefined;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label htmlFor={id}>{label}</Label>
        {isCustom ? (
          <Badge variant="secondary" className="text-[10px]">
            Custom
          </Badge>
        ) : (
          <span className="text-[10px] text-muted-foreground">Default</span>
        )}
      </div>
      <Input
        id={id}
        type="number"
        min={0}
        step="any"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder != null ? String(placeholder) : "default"}
      />
    </div>
  );
}
