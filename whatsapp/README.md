# WhatsApp sending — self-hosted Evolution API

Best self-hosted WhatsApp API (2026 comparison vs WAHA / wppconnect / Baileys): turn-key REST,
native multi-number instances, webhooks, **free** (Apache-2.0), and a built-in upgrade path to the
official Cloud API. Sends **free-form text** — no template approval, **$0/message**.

> ⚠️ **Use responsibly.** This drives WhatsApp over the unofficial protocol. Ban risk is about
> *behavior*: **opt-in recipients only, a number you own and have warmed, human-like pacing, low
> volume.** Never cold outreach, never bulk, never a client's production number → that gets banned
> fast (~68% of unofficial users report a ban within 12 months). For marketing-at-scale or a
> client's number, use the **official Cloud API** — AiSensy is already wired into the platform at
> `api/services/whatsapp/providers/aisensy.py`.

## Quickstart

```bash
cd whatsapp
cp .env.example .env                 # set a strong AUTHENTICATION_API_KEY + POSTGRES_PASSWORD
#   openssl rand -hex 24             # handy for the API key

docker compose up -d                 # starts Evolution + Postgres + Redis
curl http://localhost:8080           # health check (or open http://localhost:8080/manager)

./setup.sh create client-onboarding  # creates an instance + saves qr-client-onboarding.png
# open the PNG and scan: WhatsApp > Settings > Linked devices > Link a device
./setup.sh status client-onboarding  # -> {"state":"open"} means your number is linked

./setup.sh send client-onboarding 919876543210 "Hi! Your account is live. Reply STOP to opt out."
```

`<instance>` = one WhatsApp number (create several for several numbers).
Recipient numbers: **international format, digits only, no `+`** (e.g. `919876543210`).

## Calling it from the FastAPI backend

```python
import httpx

BASE = "http://localhost:8080"      # or your VPS URL
API_KEY = "<AUTHENTICATION_API_KEY>"
INSTANCE = "client-onboarding"

async with httpx.AsyncClient(timeout=15) as c:
    r = await c.post(
        f"{BASE}/message/sendText/{INSTANCE}",
        headers={"apikey": API_KEY},
        json={"number": "919876543210", "text": "Hello from VoiceLink!"},
    )
    r.raise_for_status()
```

## API reference (the endpoints `setup.sh` wraps)

| Action | Method + path | Body |
|---|---|---|
| Create instance | `POST /instance/create` | `{instanceName, integration:"WHATSAPP-BAILEYS", qrcode:true}` |
| Link / refresh QR | `GET /instance/connect/{instance}` | — |
| Connection state | `GET /instance/connectionState/{instance}` | — |
| Send text | `POST /message/sendText/{instance}` | `{number, text}` |
| Send media | `POST /message/sendMedia/{instance}` | `{number, mediatype, media, caption}` |

All calls take header `apikey: <AUTHENTICATION_API_KEY>`.

## Production notes

- Run this on your VPS; set `SERVER_URL` to the public URL and put it behind HTTPS (reverse proxy).
- Keep `AUTHENTICATION_API_KEY` secret — it can create/delete instances and send as your number.
- Inbound replies + delivery status come via per-instance webhooks (set `WEBHOOK_*` env or per-instance
  webhook config) — wire them to a backend route when you want two-way conversations.

## Docs
- Evolution API v2: https://doc.evolution-api.com/v2 · repo: https://github.com/EvolutionAPI/evolution-api
- Create instance: https://doc.evolution-api.com/v2/api-reference/instance-controller/create-instance-basic
