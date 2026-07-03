/**
 * Small fetch wrapper for the realtime voice *catalog* route
 * (`/api/v1/user/configurations/voices/realtime-catalog`).
 *
 * The route is not part of the generated OpenAPI client yet, so this mirrors
 * the client's base-URL resolution and Bearer-token convention (same pattern
 * as lib/voicePreview.ts / lib/adminClients.ts).
 */

export interface RealtimeVoiceCatalogEntry {
  /** Voice id/name understood by the realtime provider (e.g. "Kore"). */
  name: string;
  /** Perceived character — "male" | "female" — a picking aid, not a hard rule. */
  gender: string;
  /** Short character tag, e.g. "Firm", "Warm", "Upbeat". */
  characteristic: string;
}

/**
 * Realtime providers the backend ships a browsable voice catalog for. Both
 * Gemini realtime variants share the same 30-voice catalog.
 */
export const REALTIME_CATALOG_PROVIDERS = [
  "google_realtime",
  "google_vertex_realtime",
] as const;

export function supportsRealtimeVoiceCatalog(provider: string): boolean {
  return (REALTIME_CATALOG_PROVIDERS as readonly string[]).includes(provider);
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
  return "Failed to load voice catalog";
}

/** Fetch the prebuilt realtime voice catalog (name + gender + characteristic). */
export async function fetchRealtimeVoiceCatalog(
  token: string,
  provider: string,
): Promise<RealtimeVoiceCatalogEntry[]> {
  const query = new URLSearchParams({ provider });
  const res = await fetch(
    `${backendUrl()}/api/v1/user/configurations/voices/realtime-catalog?${query.toString()}`,
    {
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
    },
  );
  let body: unknown = [];
  try {
    body = await res.json();
  } catch {
    // Non-JSON response body — fall through to the generic error below.
  }
  if (!res.ok) throw new Error(detailFromBody(body));
  return Array.isArray(body) ? (body as RealtimeVoiceCatalogEntry[]) : [];
}
