#!/bin/bash
# =============================================================================
# glam_backup.sh
#
# Weekly mirror of the glam folder on the Media SSD to the MediaOverflow RAID on
# the production Mac Mini. Additions and deletions are both replicated:
#
#   1. Both volumes must be mounted — otherwise rsync could fill an empty mount
#      point folder on the internal disk.
#   2. Names in the source that are not in Unicode NFD form are renamed to NFD
#      (nfd_rename.pl). HFS+ on the target always stores NFD; without this, rsync
#      deletes and copies every NFC-named file again on every run.
#   3. rsync -a --delete mirrors the folder. Deleted and changed files are moved to
#      a dated folder under DELETED_DIR instead of being removed. More than
#      MAX_DELETE deletes stops the run with a ❌.
#   4. Dated folders under DELETED_DIR older than KEEP_DELETED_DAYS are removed.
#
# Pushover: a quiet ✅ on success, a loud ❌ on any failure.
#
# Usage: glam_backup.sh [--dry-run]
#   --dry-run  shows what would be renamed, copied, deleted and pruned; changes
#              nothing and sends no notification.
#
# Paths and secrets are read from glam_backup.conf (not committed to git).
# See glam_backup.conf.template for the variables.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DRY_RUN=false

# ── Load config ───────────────────────────────────────────────────────────────
load_config() {
  local config_file="$SCRIPT_DIR/glam_backup.conf"

  if [ ! -f "$config_file" ]; then
    echo "ERROR: Config file not found: $config_file"
    echo "       Copy glam_backup.conf.template to glam_backup.conf and fill in the values."
    exit 1
  fi

  source "$config_file"

  local required_vars=(
    RSYNC SOURCE_DIR SOURCE_VOLUME TARGET_DIR TARGET_VOLUME DELETED_DIR KEEP_DELETED_DAYS MAX_DELETE
    PUSHOVER_USER_KEY PUSHOVER_API_TOKEN
  )
  local missing=()
  for var in "${required_vars[@]}"; do
    if [ -z "${!var:-}" ]; then
      missing+=("$var")
    fi
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

  if [ "$DRY_RUN" = true ]; then
    echo "  (dry run — no notification: $title: $message)"
    return
  fi

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
  send_pushover "❌ Glam Backup Failed" "$1" 1
  exit 1
}

# ── 1. Volumes ────────────────────────────────────────────────────────────────
# Usage: is_mounted <volume path> — true if a file system is mounted exactly there
is_mounted() {
  mount | grep -q " on $1 ("
}

# ── 2. Unicode names ──────────────────────────────────────────────────────────
# Usage: normalize_names — renames non-NFD names in SOURCE_DIR to NFD.
# Prints the renames; returns non-zero if one failed.
normalize_names() {
  if [ "$DRY_RUN" = true ]; then
    /usr/bin/perl "$SCRIPT_DIR/nfd_rename.pl" --dry-run "$SOURCE_DIR"
  else
    /usr/bin/perl "$SCRIPT_DIR/nfd_rename.pl" "$SOURCE_DIR"
  fi
}

# ── 3. Mirror ─────────────────────────────────────────────────────────────────
# Usage: mirror <backup dir> <output file>
# Runs rsync; its itemized changes and stats go to the log and to <output file>.
# Returns rsync's exit code.
#
# --delete deletes on the target before copying into each folder (rsync 3's
# default "during"). Never switch to --delete-after or --delete-delay: an
# NFC/NFD name mismatch then deletes the freshly checked file from the mirror.
mirror() {
  local backup_dir="$1"
  local output_file="$2"
  local options=(
    --archive
    --delete
    --max-delete="$MAX_DELETE"
    --backup --backup-dir="$backup_dir"
    --exclude=.DS_Store
    --itemize-changes
    --stats --human-readable
  )
  if [ "$DRY_RUN" = true ]; then
    options+=(--dry-run)
  fi

  "$RSYNC" "${options[@]}" "$SOURCE_DIR/" "$TARGET_DIR/" 2>&1 | tee "$output_file" | sed 's/^/    /'
  return "${PIPESTATUS[0]}"
}

# Usage: stat_value <output file> <label> — the number after "<label>: " in rsync's stats
stat_value() {
  sed -n "s/^$2: \([^ ]*\).*/\1/p" "$1"
}

# ── 4. Prune deleted files ────────────────────────────────────────────────────
# Usage: prune_deleted — removes dated folders in DELETED_DIR that are
# KEEP_DELETED_DAYS old or older. Prints what it removed (or would remove).
prune_deleted() {
  local cutoff folder name
  cutoff=$(date -v-"${KEEP_DELETED_DAYS}"d +"%Y-%m-%d")

  for folder in "$DELETED_DIR"/*; do
    [ -d "$folder" ] || continue
    name=$(basename "$folder")
    # Only touch folders this script created
    if [[ ! "$name" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
      continue
    fi
    # Dated names compare as strings in date order
    if [[ "$name" > "$cutoff" ]]; then
      continue
    fi
    if [ "$DRY_RUN" = true ]; then
      echo "  would prune: $name"
    else
      rm -rf "$folder" || return 1
      echo "  pruned: $name"
    fi
  done
}

# =============================================================================
# Main
# =============================================================================
main() {
  if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=true
  fi

  load_config

  local started today
  started=$(date +%s)
  today=$(date +"%Y-%m-%d")

  echo "[$(date)] Starting glam backup$([ "$DRY_RUN" = true ] && echo " (DRY RUN)"): $SOURCE_DIR → $TARGET_DIR"

  # ── 1. Volumes ──────────────────────────────────────────────────────────────
  if ! is_mounted "$SOURCE_VOLUME"; then
    fail "$SOURCE_VOLUME is not mounted."
  fi
  if ! is_mounted "$TARGET_VOLUME"; then
    fail "$TARGET_VOLUME is not mounted."
  fi
  if [ ! -d "$SOURCE_DIR" ]; then
    fail "$SOURCE_DIR does not exist (or cannot be read — check Full Disk Access for /bin/bash)."
  fi
  if [ ! -x "$RSYNC" ]; then
    fail "rsync not found at $RSYNC (brew install rsync)."
  fi

  # ── 2. Unicode names ────────────────────────────────────────────────────────
  echo "  Normalizing names to NFD…"
  local output renamed_count
  if ! output=$(normalize_names 2>&1); then
    fail "Renaming to NFD failed: $(echo "$output" | grep FAILED | head -3)"
  fi
  if [ -n "$output" ]; then
    echo "$output" | sed 's/^/    /'
  fi
  renamed_count=$(echo "$output" | grep -c 'rename')

  # ── 3. Mirror ───────────────────────────────────────────────────────────────
  echo "  Mirroring…"
  local rsync_output rsync_exit
  rsync_output=$(mktemp -t glam_backup)
  mirror "$DELETED_DIR/$today" "$rsync_output"
  rsync_exit=$?

  local deleted transferred size
  deleted=$(stat_value "$rsync_output" "Number of deleted files")
  transferred=$(stat_value "$rsync_output" "Number of regular files transferred")
  size=$(sed -n "s/^Total transferred file size: //p" "$rsync_output")
  local error_lines
  error_lines=$(grep -iE 'error|failed|denied|not permitted' "$rsync_output" | head -3)
  rm -f "$rsync_output"

  case "$rsync_exit" in
    0)
      ;;
    24)
      # Files vanished during the run — a download moved or removed mid-run; picked up next week
      echo "  Some source files vanished during the run (rsync exit 24) — not an error."
      ;;
    25)
      fail "Stopped: more than $MAX_DELETE files to delete — the mirror is partly updated. Check $SOURCE_DIR; if the deletes are intended, raise MAX_DELETE for one run."
      ;;
    *)
      fail "rsync failed with exit code $rsync_exit: $error_lines"
      ;;
  esac

  # ── 4. Prune ────────────────────────────────────────────────────────────────
  echo "  Pruning deleted-file folders older than $KEEP_DELETED_DAYS days…"
  if ! output=$(prune_deleted 2>&1); then
    fail "Pruning $DELETED_DIR failed: $output"
  fi
  if [ -n "$output" ]; then
    echo "$output"
  fi

  local minutes=$(( ($(date +%s) - started + 59) / 60 ))
  echo "[$(date)] Backup complete in $minutes min."
  send_pushover \
    "✅ Glam Backup OK" \
    "$transferred file(s) copied ($size), $deleted deleted (kept 4 weeks), $renamed_count renamed to NFD, in $minutes min." \
    -1
}

main "$@"
