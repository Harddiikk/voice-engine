"use client";

import { planLabel } from "@/components/admin/adminFormat";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { AdminClientKycStatus } from "@/lib/adminClients";

// Per-plan accent classes. Kept intentionally muted so the badges read as
// status, not decoration.
const PLAN_CLASSES: Record<string, string> = {
  trial: "bg-muted text-muted-foreground hover:bg-muted",
  starter: "bg-sky-600 text-white hover:bg-sky-600",
  growth: "bg-emerald-600 text-white hover:bg-emerald-600",
  scale: "bg-violet-600 text-white hover:bg-violet-600",
  enterprise: "bg-amber-600 text-white hover:bg-amber-600",
};

export function PlanBadge({
  plan,
  overridden = false,
}: {
  plan: string | null | undefined;
  /** When true, a small dot marks the plan as a per-client override. */
  overridden?: boolean;
}) {
  if (!plan) return <span className="text-muted-foreground">—</span>;
  const cls = PLAN_CLASSES[plan] ?? "bg-muted text-muted-foreground";
  const badge = (
    <Badge className={cls}>
      {planLabel(plan)}
      {overridden && (
        <span
          aria-hidden
          className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-white/80"
        />
      )}
    </Badge>
  );
  if (!overridden) return badge;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex cursor-help">{badge}</span>
      </TooltipTrigger>
      <TooltipContent side="top">
        <p>Plan is a per-client override</p>
      </TooltipContent>
    </Tooltip>
  );
}

export function SuspendedBadge({
  suspended,
}: {
  suspended: boolean | null | undefined;
}) {
  if (suspended) {
    return <Badge variant="destructive">Suspended</Badge>;
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
      <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" />
      Active
    </span>
  );
}

/**
 * KYC status badge shared by the clients list and the per-client detail page.
 * Renders the disabled / no-client / progress states with an explanatory
 * tooltip.
 */
export function KycStatusBadge({ status }: { status: AdminClientKycStatus }) {
  if (status.status === "disabled") {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex cursor-help">
            <Badge variant="outline">KYC off</Badge>
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs">
          <p>VoiceLink reseller credentials are not configured on the backend</p>
        </TooltipContent>
      </Tooltip>
    );
  }
  if (status.status === "no_client") {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex cursor-help">
            <Badge variant="secondary">No client</Badge>
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs">
          <p>The org has no VoiceLink client id — provision the client first</p>
        </TooltipContent>
      </Tooltip>
    );
  }

  const label = status.is_complete
    ? "KYC complete"
    : status.kyc_status ||
      (status.current_step != null
        ? `Step ${status.current_step}`
        : "Not started");
  const details = [
    `PAN: ${status.pan_verified ? "verified" : "pending"}`,
    `Aadhaar: ${status.aadhaar_verified ? "verified" : "pending"}`,
    ...(status.account_type === "business"
      ? [`GST: ${status.gst_verified ? "verified" : "pending"}`]
      : []),
    ...(status.account_type ? [`Account: ${status.account_type}`] : []),
  ].join(" · ");

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex cursor-help">
          {status.is_complete ? (
            <Badge className="bg-emerald-600 hover:bg-emerald-600">
              {label}
            </Badge>
          ) : (
            <Badge variant="secondary">{label}</Badge>
          )}
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs">
        <p>{details}</p>
      </TooltipContent>
    </Tooltip>
  );
}
