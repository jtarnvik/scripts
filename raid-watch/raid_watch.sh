#!/bin/bash
# =============================================================================
# raid_watch.sh
#
# RAID monitoring script for macOS Apple RAID sets.
# Daily check: sends a Pushover alert only if a problem is detected.
# Weekly heartbeat: always sends the full RAID status summary.
#
# A problem is any of:
#   - a RAID set whose status is not Online
#   - a member whose status is not Online (failed, missing, rebuilding ...)
#   - a mirror with fewer than two members
#   - an expected RAID set (EXPECTED_RAID_SETS) that is not present
#
# Secrets and per-machine settings are read from raid_watch.conf (not
# committed to git). See raid_watch.conf.template for required variables.
# =============================================================================

# diskutil can hang when a RAID member stops responding. A hung run would also
# block every later run, since launchd does not start a job that is still
# running — so give up after this many seconds and report an error instead.
DISKUTIL_TIMEOUT_SECONDS=120

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

  local required_vars=(PUSHOVER_USER_KEY PUSHOVER_API_TOKEN HOST_LABEL)
  local missing=()
  for var in "${required_vars[@]}"; do
    [ -z "${!var:-}" ] && missing+=("$var")
  done
  if [ ${#EXPECTED_RAID_SETS[@]} -eq 0 ]; then
    missing+=(EXPECTED_RAID_SETS)
  fi

  if [ ${#missing[@]} -gt 0 ]; then
    echo "ERROR: Missing required config variables: ${missing[*]}"
    exit 1
  fi
}

# ── Pushover notification ─────────────────────────────────────────────────────
# Usage: send_pushover <title> <message> <priority>
#   priority: -1 = quiet (no sound), 0 = normal, 1 = high
# A failed send is logged, so that a broken token does not look like silence.
send_pushover() {
  local title="$1"
  local message="$2"
  local priority="$3"

  local curl_output
  if ! curl_output=$(curl -sS -f --max-time 30 \
      --form-string "token=$PUSHOVER_API_TOKEN" \
      --form-string "user=$PUSHOVER_USER_KEY" \
      --form-string "title=$title" \
      --form-string "message=$message" \
      --form-string "priority=$priority" \
      https://api.pushover.net/1/messages.json 2>&1); then
    echo "ERROR: Pushover notification failed: $curl_output"
  fi
}

# ── Get RAID status ───────────────────────────────────────────────────────────
# Returns: the output of diskutil appleRAID list as a plist.
# Exit code is non-zero if diskutil failed or did not answer in time.
get_raid_plist() {
  perl -e 'alarm shift; exec @ARGV' "$DISKUTIL_TIMEOUT_SECONDS" \
    diskutil appleRAID list -plist
}

# ── Read a value from the plist ───────────────────────────────────────────────
# Usage: plist_value <plist> <key path>
# Returns: the value at the key path, or the length if it is an array.
#          Empty if the key path does not exist.
plist_value() {
  local plist="$1"
  local key_path="$2"

  plutil -extract "$key_path" raw -o - - <<< "$plist" 2>/dev/null
}

# ── List membership ───────────────────────────────────────────────────────────
# Usage: contains <item> <list items...>
# Exit code 0 if the item is in the list.
contains() {
  local item="$1"
  shift

  local candidate
  for candidate in "$@"; do
    if [ "$candidate" = "$item" ]; then
      return 0
    fi
  done
  return 1
}

# ── Analyse RAID status ───────────────────────────────────────────────────────
# Usage: analyse_raid <plist>
# Sets two globals:
#   SUMMARY  - per RAID set: name, status, level and rebuild mode, then one
#              line per member
#   PROBLEMS - one line per problem found, empty if all is well
analyse_raid() {
  local plist="$1"
  SUMMARY=""
  PROBLEMS=""

  # The plist has no AppleRAIDSets key at all when there are no RAID sets
  local set_count
  set_count=$(plist_value "$plist" "AppleRAIDSets")
  set_count=${set_count:-0}
  if [ "$set_count" -eq 0 ]; then
    SUMMARY="No RAID sets found"$'\n'
  fi

  local found_names=()
  local s
  for ((s = 0; s < set_count; s++)); do
    local set_key="AppleRAIDSets.$s"
    local name status level rebuild size member_count
    name=$(plist_value "$plist" "$set_key.Name")
    status=$(plist_value "$plist" "$set_key.Status")
    level=$(plist_value "$plist" "$set_key.Level")
    rebuild=$(plist_value "$plist" "$set_key.Rebuild")
    size=$(plist_value "$plist" "$set_key.Size")
    member_count=$(plist_value "$plist" "$set_key.Members")
    member_count=${member_count:-0}
    found_names+=("$name")

    SUMMARY+="$name: $status ($level, rebuild $rebuild)"$'\n'

    if [ "$status" != "Online" ]; then
      PROBLEMS+="$name: status is $status"$'\n'
    fi
    if [ "$level" = "Mirror" ] && [ "$member_count" -lt 2 ]; then
      PROBLEMS+="$name: mirror has only $member_count member(s)"$'\n'
    fi

    local m
    for ((m = 0; m < member_count; m++)); do
      local member_key="$set_key.Members.$m"
      local device member_status rebuilt_bytes
      device=$(plist_value "$plist" "$member_key.BSD Name")
      member_status=$(plist_value "$plist" "$member_key.MemberStatus")
      rebuilt_bytes=$(plist_value "$plist" "$member_key.MemberRebuildStatus")

      # A member whose disk is gone has neither a device nor a status
      device=${device:-"(no disk)"}
      member_status=${member_status:-Missing}
      if [ "$member_status" = "Rebuilding" ] && [ -n "$rebuilt_bytes" ] && [ -n "$size" ]; then
        member_status="Rebuilding $((rebuilt_bytes * 100 / size))%"
      fi

      SUMMARY+="  $device: $member_status"$'\n'

      if [ "$member_status" != "Online" ]; then
        PROBLEMS+="$name: member $device is $member_status"$'\n'
      fi
    done
  done

  local expected
  for expected in "${EXPECTED_RAID_SETS[@]}"; do
    if ! contains "$expected" "${found_names[@]}"; then
      PROBLEMS+="$expected: not found"$'\n'
    fi
  done
}

# =============================================================================
# Main
# =============================================================================
main() {
  local mode="${1:-check}"
  load_config

  echo "[$(date)] Starting RAID watch (mode: $mode)..."

  local plist
  if ! plist=$(get_raid_plist) || ! plutil -lint - <<< "$plist" > /dev/null 2>&1; then
    echo "ERROR: Could not read RAID status from diskutil."
    send_pushover \
      "❌ RAID watch error - $HOST_LABEL" \
      "Could not read RAID status: diskutil appleRAID list failed or did not answer within $DISKUTIL_TIMEOUT_SECONDS seconds." \
      1
    exit 1
  fi

  analyse_raid "$plist"

  if [ -n "$PROBLEMS" ]; then
    echo "[$(date)] RAID problem detected — sending alert:"
    echo -n "$PROBLEMS"
    send_pushover \
      "❌ RAID problem - $HOST_LABEL" \
      "Problems:"$'\n'"$PROBLEMS"$'\n'"Status:"$'\n'"$SUMMARY" \
      1
  elif [ "$mode" = "heartbeat" ]; then
    echo "[$(date)] RAID status OK — sending weekly heartbeat."
    send_pushover \
      "✅ RAID OK - $HOST_LABEL" \
      "$SUMMARY" \
      0
  else
    echo "[$(date)] RAID status OK — no notification sent."
  fi
}

main "$@"
