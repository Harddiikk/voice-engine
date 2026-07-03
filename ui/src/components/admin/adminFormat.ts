/**
 * Shared formatting helpers for the admin client-management screens
 * (list + per-client detail).
 */

/** Format an INR amount as "₹1,234.5". `null`/`undefined` renders the dash. */
export function formatInr(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

/**
 * Format a money balance for a client. A `null` balance means the org is
 * unmetered (unlimited); otherwise show the INR amount.
 */
export function formatMoneyBalance(value: number | null | undefined): string {
  if (value == null) return "Unlimited";
  return formatInr(value);
}

/** Seconds → "X.X min", or "Unlimited" for unmetered (null) balances. */
export function formatCredits(seconds: number | null | undefined): string {
  if (seconds == null) return "Unlimited";
  return `${(seconds / 60).toFixed(1)} min`;
}

/** Title-case a plan slug ("growth" → "Growth"); dash for empty. */
export function planLabel(plan: string | null | undefined): string {
  if (!plan) return "—";
  return plan.charAt(0).toUpperCase() + plan.slice(1);
}

/** Format an ISO timestamp compactly; falls back to the raw value. */
export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
