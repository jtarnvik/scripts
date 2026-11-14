#!/bin/bash
# =============================================================================
# paperless_backup.sh
#
# Weekly backup of Paperless-ngx on the production Mac Mini — decision 14 of the
# Paperless plan in paperless-ingest/paperless-plan.md:
#
#   1. document_exporter (inside the webserver container) writes a dated zip,
#      paperless-export-YYYY-MM-DD.zip, into the staging folder on the internal
#      disk, which the webserver has bind-mounted as its export folder.
#   2. A short-lived container copies the zip to the MediaOverflow mirror, checks
#      the copy, and deletes all but the newest KEEP_ON_MIRROR zips there. Docker
#      does all reading and writing on the external disk, so this script never
#      touches it (no Removable Volumes prompt). --mount fails when the mirror is
#      not mounted, instead of creating the folder on the internal disk.
#   3. The zip is moved from staging into iCloud Drive; then the previous zip
#      there is deleted — only the latest is kept in iCloud.
#
# Pushover: a quiet ✅ on success, a loud ❌ on any failure.
#
# Paths and secrets are read from paperless_backup.conf (not committed to git).
# See paperless_backup.conf.template for the variables.
# =============================================================================

ZIP_PREFIX="paperless-export-"

# ── Load config ───────────────────────────────────────────────────────────────
load_config() {
  local config_file
  config_file="$(dirname "$0")/paperless_backup.conf"

  if [ ! -f "$config_file" ]; then
    echo "ERROR: Config file not found: $config_file"
    echo "       Copy paperless_backup.conf.template to paperless_backup.conf and fill in the values."
    exit 1
  fi

  source "$config_file"

  local required_vars=(
    WEBSERVER_CONTAINER CONTAINER_EXPORT_DIR STAGING_DIR MIRROR_DIR KEEP_ON_MIRROR HELPER_IMAGE
    ICLOUD_DIR PUSHOVER_USER_KEY PUSHOVER_API_TOKEN
  )
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

# Usage: fail <message> — logs, sends the ❌ and exits
fail() {
  echo "[$(date)] FAILED: $1"
  send_pushover "❌ Paperless Backup Failed" "$1" 1
  exit 1
}

# ── Remove zips left in staging by a failed run ───────────────────────────────
clear_staging() {
  local leftover
  for leftover in "$STAGING_DIR/$ZIP_PREFIX"*.zip; do
    [ -e "$leftover" ] || continue
    echo "  Removing leftover from an earlier run: $(basename "$leftover")"
    rm -f "$leftover"
  done
}

# ── 1. Export ─────────────────────────────────────────────────────────────────
# Usage: export_zip <zip_name without .zip>
# Prints the exporter's output; returns its exit code.
export_zip() {
  local zip_name="$1"
  docker exec "$WEBSERVER_CONTAINER" \
    document_exporter "$CONTAINER_EXPORT_DIR" --zip --zip-name "$zip_name" --no-progress-bar 2>&1
}

# ── 2. Copy to the mirror ─────────────────────────────────────────────────────
# Usage: copy_to_mirror <zip file name>
# Copies under a .partial name, compares it byte for byte with the staged zip,
# renames it, and keeps the newest KEEP_ON_MIRROR zips. Prints what the helper
# container printed; returns its exit code.
copy_to_mirror() {
  local zip_file="$1"
  docker run --rm \
    --mount "type=bind,source=$STAGING_DIR,target=/staging,readonly" \
    --mount "type=bind,source=$MIRROR_DIR,target=/mirror" \
    -e ZIP="$zip_file" -e PREFIX="$ZIP_PREFIX" -e KEEP="$KEEP_ON_MIRROR" \
    "$HELPER_IMAGE" sh -euc '
      cp "/staging/$ZIP" "/mirror/$ZIP.partial"
      cmp "/staging/$ZIP" "/mirror/$ZIP.partial"
      mv "/mirror/$ZIP.partial" "/mirror/$ZIP"
      # Dated names sort by date; delete all but the newest $KEEP
      ls -1 /mirror/"$PREFIX"*.zip | sort -r | tail -n +"$((KEEP + 1))" | while read -r old; do
        rm -f "$old"
        echo "deleted $(basename "$old")"
      done
      echo "on mirror: $(ls -1 /mirror/"$PREFIX"*.zip | wc -l | tr -d " ") zip(s)"
    ' 2>&1
}

# ── 3. Move to iCloud ─────────────────────────────────────────────────────────
# Usage: move_to_icloud <zip file name>
# Moves the zip, then deletes every other export zip in the iCloud folder.
# Prints errors; returns non-zero on failure.
move_to_icloud() {
  local zip_file="$1"
  mkdir -p "$ICLOUD_DIR" || return 1
  mv "$STAGING_DIR/$zip_file" "$ICLOUD_DIR/$zip_file" || return 1

  local old
  for old in "$ICLOUD_DIR/$ZIP_PREFIX"*.zip; do
    [ -e "$old" ] || continue
    [ "$(basename "$old")" = "$zip_file" ] && continue
    rm -f "$old" || return 1
    echo "  Deleted previous iCloud copy: $(basename "$old")"
  done
}

# =============================================================================
# Main
# =============================================================================
main() {
  load_config

  local started zip_name zip_file
  started=$(date +%s)
  zip_name="$ZIP_PREFIX$(date +"%Y-%m-%d")"
  zip_file="$zip_name.zip"

  echo "[$(date)] Starting Paperless backup: $zip_file"

  if ! docker inspect -f '{{.State.Running}}' "$WEBSERVER_CONTAINER" 2>/dev/null | grep -q true; then
    fail "Container $WEBSERVER_CONTAINER is not running (or Docker is not reachable)."
  fi

  clear_staging

  # ── 1. Export ───────────────────────────────────────────────────────────────
  echo "  Exporting…"
  local output
  if ! output=$(export_zip "$zip_name"); then
    fail "document_exporter failed: $(echo "$output" | tail -5)"
  fi
  [ -n "$output" ] && echo "$output" | sed 's/^/    /'
  if [ ! -s "$STAGING_DIR/$zip_file" ]; then
    fail "document_exporter reported success, but $STAGING_DIR/$zip_file is missing or empty."
  fi
  local zip_size
  zip_size=$(du -sh "$STAGING_DIR/$zip_file" | cut -f1)
  echo "  Exported: $zip_file ($zip_size)"

  # ── 2. Mirror ───────────────────────────────────────────────────────────────
  echo "  Copying to the mirror: $MIRROR_DIR"
  if ! output=$(copy_to_mirror "$zip_file"); then
    # The zip stays in staging; the next run removes it and exports anew
    fail "Copy to the MediaOverflow mirror failed (is it mounted?): $(echo "$output" | tail -5)"
  fi
  echo "$output" | sed 's/^/    /'
  local mirror_count
  mirror_count=$(echo "$output" | sed -n 's/^on mirror: //p')

  # ── 3. iCloud ───────────────────────────────────────────────────────────────
  echo "  Moving to iCloud: $ICLOUD_DIR"
  if ! output=$(move_to_icloud "$zip_file" 2>&1); then
    fail "Move to iCloud failed: $output"
  fi
  [ -n "$output" ] && echo "$output"

  local minutes=$(( ($(date +%s) - started + 59) / 60 ))
  echo "[$(date)] Backup complete in $minutes min."
  send_pushover \
    "✅ Paperless Backup OK" \
    "$zip_file ($zip_size) in $minutes min. Mirror: $mirror_count. iCloud: latest." \
    -1
}

main "$@"
