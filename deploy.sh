#!/usr/bin/env bash
# Kopiert die Integration per SSH in das eigene Home Assistant und startet es neu.
# Konfiguration in .env:  HA_SSH=root@homeassistant.local   (optional HA_SSH_PORT=22)
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
: "${HA_SSH:?HA_SSH fehlt, z. B. HA_SSH=root@homeassistant.local in .env}"
PORT="${HA_SSH_PORT:-22}"
NAME=vfr_flight_weather
DEST=/config/custom_components

echo "→ Kopiere $NAME nach $HA_SSH:$DEST"
tar -C custom_components --exclude='__pycache__' -czf - "$NAME" \
  | ssh -p "$PORT" "$HA_SSH" "mkdir -p $DEST && rm -rf $DEST/$NAME && tar -C $DEST -xzf -"

if [ "${1:-}" = "--no-restart" ]; then
  echo "✓ Kopiert. Browser neu laden (Strg+Umschalt+R), um Frontend-Änderungen zu sehen."
  exit 0
fi

echo "→ Prüfe Konfiguration"
ssh -p "$PORT" "$HA_SSH" "ha core check"
echo "→ Starte Home Assistant neu"
ssh -p "$PORT" "$HA_SSH" "ha core restart"
echo "✓ Fertig."
