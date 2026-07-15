# Auto4You AI Agent Builder — Design (Bucket B, v1)

**Date:** 2026-06-29
**Status:** Draft for approval
**Decisions locked:** generation engine = Anthropic Claude API; first target = GPC agri/order template, then generalize.

## 1. Goal

A high-conversion in-product flow: the user types a business prompt + answers a short
questionnaire → an AI assembles a **valid Dograh `workflow_definition`** (+ tools + webhook) →
the platform creates it as a **draft** the user reviews in the existing workflow editor and
publishes. This is the in-product version of what GPC does today by hand via Claude Code + the
Dograh MCP/REST API.

Guiding principle (from the GPC vault): **LLM at build-time, deterministic at runtime.** Claude
only *generates* the workflow; call execution and any order/webhook backend stay deterministic.

## 2. Scope (v1)

In scope:
- Generate the **Dograh voice workflow** (the agent graph: Global persona node, Start→talking
  nodes→End, extraction variables, optional webhook node + http_api tool).
- Create it in-platform via the existing API (`create/definition → PUT → validate`), return a
  **draft** (human-in-the-loop review before publish; no auto-publish in v1).
- A short **business questionnaire** (synthesized below) + free-text prompt.
- **Webhook injection:** if the user supplies a webhook URL, inject a `webhook` node with a
  `payload_template` of `{{initial_context.*}}`/`{{gathered_context.*}}` (GPC pattern), and/or an
  `http_api` tool for mid-call calls (e.g. place_order / kyc).
- A **GPC "retail order-placing" template** as the seeded few-shot example so agri/retail prompts
  come out well-formed.

Out of scope (v1) — flagged, with reasons:
- **No auto-generation of the n8n/Odoo order backend.** n8n exposes no write API (vault gap). v1
  wires the agent to a webhook URL the user provides; they own the backend.
- **No auto-publish** — generated workflows land as drafts for review.
- **"Banner/retail page"** — not defined in the vault; deferred pending clarification (see §8).
- Generic multi-vertical generation is a later phase; v1 is GPC-template-anchored but written so
  the template is swappable.

## 3. Architecture

```
UI: "Build with AI" (Home/dashboard card)
  → prompt box + business questionnaire + optional webhook URL
  → POST /api/v1/agent-builder/generate
Backend (api/routes/agent_builder.py — thin):
  → services/agent_builder/generator.py
      1. Build a structured Claude request: system prompt = the workflow_definition contract
         (node/tool field schema from api/services/workflow/dto.py) + a trimmed GPC reference
         workflow as a few-shot example.
      2. Call Anthropic API (latest Claude) with tool/JSON output → get {workflow_definition,
         tools[], model_config, extraction_variables}.
      3. Validate the JSON against the Pydantic ReactFlow DTO locally (services/workflow validator).
      4. Create tools (POST /tools/), set tool_uuids on nodes, create workflow
         (create/definition), set workflow_configurations.ai_model_configuration_v2, run /validate.
      5. Return {workflow_id, status: "draft", warnings[]}.
  → services/agent_builder/anthropic_client.py (Claude call; reads ANTHROPIC_API_KEY)
  → services/agent_builder/templates/gpc_retail.py (the seeded reference + questionnaire)
UI: on success → redirect to the existing workflow editor (/workflow/{id}) for review + publish.
```

Why generate-then-validate-then-create (not direct DB writes): reuses the existing validation +
versioning so a malformed AI output can't corrupt a workflow; the draft is editable in the current
editor.

## 4. The business questionnaire (synthesized from the vault)

The vault has no canonical list; this is synthesized from the "⚠️ to confirm" items + locked
decisions (D1–D5) + Priya's conversation-design inputs:

1. **Business type & what the agent does** (free text → the prompt).
2. **Who you sell to** (retailers/dealers vs end consumers).
3. **Product catalog** — paste/upload (name, pack sizes, optional price/usage). Becomes the Global
   node's embedded catalog (small) or a knowledge-base document (large).
4. **Order / CRM backend** — webhook URL to place orders/leads (optional). If given → webhook node
   + http_api tool.
5. **Customer lookup / KYC gating** — is there a lookup/KYC step before pricing/ordering? (yes/no +
   endpoint).
6. **Pricing source** — trust spoken price vs backend pricelist.
7. **Persona** — name, language(s)/vernacular, voice, gender, tone.
8. **Goal + objections + cross-sell** — primary call goal, common objections, cross-sell rules.
9. **Fulfillment** — delivery window / next steps.

v1 ships a sensible default questionnaire; unanswered fields get reasonable defaults in generation.

## 5. Generated artifact shape (grounded in real schema)

`workflow_definition = { nodes[], edges[], viewport }`:
- `globalNode` — persona/policy/catalog (`data.prompt`).
- `startCall` — greeting as the **first line of the prompt** (the static `greeting` field does NOT
  play with Gemini realtime — vault lesson), `tool_uuids`.
- `agentNode×N` — talking steps with `extraction_enabled` + `extraction_variables` (feed
  `gathered_context`), `add_global_prompt: true`, `tool_uuids` (tools repeated on every talking
  node — vault lesson: a tool is only available while in its node).
- `endCall`.
- `webhook` (if URL given) — `http_method`, `endpoint_url`, `payload_template` with
  `{{initial_context.*}}`/`{{gathered_context.*}}`.
- `edges` — source/target between nodes (+ optional condition/label).
- Tools: `POST /api/v1/tools/` `http_api` with `parameters` (LLM-filled) + `preset_parameters`
  (`value_template: {{initial_context.phone_number}}`).
- Models: `workflow_configurations.ai_model_configuration_v2` (e.g. realtime `google_realtime` +
  llm `google`, or Dograh-managed) — chosen from the questionnaire/org defaults.

## 6. New code (all additive, no upstream files touched where avoidable)

Backend:
- `api/routes/agent_builder.py` — `POST /api/v1/agent-builder/generate` (+ maybe `/preview`).
- `api/services/agent_builder/generator.py` — orchestration.
- `api/services/agent_builder/anthropic_client.py` — Claude call (reads `ANTHROPIC_API_KEY`,
  model = latest Claude per repo guidance).
- `api/services/agent_builder/templates/gpc_retail.py` — seeded reference workflow + questionnaire
  defaults.
- `api/schemas/agent_builder.py` — request/response models.

Frontend:
- `ui/src/app/home/` — a prominent "Build with AI" entry (card on Home).
- `ui/src/app/agent-builder/page.tsx` — the prompt + questionnaire form, calls the endpoint, shows
  progress, redirects to `/workflow/{id}` on success.

Config:
- `ANTHROPIC_API_KEY` (VPS `.env.api` secret) — required to generate; if unset the endpoint returns
  a clear error (never crashes).

## 7. Verification / acceptance

- Generated `workflow_definition` passes the existing `/validate` (DTO + graph checks) before the
  endpoint returns success.
- A GPC-style prompt ("Hindi outbound sales caller for an agri-input retailer, place orders via my
  webhook") produces: Global persona, Start→Probe→Recommend→Rate→Close, extraction vars, a webhook
  node bound to the supplied URL — reviewable in the editor.
- Unit: generator produces schema-valid JSON for 3 sample prompts (retail order, lead-qual,
  support). Adversarial: malformed Claude output is caught by local validation and surfaced as a
  warning, not a 500.

## 8. Open questions (need answers before/while building)

1. **ANTHROPIC_API_KEY** — you provide; I set it as a VPS secret.
2. **"Banner/retail" concept** — undefined in the vault. Options: (a) auto-generate a shareable
   landing/embed page for the created agent, (b) it just means the embed widget we already have,
   (c) skip for v1. Need your intent.
3. **Auto-publish vs draft-for-review** — v1 proposes draft-for-review (recommended).
4. **Catalog input** — paste text vs file upload vs pull from an existing knowledge-base doc.
