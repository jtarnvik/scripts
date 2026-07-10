#!/bin/bash
# =============================================================================
# paprika_backup.sh
#
# Weekly backup script for Paprika Recipe Manager 3.
# Takes a consistent snapshot of the live SQLite database, packages it into a
# dated zip, moves the zip to iCloud, and sends a Pushover notification on
# success or failure.
#
# The snapshot uses `sqlite3 .backup`, which produces a single checkpointed
# Paprika.sqlite file and is safe to run while Paprika is open and syncing
# (no torn-copy risk from the live WAL).
#
# Secrets and paths are read from paprika_backup.conf (not committed to git).
# See paprika_backup.conf.template for required variables.
#
# NOTE: Step 1 backs up the SQLite database only. The built-in HTML export
# (AppleScript-driven) is Step 2 — see CLAUDE.md.
# =============================================================================

# ── Load config ───────────────────────────────────────────────────────────────
load_config() {
  local config_file
  config_file="$(dirname "$0")/paprika_backup.conf"

  if [ ! -f "$config_file" ]; then
    echo "ERROR: Config file not found: $config_file"
    echo "       Copy paprika_backup.conf.template to paprika_backup.conf and fill in the values."
    exit 1
  fi

  source "$config_file"

  # Validate that all required variables are set
  local required_vars=(
    PAPRIKA_DB_DIR BACKUP_DIR ICLOUD_DIR
    PUSHOVER_USER_KEY PUSHOVER_API_TOKEN
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

# ── Snapshot the SQLite database ──────────────────────────────────────────────
# Usage: backup_sqlite <output_dir>
# Produces <output_dir>/Paprika.sqlite via `sqlite3 .backup` (a consistent,
# checkpointed copy of the live database). Prints any error output and returns
# sqlite3's exit code.
backup_sqlite() {
  local output_dir="$1"
  local db_file="$PAPRIKA_DB_DIR/Paprika.sqlite"

  if [ ! -f "$db_file" ]; then
    echo "Paprika database not found: $db_file"
    return 1
  fi

  local stderr_tmp
  stderr_tmp=$(mktemp)

  sqlite3 "$db_file" ".backup '$output_dir/Paprika.sqlite'" 2>"$stderr_tmp"
  local exit_code=$?

  cat "$stderr_tmp"
  rm -f "$stderr_tmp"
  return $exit_code
}

# ── Package the snapshot into a zip ───────────────────────────────────────────
# Usage: create_zip <source_dir> <zip_file>
# Returns: any error output (empty string = success)
create_zip() {
  local source_dir="$1"
  local zip_file="$2"

  zip -j "$zip_file" "$source_dir"/* 2>&1 >/dev/null
}

# ── Move zip to iCloud ────────────────────────────────────────────────────────
# Usage: move_to_icloud <zip_file>
# Returns: error output (empty string = success)
move_to_icloud() {
  local zip_file="$1"
  mkdir -p "$ICLOUD_DIR" 2>&1 || { echo "Could not create iCloud dir: $ICLOUD_DIR"; return 1; }
  mv "$zip_file" "$ICLOUD_DIR/" 2>&1
}

# ── Cleanup temp files ────────────────────────────────────────────────────────
cleanup() {
  rm -rf "$WORK_DIR"
}

# =============================================================================
# Main
# =============================================================================
main() {
  load_config

  local date
  date=$(date +"%Y-%m-%d")
  WORK_DIR=$(mktemp -d)
  local zip_file="$BACKUP_DIR/paprika_backup_$date.zip"
  local errors=""

  trap cleanup EXIT

  mkdir -p "$BACKUP_DIR"
  echo "[$(date)] Starting Paprika backup..."

  # ── Snapshot the database ───────────────────────────────────────────────────
  echo "  Snapshotting SQLite database from: $PAPRIKA_DB_DIR"
  local db_errors db_exit
  db_errors=$(backup_sqlite "$WORK_DIR")
  db_exit=$?

  if [ -n "$db_errors" ] || [ $db_exit -ne 0 ]; then
    local db_msg="${db_errors:-"exit code $db_exit"}"
    echo "ERROR: could not snapshot Paprika database: $db_msg"
    send_pushover \
      "❌ Paprika Backup Failed - Mac Mini" \
      "Could not snapshot the Paprika SQLite database.\n$db_msg" \
      1
    exit 1
  fi

  # ── Package into zip ────────────────────────────────────────────────────────
  echo "  Creating zip: $zip_file"
  local zip_errors
  zip_errors=$(create_zip "$WORK_DIR" "$zip_file")

  if [ $? -ne 0 ] || [ -n "$zip_errors" ]; then
    errors="$errors\n[zip]: $zip_errors"
    echo "  WARNING: errors creating zip: $zip_errors"
  fi

  # ── Move to iCloud ──────────────────────────────────────────────────────────
  local zip_size
  zip_size=$(du -sh "$zip_file" 2>/dev/null | cut -f1)

  echo "  Moving to iCloud: $ICLOUD_DIR"
  local icloud_errors
  icloud_errors=$(move_to_icloud "$zip_file")
  if [ -n "$icloud_errors" ]; then
    errors="$errors\n[icloud]: $icloud_errors"
    echo "  WARNING: errors moving to iCloud: $icloud_errors"
  fi

  # ── Notify ──────────────────────────────────────────────────────────────────
  if [ -z "$errors" ]; then
    echo "[$(date)] Backup complete: $ICLOUD_DIR/paprika_backup_$date.zip ($zip_size)"
    send_pushover \
      "✅ Paprika Backup OK - Mac Mini" \
      "SQLite snapshot backed up.\nFile: paprika_backup_$date.zip ($zip_size)" \
      -1
  else
    echo "[$(date)] Backup completed with errors."
    send_pushover \
      "⚠️ Paprika Backup — Errors Occurred - Mac Mini" \
      "SQLite snapshot taken, but later steps had errors:$(echo -e "$errors")\nFile may be missing or partial: paprika_backup_$date.zip" \
      1
  fi
}

main "$@"
