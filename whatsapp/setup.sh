#!/usr/bin/env bash
# Evolution API helper — create an instance, link a WhatsApp number, send messages.
#
#   ./setup.sh create <instance>                  create instance + save QR to qr-<instance>.png
#   ./setup.sh qr     <instance>                  refresh the QR (re-link)
#   ./setup.sh status <instance>                  connection state ("open" = linked)
#   ./setup.sh send   <instance> <number> "<msg>" send a text message
#   ./setup.sh media  <instance> <number> <url> "<caption>"   send an image/media URL
#
# Reads SERVER_URL + AUTHENTICATION_API_KEY from ./.env. Number = digits only, no "+".
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
BASE="${SERVER_URL:-http://localhost:8080}"
KEY="${AUTHENTICATION_API_KEY:?Set AUTHENTICATION_API_KEY in .env}"
cmd="${1:-}"; inst="${2:-}"

save_qr() { # reads JSON on stdin, writes qr-<inst>.png from .qrcode.base64 or .base64
  INST="$inst" python3 - <<'PY'
import os, sys, json, base64
inst = os.environ["INST"]
d = json.load(sys.stdin)
q = (d.get("qrcode") or {}).get("base64") or d.get("base64") or ""
if q:
    open(f"qr-{inst}.png", "wb").write(base64.b64decode(q.split(",", 1)[-1]))
    print(f"✅ QR saved -> qr-{inst}.png")
    print("   Open it and scan: WhatsApp > Settings > Linked devices > Link a device")
else:
    print(json.dumps(d, indent=2))
PY
}

case "$cmd" in
  create)
    : "${inst:?usage: ./setup.sh create <instance>}"
    curl -fsS -X POST "$BASE/instance/create" \
      -H "apikey: $KEY" -H 'Content-Type: application/json' \
      -d "{\"instanceName\":\"$inst\",\"integration\":\"WHATSAPP-BAILEYS\",\"qrcode\":true}" | save_qr
    echo "   (or scan visually in the Manager UI: $BASE/manager)"
    ;;
  qr)
    : "${inst:?usage: ./setup.sh qr <instance>}"
    curl -fsS "$BASE/instance/connect/$inst" -H "apikey: $KEY" | save_qr
    ;;
  status)
    : "${inst:?usage: ./setup.sh status <instance>}"
    curl -fsS "$BASE/instance/connectionState/$inst" -H "apikey: $KEY"; echo
    ;;
  send)
    : "${inst:?usage: ./setup.sh send <instance> <number> \"<msg>\"}"
    num="${3:?recipient number, international format, no +}"; msg="${4:?message text}"
    curl -fsS -X POST "$BASE/message/sendText/$inst" \
      -H "apikey: $KEY" -H 'Content-Type: application/json' \
      -d "{\"number\":\"$num\",\"text\":$(printf '%s' "$msg" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}"; echo
    ;;
  media)
    : "${inst:?usage: ./setup.sh media <instance> <number> <url> \"<caption>\"}"
    num="${3:?recipient number}"; url="${4:?media url}"; cap="${5:-}"
    curl -fsS -X POST "$BASE/message/sendMedia/$inst" \
      -H "apikey: $KEY" -H 'Content-Type: application/json' \
      -d "{\"number\":\"$num\",\"mediatype\":\"image\",\"media\":\"$url\",\"caption\":\"$cap\"}"; echo
    ;;
  *)
    echo "Usage: ./setup.sh {create|qr|status|send|media} <instance> [number] [text/url] [caption]"; exit 1 ;;
esac
