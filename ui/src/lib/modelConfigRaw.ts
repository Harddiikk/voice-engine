/**
 * Small fetch wrapper for the model-configuration raw-payload debug route
 * (`/api/v1/organizations/model-configurations/v2/raw`).
 *
 * The route is not part of the generated OpenAPI client yet, so this mirrors
 * the client's base-URL resolution and Bearer-token convention (same pattern
 * as lib/adminClients.ts).
 */

export interface ModelConfigurationV2Raw {
  /** Raw stored v2 payload with secrets masked; null when no row exists. */
  value: Record<string, unknown> | null;
  /** Why the stored payload failed validation; null when it is valid. */
  validation_error?: string | null;
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

export async function getModelConfigurationV2Raw(
  token: string,
): Promise<ModelConfigurationV2Raw> {
  const res = await fetch(
    `${backendUrl()}/api/v1/organizations/model-configurations/v2/raw`,
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
  return body as ModelConfigurationV2Raw;
}
