#!/usr/bin/env bash
#
# Open Relay uptime check. Polls one or more health URLs and fires an alert
# ONLY when a target's state changes (up->down or down->up), so you're paged
# on the edge, not every run.
#
# Run this on a box OTHER than the one it watches. A monitor living on the same
# host can't tell you when that host is down. The Hostinger box that already
# holds the offsite backups is a natural home; any always-on machine works.
#
# Schedule it every ~2 minutes with the systemd timer in ops/systemd/, or with
# cron on shared hosting. See ops/UPTIME.md.
#
# Config comes from environment variables (secrets off the repo in
# /etc/openrelay-uptime.env):
#
#   CHECK_URLS     space-separated health URLs to poll
#                  (default: the orc + chat /api/health endpoints)
#   EXPECT         substring the 200 response body must contain (default: ok)
#   TIMEOUT        per-request seconds (default: 10)
#   RETRIES        failed probes in a row before flipping to DOWN (default: 2),
#                  so a single blip doesn't page you
#   STATE_DIR      where last-known state is kept
#                  (default: /var/lib/openrelay-uptime; use ~/... on shared hosting)
#   ALERT_WEBHOOK  Discord/Slack-style webhook; POSTed {"content": "<msg>"}
#   ALERT_CMD      optional command run as: ALERT_CMD "<msg>" (email, ntfy, ...)
#
set -euo pipefail

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

CHECK_URLS="${CHECK_URLS:-https://orc.openrelay.pl/api/health https://chat.openrelay.pl/api/health}"
EXPECT="${EXPECT:-ok}"
TIMEOUT="${TIMEOUT:-10}"
RETRIES="${RETRIES:-2}"
STATE_DIR="${STATE_DIR:-/var/lib/openrelay-uptime}"
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"
ALERT_CMD="${ALERT_CMD:-}"

mkdir -p "$STATE_DIR"

# Minimal JSON string escaper for the webhook payload.
json_str() {
  local s="$1"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/\\n}"
  printf '"%s"' "$s"
}

alert() {
  local msg="$1"
  log "ALERT: $msg"
  local delivered=0
  if [ -n "$ALERT_WEBHOOK" ]; then
    if curl -fsS -m "$TIMEOUT" -H 'Content-Type: application/json' \
         -d "{\"content\": $(json_str "$msg")}" "$ALERT_WEBHOOK" >/dev/null 2>&1
    then delivered=1; else log "WARN: webhook post failed"; fi
  fi
  if [ -n "$ALERT_CMD" ]; then
    if "$ALERT_CMD" "$msg"; then delivered=1; else log "WARN: ALERT_CMD failed"; fi
  fi
  [ "$delivered" -eq 1 ] || log "WARN: no working alert channel; message not delivered"
}

# Up if the request returns HTTP 200 and the body contains EXPECT. A connection
# failure (host unreachable) or any non-200 counts as down.
probe() {
  local url="$1" out code body
  out="$(curl -s -m "$TIMEOUT" -w '\n%{http_code}' "$url" 2>/dev/null)" || return 1
  code="${out##*$'\n'}"
  body="${out%$'\n'*}"
  [ "$code" = "200" ] || return 1
  case "$body" in *"$EXPECT"*) return 0 ;; *) return 1 ;; esac
}

for url in $CHECK_URLS; do
  key="$(printf '%s' "$url" | tr -c 'A-Za-z0-9' '_')"
  state_file="$STATE_DIR/$key"
  prev="up"; [ -f "$state_file" ] && prev="$(cat "$state_file")"

  # Confirm across RETRIES so a momentary blip doesn't flip the state.
  now="down"
  for _ in $(seq 1 "$RETRIES"); do
    if probe "$url"; then now="up"; break; fi
    sleep 2
  done

  if [ "$now" != "$prev" ]; then
    if [ "$now" = "down" ]; then
      alert "🔴 DOWN: $url is not responding (failed ${RETRIES}x)."
    else
      alert "🟢 RECOVERED: $url is back up."
    fi
    printf '%s' "$now" > "$state_file"
  fi
  log "$url -> $now"
done

# Always exit 0: the monitor ran fine. A target being down is reported by the
# alert + the log line, not by this script's exit code (which would otherwise
# make the systemd unit read as failed on every outage).
exit 0
