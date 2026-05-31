#!/bin/bash
# =============================================================================
# raid_watch.sh
#
# RAID monitoring script for macOS Apple RAID sets.
# Daily check: sends a Pushover alert only if a problem is detected.
# Weekly heartbeat: always sends the full RAID status summary.
#
# Secrets are read from raid_watch.conf (not committed to git).
# See raid_watch.conf.template for required variables.
# =============================================================================

# ── Load config ───────────────────────────────────────────────────────────────
load_config() {
  local config_file
  config_file="$(dirname "$0")/raid_watch.conf"

  if [ ! -f "$config_file" ]; then
    echo "ERROR: Config file not found: $config_file"
    echo "       Copy raid_watch.conf.template to raid_watch.conf and fill in the values."
    exit 1
  fi

  source "$config_file"

  local required_vars=(PUSHOVER_USER_KEY PUSHOVER_API_TOKEN)
  local missing=()
  for var in "${required_vars[@]}"; do
    [ -z "${!var:-}" ] && missing+=("$var")
  done

  if [ ${#missing[@]} -gt 0 ]; then
    echo "ERROR: Missing required config variables: ${missing[*]}"
    exit 1
  fi
}

# ── Pushover notification ─────────────────────────────────────────────────────
# Usage: send_pushover <title> <message> <priority>
#   priority: -1 = quiet (no sound), 0 = normal, 1 = high
send_pushover() {
  local title="$1"
  local message="$2"
  local priority="$3"

  curl -s \
    --form-string "token=$PUSHOVER_API_TOKEN" \
    --form-string "user=$PUSHOVER_USER_KEY" \
    --form-string "title=$title" \
    --form-string "message=$message" \
    --form-string "priority=$priority" \
    https://api.pushover.net/1/messages.json > /dev/null
}

# ── Get raw RAID status ───────────────────────────────────────────────────────
# Returns: raw output from diskutil appleRAID list
get_raid_status() {
  diskutil appleRAID list
}

# ── Extract RAID summary ──────────────────────────────────────────────────────
# Usage: extract_summary <status_output>
# Returns: lines useful for a status notification (name, state, member counts)
extract_summary() {
  local status="$1"
  echo "$status" | grep -E "Name:|Status:|#.*Online|#.*Failed|#.*Rebuilding|#.*Missing"
}

# ── Extract problem lines ─────────────────────────────────────────────────────
# Usage: extract_problems <status_output>
# Returns: lines indicating a degraded/failed RAID, empty string if healthy
extract_problems() {
  local status="$1"
  echo "$status" | grep -E "Failed|Degraded|Offline|Missing|Rebuilding"
}

# =============================================================================
# Main
# =============================================================================
main() {
  local mode="${1:-check}"
  load_config

  echo "[$(date)] Starting RAID watch (mode: $mode)..."

  local status
  status=$(get_raid_status)

  if [ -z "$status" ]; then
    echo "ERROR: Could not retrieve RAID status from diskutil."
    send_pushover \
      "❌ RAID Watch Error - Mac Mini" \
      "Could not retrieve RAID status from diskutil appleRAID list." \
      1
    exit 1
  fi

  if [ "$mode" = "heartbeat" ]; then
    local summary
    summary=$(extract_summary "$status")
    echo "[$(date)] Sending weekly RAID heartbeat."
    send_pushover \
      "💓 RAID Weekly Status - Mac Mini" \
      "$summary" \
      0
  else
    local problems
    problems=$(extract_problems "$status")
    if [ -n "$problems" ]; then
      echo "[$(date)] RAID problem detected — sending alert."
      send_pushover \
        "⚠️ RAID Alert - Mac Mini" \
        "$problems" \
        1
    else
      echo "[$(date)] RAID status OK — no notification sent."
    fi
  fi
}

main "$@"
