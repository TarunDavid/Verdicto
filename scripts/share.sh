#!/usr/bin/env bash
# Share Verdicto with friends through a Cloudflare quick tunnel (free, no account).
#   ./scripts/share.sh          -> prints a https://<random-words>.trycloudflare.com link
# Stop with Ctrl+C. The link changes every time you start it.
# People using the link can't open /admin/ or see debug pages (factcheck.middleware.TunnelGuardMiddleware).
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8000}"
if [ ! -x tools/cloudflared ]; then
  echo "tools/cloudflared is missing. Download cloudflared for your Mac from"
  echo "https://github.com/cloudflare/cloudflared/releases (darwin-arm64 or darwin-amd64) into tools/." >&2
  exit 1
fi
if ! curl -s -o /dev/null "http://127.0.0.1:${PORT}/"; then
  echo "Starting the Verdicto server on port ${PORT}…"
  .venv/bin/python manage.py runserver "${PORT}" &
  SERVER_PID=$!
  trap 'kill "${SERVER_PID}" 2>/dev/null' EXIT
  sleep 3
fi
echo "Opening the tunnel… look for the https://….trycloudflare.com link below."
tools/cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:${PORT}"
