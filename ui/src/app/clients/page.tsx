"use client";

import {
  ArrowRight,
  Coins,
  ExternalLink,
  Loader2,
  RefreshCw,
  Search,
  UserPlus,
  Users,
} from "lucide-react";
import Link from "next/link";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

import { PlanBadge, SuspendedBadge } from "@/components/admin/AdminBadges";
import {
  formatCredits,
  formatInr,
  formatMoneyBalance,
  planLabel,
} from "@/components/admin/adminFormat";
import { PageHeader } from "@/components/layout/PageHeader";
import { PageShell } from "@/components/layout/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  ADMIN_PLANS,
  type AdminClient,
  createAdminClient,
  grantCreditsToClient,
  listAdminClients,
} from "@/lib/adminClients";
import { useAuth } from "@/lib/auth";
import { impersonateAsSuperadmin } from "@/lib/utils";

const LOW_BALANCE_THRESHOLD_INR = 100;

function VoiceLinkStatusBadge({ client }: { client: AdminClient }) {
  let badge: ReactNode;
  switch (client.live_state) {
    case "active":
      badge = (
        <Badge className="bg-emerald-600 hover:bg-emerald-600">Active</Badge>
      );
      break;
    case "missing":
      badge = <Badge variant="destructive">Missing</Badge>;
      break;
    case "unconfigured":
      badge = <Badge variant="outline">Not configured</Badge>;
      break;
    default:
      badge = <Badge variant="secondary">Unknown</Badge>;
  }

  const tooltip =
    client.voicelink_error ||
    (client.live_state !== "active" &&
    client.voicelink_status &&
    client.voicelink_status !== "provisioned"
      ? `Stored status: ${client.voicelink_status}`
      : null);

  if (!tooltip) return badge;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex cursor-help">{badge}</span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs">
        <p>{tooltip}</p>
      </TooltipContent>
    </Tooltip>
  );
}

/** True when the org is unmetered (unlimited) on money or credits. */
function isUnlimited(client: AdminClient): boolean {
  return (
    client.money_left_inr === null || client.credits_seconds_remaining === null
  );
}

/** Balance cell — prefers the INR money balance, falls back to credit minutes
 * when the backend has not shipped the money fields yet. */
function balanceDisplay(client: AdminClient): string {
  if (client.money_left_inr !== undefined) {
    return formatMoneyBalance(client.money_left_inr);
  }
  return formatCredits(client.credits_seconds_remaining);
}

type SuspendedFilter = "all" | "active" | "suspended";

export default function ClientsPage() {
  const { user, getAccessToken, loading: authLoading } = useAuth();
  const hasFetched = useRef(false);

  const [clients, setClients] = useState<AdminClient[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Filters
  const [search, setSearch] = useState("");
  const [planFilter, setPlanFilter] = useState<string>("all");
  const [suspendedFilter, setSuspendedFilter] = useState<SuspendedFilter>("all");
  const [lowBalanceOnly, setLowBalanceOnly] = useState(false);

  // Grant credits dialog state (kept as a quick row action)
  const [grantTarget, setGrantTarget] = useState<AdminClient | null>(null);
  const [grantMinutes, setGrantMinutes] = useState("");

  // New client dialog state
  const [createOpen, setCreateOpen] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [newName, setNewName] = useState("");
  const [newPlan, setNewPlan] = useState<string>("trial");
  const [newCredits, setNewCredits] = useState("");

  const fetchClients = useCallback(
    async (showSpinner = false) => {
      if (showSpinner) setRefreshing(true);
      try {
        const token = await getAccessToken();
        if (!token) throw new Error("Missing access token");
        const result = await listAdminClients(token);
        setClients(result.clients);
      } catch (err) {
        toast.error(
          err instanceof Error ? err.message : "Failed to load clients",
        );
      } finally {
        setLoading(false);
        if (showSpinner) setRefreshing(false);
      }
    },
    [getAccessToken],
  );

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) return;
    hasFetched.current = true;
    fetchClients();
  }, [authLoading, user, fetchClients]);

  const filteredClients = useMemo(() => {
    const q = search.trim().toLowerCase();
    return clients.filter((c) => {
      if (q) {
        const haystack = [
          c.organization_name,
          c.owner_email ?? "",
          `#${c.organization_id}`,
          String(c.organization_id),
        ]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      if (planFilter !== "all" && (c.effective_plan ?? "") !== planFilter) {
        return false;
      }
      if (suspendedFilter === "suspended" && !c.suspended) return false;
      if (suspendedFilter === "active" && c.suspended) return false;
      if (lowBalanceOnly) {
        const low =
          c.money_left_inr != null &&
          c.money_left_inr < LOW_BALANCE_THRESHOLD_INR;
        if (!low) return false;
      }
      return true;
    });
  }, [clients, search, planFilter, suspendedFilter, lowBalanceOnly]);

  const grantMinutesNumber = Number(grantMinutes);
  const grantMinutesValid =
    Number.isInteger(grantMinutesNumber) &&
    grantMinutesNumber >= 1 &&
    grantMinutesNumber <= 100000;

  const openGrantDialog = (client: AdminClient) => {
    setGrantTarget(client);
    setGrantMinutes("");
  };

  const onGrantCredits = async () => {
    if (!grantTarget || !grantMinutesValid) return;
    setSubmitting(true);
    try {
      const token = await getAccessToken();
      if (!token) throw new Error("Missing access token");
      const result = await grantCreditsToClient(
        token,
        grantTarget.organization_id,
        grantMinutesNumber,
      );
      toast.success(
        `Granted ${grantMinutesNumber} minute${grantMinutesNumber === 1 ? "" : "s"} — balance is now ${formatCredits(result.credits_seconds_remaining)}`,
      );
      setGrantTarget(null);
      // Refetch so the ₹ balance column reflects the new credit balance.
      await fetchClients();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to grant credits",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const newEmailValid = /.+@.+\..+/.test(newEmail.trim());
  const newCreditsNumber = newCredits.trim() === "" ? 0 : Number(newCredits);
  const newCreditsValid =
    newCredits.trim() === "" ||
    (Number.isInteger(newCreditsNumber) &&
      newCreditsNumber >= 0 &&
      newCreditsNumber <= 100000);

  const resetCreateForm = () => {
    setNewEmail("");
    setNewName("");
    setNewPlan("trial");
    setNewCredits("");
  };

  const onCreateClient = async () => {
    if (!newEmailValid || !newCreditsValid) return;
    setSubmitting(true);
    try {
      const token = await getAccessToken();
      if (!token) throw new Error("Missing access token");
      await createAdminClient(token, {
        email: newEmail.trim(),
        ...(newName.trim() ? { name: newName.trim() } : {}),
        plan: newPlan,
        ...(newCredits.trim() !== ""
          ? { initial_credit_minutes: newCreditsNumber }
          : {}),
      });
      toast.success(`Client created for ${newEmail.trim()}`);
      setCreateOpen(false);
      resetCreateForm();
      await fetchClients();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to create client",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const onImpersonate = async (client: AdminClient) => {
    if (!client.owner_provider_id) {
      toast.error("This organization has no owner user to impersonate");
      return;
    }
    try {
      const token = await getAccessToken();
      if (!token) throw new Error("Missing access token");
      await impersonateAsSuperadmin({
        accessToken: token,
        providerUserId: client.owner_provider_id,
        redirectPath: "/model-configurations",
        openInNewTab: true,
      });
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to impersonate user",
      );
    }
  };

  const hasFilters =
    search.trim() !== "" ||
    planFilter !== "all" ||
    suspendedFilter !== "all" ||
    lowBalanceOnly;

  return (
    <PageShell width="wide">
      <PageHeader
        eyebrow="Superuser"
        title="Clients"
        icon={Users}
        subtitle="Client organizations, their plan, balance and VoiceLink state. Open Manage on a row to change plan & pricing, provision VoiceLink, view KYC, add ops notes, or suspend the client."
        actions={
          <>
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <UserPlus className="mr-2 h-4 w-4" />
              New client
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => fetchClients(true)}
              disabled={loading || refreshing}
            >
              <RefreshCw
                className={`mr-2 h-4 w-4 ${refreshing ? "animate-spin" : ""}`}
              />
              Refresh
            </Button>
          </>
        }
      />

      {/* Filters */}
      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
          <div className="relative w-full sm:max-w-xs">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name, email or #id"
              className="pl-9"
            />
          </div>
          <Select value={planFilter} onValueChange={setPlanFilter}>
            <SelectTrigger className="w-full sm:w-[150px]">
              <SelectValue placeholder="Plan" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All plans</SelectItem>
              {ADMIN_PLANS.map((p) => (
                <SelectItem key={p} value={p}>
                  {planLabel(p)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={suspendedFilter}
            onValueChange={(v) => setSuspendedFilter(v as SuspendedFilter)}
          >
            <SelectTrigger className="w-full sm:w-[150px]">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="active">Active only</SelectItem>
              <SelectItem value="suspended">Suspended only</SelectItem>
            </SelectContent>
          </Select>
          <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
            <Checkbox
              checked={lowBalanceOnly}
              onCheckedChange={(v) => setLowBalanceOnly(v === true)}
            />
            Low balance (&lt; {formatInr(LOW_BALANCE_THRESHOLD_INR)})
          </label>
          {hasFilters && (
            <span className="text-xs text-muted-foreground">
              {filteredClients.length} of {clients.length}
            </span>
          )}
        </div>

        {loading ? (
          <div className="rounded-2xl border border-border/60 bg-card p-5 shadow-[var(--shadow-card)]">
            <div className="grid gap-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          </div>
        ) : clients.length === 0 ? (
          <div className="flex flex-col items-center gap-3 rounded-2xl border border-border/60 bg-card px-6 py-16 text-center shadow-[var(--shadow-card)]">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted">
              <Users className="h-6 w-6 text-muted-foreground" />
            </div>
            <p className="text-label text-foreground">
              No client organizations yet
            </p>
            <p className="text-body max-w-sm text-muted-foreground">
              New signups appear here automatically, or use{" "}
              <span className="font-medium text-foreground">New client</span>{" "}
              to create one.
            </p>
          </div>
        ) : filteredClients.length === 0 ? (
          <div className="flex flex-col items-center gap-3 rounded-2xl border border-border/60 bg-card px-6 py-16 text-center shadow-[var(--shadow-card)]">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted">
              <Search className="h-6 w-6 text-muted-foreground" />
            </div>
            <p className="text-label text-foreground">No matching clients</p>
            <p className="text-body max-w-sm text-muted-foreground">
              No clients match the current search and filters.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto rounded-2xl border border-border/60 bg-card shadow-[var(--shadow-card)]">
            <Table>
              <TableHeader>
                <TableRow className="border-border/50 hover:bg-transparent">
                  <TableHead className="text-label text-muted-foreground">
                    Organization
                  </TableHead>
                  <TableHead className="text-label text-muted-foreground">
                    Owner email
                  </TableHead>
                  <TableHead className="text-label text-muted-foreground">
                    Plan
                  </TableHead>
                  <TableHead className="text-label text-muted-foreground">
                    VoiceLink
                  </TableHead>
                  <TableHead className="text-label text-muted-foreground">
                    DID
                  </TableHead>
                  <TableHead className="text-label text-right text-muted-foreground">
                    ₹ Balance
                  </TableHead>
                  <TableHead className="text-label text-right text-muted-foreground">
                    ₹ Spent
                  </TableHead>
                  <TableHead className="text-label text-muted-foreground">
                    Status
                  </TableHead>
                  <TableHead className="text-label text-right text-muted-foreground">
                    Actions
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredClients.map((client) => (
                  <TableRow
                    key={client.organization_id}
                    className="border-border/50 transition-colors hover:bg-muted/40"
                  >
                    <TableCell>
                      <Link
                        href={`/clients/${client.organization_id}`}
                        className="font-medium tabular-nums hover:underline"
                      >
                        #{client.organization_id}
                      </Link>
                      <div className="max-w-[180px] truncate text-xs text-muted-foreground">
                        {client.organization_name}
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {client.owner_email ?? "—"}
                    </TableCell>
                    <TableCell>
                      <PlanBadge plan={client.effective_plan} />
                    </TableCell>
                    <TableCell>
                      <VoiceLinkStatusBadge client={client} />
                    </TableCell>
                    <TableCell className="font-mono text-sm tabular-nums">
                      {client.did_number ??
                        (client.has_voicelink_config ? (
                          <span className="font-sans text-muted-foreground">
                            No DID
                          </span>
                        ) : (
                          "—"
                        ))}
                    </TableCell>
                    <TableCell className="text-right text-sm tabular-nums">
                      {isUnlimited(client) ? (
                        <span className="text-muted-foreground">Unlimited</span>
                      ) : (
                        balanceDisplay(client)
                      )}
                    </TableCell>
                    <TableCell className="text-right text-sm tabular-nums">
                      {client.money_spent_inr !== undefined
                        ? formatInr(client.money_spent_inr)
                        : "—"}
                    </TableCell>
                    <TableCell>
                      <SuspendedBadge suspended={client.suspended} />
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="inline-flex">
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => openGrantDialog(client)}
                                disabled={isUnlimited(client)}
                              >
                                <Coins className="h-3.5 w-3.5" />
                              </Button>
                            </span>
                          </TooltipTrigger>
                          <TooltipContent side="top">
                            <p>
                              {isUnlimited(client)
                                ? "Unmetered org (unlimited) — granting would meter it"
                                : "Grant call credits (minutes)"}
                            </p>
                          </TooltipContent>
                        </Tooltip>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => onImpersonate(client)}
                              disabled={!client.owner_provider_id}
                            >
                              <ExternalLink className="h-3.5 w-3.5" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent side="top">
                            <p>Impersonate the owner (new tab)</p>
                          </TooltipContent>
                        </Tooltip>
                        <Button variant="outline" size="sm" asChild>
                          <Link href={`/clients/${client.organization_id}`}>
                            Manage
                            <ArrowRight className="ml-1 h-3.5 w-3.5" />
                          </Link>
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        {/* Grant credits dialog */}
        <Dialog
          open={grantTarget !== null}
          onOpenChange={(open) => !open && setGrantTarget(null)}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Grant credits</DialogTitle>
              <DialogDescription>
                Adds call credits to the metered balance of{" "}
                {grantTarget?.owner_email ?? "this organization"} (1 credit = 1
                minute of call time). Current balance:{" "}
                {grantTarget ? balanceDisplay(grantTarget) : "—"}.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="grant-minutes">Minutes</Label>
              <Input
                id="grant-minutes"
                type="number"
                min={1}
                max={100000}
                step={1}
                value={grantMinutes}
                onChange={(e) => setGrantMinutes(e.target.value)}
                placeholder="e.g. 60"
              />
              <p className="text-xs text-muted-foreground">
                Whole minutes, between 1 and 100,000.
              </p>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setGrantTarget(null)}
                disabled={submitting}
              >
                Cancel
              </Button>
              <Button
                onClick={onGrantCredits}
                disabled={submitting || !grantMinutesValid}
              >
                {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Grant credits
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* New client dialog */}
        <Dialog
          open={createOpen}
          onOpenChange={(open) => {
            setCreateOpen(open);
            if (!open) resetCreateForm();
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>New client</DialogTitle>
              <DialogDescription>
                Creates a client organization for this email. Pick a plan and,
                optionally, seed an initial credit balance.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="new-email">Owner email</Label>
                <Input
                  id="new-email"
                  type="email"
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                  placeholder="owner@company.com"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-name">Organization name (optional)</Label>
                <Input
                  id="new-name"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="Acme Inc."
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="new-plan">Plan</Label>
                  <Select value={newPlan} onValueChange={setNewPlan}>
                    <SelectTrigger id="new-plan">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ADMIN_PLANS.map((p) => (
                        <SelectItem key={p} value={p}>
                          {planLabel(p)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="new-credits">Initial credits (min)</Label>
                  <Input
                    id="new-credits"
                    type="number"
                    min={0}
                    max={100000}
                    step={1}
                    value={newCredits}
                    onChange={(e) => setNewCredits(e.target.value)}
                    placeholder="optional"
                  />
                </div>
              </div>
              {!newCreditsValid && (
                <p className="text-xs text-destructive">
                  Credits must be a whole number between 0 and 100,000.
                </p>
              )}
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => {
                  setCreateOpen(false);
                  resetCreateForm();
                }}
                disabled={submitting}
              >
                Cancel
              </Button>
              <Button
                onClick={onCreateClient}
                disabled={submitting || !newEmailValid || !newCreditsValid}
              >
                {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Create client
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
    </PageShell>
  );
}
