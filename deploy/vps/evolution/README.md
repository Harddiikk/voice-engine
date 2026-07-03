# WhatsApp (Evolution API) on the Auto4You VPS

Self-hosted WhatsApp HTTP API at **https://wa.auto4you.in**, isolated from the
voice-engine app (own postgres + redis, no shared DB), fronted by the existing
Caddy with automatic TLS. No host ports are published — the only way in is Caddy.

| What | Value |
| --- | --- |
| URL | https://wa.auto4you.in (Caddy → `evolution-api:8080`) |
| Manager UI | https://wa.auto4you.in/manager |
| Auth | `apikey: <AUTHENTICATION_API_KEY>` header on every request |
| Stack dir (VPS) | `/opt/voiceengine/evolution/` |
| Network | joins existing `voiceengine_app-network` (for Caddy); db/redis on private `internal` |

## Prerequisite (DNS — only the domain owner can do this)

Add an **A record**: `wa.auto4you.in → 165.22.216.189`. Caddy issues the TLS
cert automatically once this resolves. Until then the stack runs fine but the
public HTTPS URL won't have a cert.

## Deploy

```bash
# 1. copy the stack to the VPS
scp deploy/vps/evolution/docker-compose.yml \
    root@165.22.216.189:/opt/voiceengine/evolution/docker-compose.yml

# 2. on the VPS: create secrets + start
ssh root@165.22.216.189
cd /opt/voiceengine/evolution
cat > .env <<EOF
SERVER_URL=https://wa.auto4you.in
AUTHENTICATION_API_KEY=$(openssl rand -hex 24)
POSTGRES_DB=evolution
POSTGRES_USER=evolution
POSTGRES_PASSWORD=$(openssl rand -hex 16)
EOF
docker compose up -d
docker compose ps          # all three healthy/up

# 3. add the Caddy route (APPEND — never overwrite; the box also routes v2u)
cp /opt/voiceengine/Caddyfile /opt/voiceengine/Caddyfile.bak.$(date +%s)
cat >> /opt/voiceengine/Caddyfile <<'EOF'

wa.auto4you.in {
	reverse_proxy evolution-api:8080
}
EOF
docker exec voiceengine-caddy-1 caddy validate --config /etc/caddy/Caddyfile
docker exec voiceengine-caddy-1 caddy reload   --config /etc/caddy/Caddyfile
```

## Link a WhatsApp number + send

```bash
KEY=$(grep AUTHENTICATION_API_KEY /opt/voiceengine/evolution/.env | cut -d= -f2)

# create an instance (one per WhatsApp number)
curl -s -X POST https://wa.auto4you.in/instance/create \
  -H "apikey: $KEY" -H 'Content-Type: application/json' \
  -d '{"instanceName":"client-onboarding","integration":"WHATSAPP-BAILEYS","qrcode":true}'

# easiest: open the Manager UI and scan the QR
#   https://wa.auto4you.in/manager   (login with the API key)
#   WhatsApp > Settings > Linked devices > Link a device

# check it linked
curl -s https://wa.auto4you.in/instance/connectionState/client-onboarding -H "apikey: $KEY"

# send
curl -s -X POST https://wa.auto4you.in/message/sendText/client-onboarding \
  -H "apikey: $KEY" -H 'Content-Type: application/json' \
  -d '{"number":"919876543210","text":"Hi! Your account is live. Reply STOP to opt out."}'
```

The repo's `whatsapp/setup.sh` also works against this — set `SERVER_URL=https://wa.auto4you.in`
and `AUTHENTICATION_API_KEY=<key>` in `whatsapp/.env`.

## Notes

- **Isolation:** own postgres/redis on a private network; the app DB is never touched.
- **Security:** no published ports; all access is API-key-gated through Caddy TLS.
- **Resources:** ~0.4–0.7 GB RAM total for the three containers.
- **Updates:** `cd /opt/voiceengine/evolution && docker compose pull && docker compose up -d`.
- **Teardown:** `docker compose down` (add `-v` to also drop the linked-session data).
- ⚠️ **Compliance:** opt-in recipients, a warmed number you own, human-like pacing, low
  volume. Never cold/bulk → ban. For a client's production number or scale, use the
  official Cloud API (AiSensy is already wired into the platform).
```
