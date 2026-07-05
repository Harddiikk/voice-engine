/**
 * Small fetch wrapper for the superuser admin Clients routes
 * (`/api/v1/admin/clients*`).
 *
 * These routes are not part of the generated OpenAPI client yet, so this
 * mirrors the client's base-URL resolution and Bearer-token convention
 * (same pattern as lib/kyc.ts).
 */

export type VoiceLinkLiveState =
  | "active"
  | "missing"
  | "unconfigured"
  | "unknown";

export interface AdminClient {
  organization_id: number;
  organization_name: string;
  owner_user_id?: number | null;
  owner_email?: string | null;
  owner_provider_id?: string | null;
  created_at?: string | null;
  voicelink_status?: string | null;
  voicelink_client_id?: string | null;
  voicelink_username?: string | null;
  voicelink_error?: string | null;
  has_voicelink_config: boolean;
  did_number?: string | null;
  // Live reconciliation against VoiceLink.
  live_state: VoiceLinkLiveState;
  live_client_id?: string | null;
  // Remaining call-seconds balance; null = unmetered (unlimited).
  credits_seconds_remaining?: number | null;
  // Billing/plan enrichment (added by the richer admin list endpoint). These
  // are optional so the row still renders if the backend has not shipped them.
  effective_plan?: string | null;
  // Remaining balance in INR; null = unmetered (unlimited).
  money_left_inr?: number | null;
  money_spent_inr?: number | null;
  per_minute_inr?: number | null;
  suspended?: boolean;
}

// The five sellable plans, in ascending order, for the plan selectors.
export const ADMIN_PLANS = [
  "trial",
  "starter",
  "growth",
  "scale",
  "enterprise",
] as const;
export type AdminPlan = (typeof ADMIN_PLANS)[number];

export interface AdminClientNote {
  at?: string | null;
  by?: string | number | null;
  text: string;
}

export interface AdminClientMoney {
  balance_seconds?: number | null;
  unlimited?: boolean | null;
  per_minute_inr?: number | null;
  money_left_inr?: number | null;
  spent_seconds?: number | null;
  money_spent_inr?: number | null;
}

export interface AdminClientPricing {
  per_minute_inr?: number | null;
  number_price_inr?: number | null;
  setup_fee_inr?: number | null;
  // Map of pricing-field name -> true when that field is a per-client override
  // (rather than derived from the plan default).
  custom?: Record<string, boolean> | null;
}

// Admin-designed plan card shown to the client on their Credits page. When
// enabled the client sees ONLY this card (no packs/credit internals).
export interface AdminPlanCard {
  title: string;
  price_inr: number;
  included_minutes: number;
  features: string[];
  enabled: boolean;
}

export interface AdminClientVoiceLink {
  status?: string | null;
  client_id?: string | null;
  username?: string | null;
  did_number?: string | null;
}

export interface AdminClientUsage {
  total_calls?: number | null;
  total_minutes?: number | null;
  [key: string]: number | null | undefined;
}

// Detail payload for GET /admin/clients/{orgId}. Every nested block is optional
// so the page keeps rendering if the parallel backend has not shipped a field.
export interface AdminClientDetail {
  organization_id: number;
  organization_name: string;
  owner_email?: string | null;
  owner_user_id?: number | null;
  owner_provider_id?: string | null;
  plan?: string | null;
  plan_override?: string | null;
  features?: { api?: boolean | null; mcp?: boolean | null } | null;
  pricing?: AdminClientPricing | null;
  money?: AdminClientMoney | null;
  suspended?: boolean;
  // When true, this client also sees the Dograh managed voice + BYOK in the
  // model/voice editor; default false = Gemini voices only.
  show_dograh_voice?: boolean;
  // True when a per-client Gemini key override is set (key itself never sent);
  // false = client uses the shared platform Gemini key.
  has_gemini_key?: boolean;
  // Client plan card + expiry (null = no card / never purchased).
  plan_card?: AdminPlanCard | null;
  plan_expires_at?: string | null;
  // What the client filled during first-run onboarding (or null).
  onboarding_profile?: Record<string, unknown> | null;
  notes?: AdminClientNote[] | null;
  voicelink?: AdminClientVoiceLink | null;
  kyc?: AdminClientKycStatus | null;
  usage?: AdminClientUsage | null;
}

// PATCH /admin/clients/{orgId}/profile — every field optional; only sent fields
// are applied. A `null` on a pricing field clears the override (reset to plan
// default); a `null` on plan_override clears the plan override.
export interface AdminProfilePatch {
  plan_override?: string | null;
  per_minute_inr?: number | null;
  number_price_inr?: number | null;
  setup_fee_inr?: number | null;
  suspended?: boolean;
  show_dograh_voice?: boolean;
  // Per-client Gemini key override; "" clears it back to the platform key.
  gemini_api_key?: string;
  // Client plan card; null removes the card.
  plan_card?: AdminPlanCard | null;
  // ISO timestamp; null clears back to "never purchased".
  plan_expires_at?: string | null;
}

export interface ChargeSetupFeeResult {
  balance?: number | null;
  money?: AdminClientMoney | null;
}

export interface CreateAdminClientBody {
  email: string;
  name?: string;
  plan?: string;
  initial_credit_minutes?: number;
}

export interface CreateAdminClientResult {
  organization_id?: number;
  [key: string]: unknown;
}

export interface AdminAuditEntry {
  id: number;
  actor_user_id?: number | null;
  target_organization_id?: number | null;
  action: string;
  detail?: unknown;
  created_at?: string | null;
}

export interface AdminClientsListResult {
  clients: AdminClient[];
}

export interface RetryProvisionResult {
  voicelink_status: string;
  voicelink_client_id?: string | null;
  voicelink_username?: string | null;
  voicelink_error?: string | null;
}

export interface CreateClientResult {
  action: string; // "linked" | "created"
  voicelink_status: string;
  voicelink_client_id?: string | null;
  voicelink_username?: string | null;
  voicelink_error?: string | null;
}

export interface AssignDidBody {
  did_number: string;
  client_id?: string;
}

export interface AssignDidResult {
  configuration_id: number;
  created: boolean;
  did_number: string;
  client_id?: string | null;
}

export interface GrantCreditsResult {
  organization_id: number;
  granted_seconds: number;
  credits_seconds_remaining?: number | null;
}

export interface SetCreditsResult {
  organization_id: number;
  credits_seconds_remaining?: number | null;
}

// Backend detail string for GET /password when no display copy is stored
// (absent, encryption key unset, or the stored token failed to decrypt).
export const NO_STORED_PASSWORD = "no_stored_password";

export interface ClientPasswordResult {
  username?: string | null;
  password: string;
}

export interface RecordPasswordResult {
  organization_id: number;
  stored: boolean;
  // Reminder: this is a record of the portal password, not a change on
  // VoiceLink (there is no upstream change-password API).
  note: string;
}

// "ok" = fetched from VoiceLink | "no_client" = org has no VoiceLink client
// id | "disabled" = reseller credentials unset on the backend.
export type AdminKycState = "ok" | "no_client" | "disabled";

export interface AdminClientKycStatus {
  status: AdminKycState;
  enabled: boolean;
  client_id_configured?: boolean;
  has_voicelink_config?: boolean;
  client_id?: string | null;
  kyc_status?: string | null;
  pan_verified?: boolean | null;
  aadhaar_verified?: boolean | null;
  gst_verified?: boolean | null;
  is_complete?: boolean | null;
  current_step?: number | string | null;
  account_type?: string | null;
}

function backendUrl(): string {
  return (
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    (typeof window !== "undefined" ? window.location.origin : "")
  );
}

function detailFromBody(body: unknown): string {
  const e = body as { detail?: unknown };
  if (typeof e?.detail === "string") return e.detail;
  if (Array.isArray(e?.detail) && e.detail.length > 0) {
    const first = e.detail[0] as { msg?: string };
    if (first?.msg) return first.msg;
  }
  return "Request failed";
}

async function adminRootFetch<T>(
  token: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${backendUrl()}/api/v1/admin${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(init?.headers ?? {}),
    },
  });
  let body: unknown = {};
  try {
    body = await res.json();
  } catch {
    // Non-JSON response body — fall through to the generic error below.
  }
  if (!res.ok) throw new Error(detailFromBody(body));
  return body as T;
}

// Convenience wrapper for the `/admin/clients` sub-tree (the majority of
// routes). `path` is appended after `/admin/clients` (e.g. "" or "/42/notes").
function adminFetch<T>(
  token: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  return adminRootFetch<T>(token, `/clients${path}`, init);
}

export const listAdminClients = (token: string) =>
  adminFetch<AdminClientsListResult>(token, "");

export const retryProvisionClient = (
  token: string,
  organizationId: number,
  password: string,
) =>
  adminFetch<RetryProvisionResult>(
    token,
    `/${organizationId}/retry-provision`,
    { method: "POST", body: JSON.stringify({ password }) },
  );

export const createClientForOrg = (
  token: string,
  organizationId: number,
  password?: string,
) =>
  adminFetch<CreateClientResult>(token, `/${organizationId}/create`, {
    method: "POST",
    body: JSON.stringify(password ? { password } : {}),
  });

export const assignDidToClient = (
  token: string,
  organizationId: number,
  body: AssignDidBody,
) =>
  adminFetch<AssignDidResult>(token, `/${organizationId}/assign-did`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const grantCreditsToClient = (
  token: string,
  organizationId: number,
  minutes: number,
) =>
  adminFetch<GrantCreditsResult>(token, `/${organizationId}/grant-credits`, {
    method: "POST",
    body: JSON.stringify({ minutes }),
  });

// Set the org's balance to an EXACT value (up or down), unlike grant which
// only adds. Pass exactly one of `minutes` or `rupees`; rupees is converted to
// credits server-side at the client's per-minute rate. `minutes: 0` zeroes it.
export const setClientCredits = (
  token: string,
  organizationId: number,
  amount: { minutes: number } | { rupees: number },
) =>
  adminFetch<SetCreditsResult>(token, `/${organizationId}/set-credits`, {
    method: "POST",
    body: JSON.stringify(amount),
  });

export const getClientKycStatus = (token: string, organizationId: number) =>
  adminFetch<AdminClientKycStatus>(token, `/${organizationId}/kyc-status`);

export const getClientPassword = (token: string, organizationId: number) =>
  adminFetch<ClientPasswordResult>(token, `/${organizationId}/password`);

export const recordClientPassword = (
  token: string,
  organizationId: number,
  password: string,
) =>
  adminFetch<RecordPasswordResult>(token, `/${organizationId}/password`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });

export const getAdminClientDetail = (token: string, organizationId: number) =>
  adminFetch<AdminClientDetail>(token, `/${organizationId}`);

export const updateAdminProfile = (
  token: string,
  organizationId: number,
  patch: AdminProfilePatch,
) =>
  adminFetch<AdminClientDetail>(token, `/${organizationId}/profile`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const addClientNote = async (
  token: string,
  organizationId: number,
  text: string,
): Promise<AdminClientNote[]> => {
  // The endpoint returns { organization_id, notes: [...] } — unwrap to the list.
  const res = await adminFetch<{ notes?: AdminClientNote[] }>(
    token,
    `/${organizationId}/notes`,
    { method: "POST", body: JSON.stringify({ text }) },
  );
  return Array.isArray(res?.notes) ? res.notes : [];
};

export const chargeSetupFee = (
  token: string,
  organizationId: number,
  amountInr?: number,
) =>
  adminFetch<ChargeSetupFeeResult>(token, `/${organizationId}/charge-setup-fee`, {
    method: "POST",
    body: JSON.stringify(amountInr != null ? { amount_inr: amountInr } : {}),
  });

export const createAdminClient = (token: string, body: CreateAdminClientBody) =>
  adminFetch<CreateAdminClientResult>(token, "", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const listAdminAudit = async (
  token: string,
  organizationId?: number,
  limit = 50,
): Promise<AdminAuditEntry[]> => {
  const qs = new URLSearchParams();
  if (organizationId != null) qs.set("org_id", String(organizationId));
  qs.set("limit", String(limit));
  // The endpoint returns { items: [...] } — unwrap to the array the UI maps over.
  const res = await adminRootFetch<{ items?: AdminAuditEntry[] }>(
    token,
    `/audit?${qs.toString()}`,
  );
  return Array.isArray(res?.items) ? res.items : [];
};
