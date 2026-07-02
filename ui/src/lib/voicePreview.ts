/**
 * Small fetch wrapper for the realtime voice preview route
 * (`/api/v1/user/configurations/voices/realtime-preview`).
 *
 * The route is not part of the generated OpenAPI client yet, so this mirrors
 * the client's base-URL resolution and Bearer-token convention (same pattern
 * as lib/adminClients.ts).
 */

export interface RealtimeVoicePreviewResult {
  /** Signed URL of a short WAV sample for the requested voice. */
  url: string;
  /** True when the sample was served from the storage cache. */
  cached: boolean;
}

export interface RealtimeVoicePreviewParams {
  provider: string;
  voice: string;
  language?: string;
  model?: string;
}

/** Realtime providers the backend can synthesize previews for. */
export const REALTIME_PREVIEW_PROVIDERS = [
  "google_realtime",
  "openai_realtime",
] as const;

export function supportsRealtimeVoicePreview(provider: string): boolean {
  return (REALTIME_PREVIEW_PROVIDERS as readonly string[]).includes(provider);
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

export async function fetchRealtimeVoicePreview(
  token: string,
  params: RealtimeVoicePreviewParams,
): Promise<RealtimeVoicePreviewResult> {
  const query = new URLSearchParams({
    provider: params.provider,
    voice: params.voice,
  });
  if (params.language) query.set("language", params.language);
  if (params.model) query.set("model", params.model);

  const res = await fetch(
    `${backendUrl()}/api/v1/user/configurations/voices/realtime-preview?${query.toString()}`,
    {
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
    },
  );
  let body: unknown = {};
  try {
    body = await res.json();
  } catch {
    // Non-JSON response body — fall through to the generic error below.
  }
  if (!res.ok) throw new Error(detailFromBody(body));
  return body as RealtimeVoicePreviewResult;
}
