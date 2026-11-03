#!/bin/bash
# =============================================================================
# mysql_backup.sh
#
# Weekly MySQL backup script for a Dockerised MySQL instance.
# Dumps all user databases, packages them into a dated zip, and sends
# a Pushover notification on success or failure.
#
# Secrets and paths are read from mysql_backup.conf (not committed to git).
# See mysql_backup.conf.template for required variables.
# =============================================================================

# ── Load config ───────────────────────────────────────────────────────────────
load_config() {
  local config_file
  config_file="$(dirname "$0")/mysql_backup.conf"

  if [ ! -f "$config_file" ]; then
    echo "ERROR: Config file not found: $config_file"
    echo "       Copy mysql_backup.conf.template to mysql_backup.conf and fill in the values."
    exit 1
  fi

  source "$config_file"

  # Validate that all required variables are set
  local required_vars=(
    MYSQL_CONTAINER MYSQL_USER MYSQL_PASSWORD
    BACKUP_DIR ICLOUD_DIR PUSHOVER_USER_KEY PUSHOVER_API_TOKEN
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

# ── Discover databases ────────────────────────────────────────────────────────
# Returns all user databases, excluding MySQL system databases.
discover_databases() {
  docker exec "$MYSQL_CONTAINER" \
    mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" \
    -e "SHOW DATABASES;" 2>/dev/null \
    | grep -Ev "^(Database|information_schema|performance_schema|mysql|sys)$"
}

# ── Dump a single database ────────────────────────────────────────────────────
# Usage: dump_database <db_name> <output_dir>
# Prints filtered error output (if any) and returns mysqldump's exit code.
dump_database() {
  local db="$1"
  local output_dir="$2"
  local stderr_tmp
  stderr_tmp=$(mktemp)

  docker exec "$MYSQL_CONTAINER" \
    mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" \
    --single-transaction --routines --triggers "$db" \
    >"$output_dir/$db.sql" 2>"$stderr_tmp"
  local exit_code=$?

  grep -v "\[Warning\] Using a password on the command line interface can be insecure" "$stderr_tmp"
  rm -f "$stderr_tmp"
  return $exit_code
}

# ── Package dumps into a zip ──────────────────────────────────────────────────
# Usage: create_zip <source_dir> <zip_file>
# Returns: any error output (empty string = success)
create_zip() {
  local source_dir="$1"
  local zip_file="$2"

  zip -j "$zip_file" "$source_dir"/*.sql 2>&1 >/dev/null
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
  local zip_file="$BACKUP_DIR/mysql_backup_$date.zip"
  local errors=""

  trap cleanup EXIT

  mkdir -p "$BACKUP_DIR"
  echo "[$(date)] Starting MySQL backup..."

  # ── Discover databases ──────────────────────────────────────────────────────
  local databases
  databases=$(discover_databases)

  if [ -z "$databases" ]; then
    echo "ERROR: Could not connect to MySQL or no user databases found."
    send_pushover \
      "❌ MySQL Backup Failed" \
      "Could not connect to MySQL container '$MYSQL_CONTAINER', or no databases found." \
      1
    exit 1
  fi

  local db_count
  db_count=$(echo "$databases" | wc -l | tr -d ' ')
  local db_list
  db_list=$(echo "$databases" | tr '\n' ' ')
  echo "  Found $db_count database(s): $db_list"

  # ── Dump each database ──────────────────────────────────────────────────────
  for db in $databases; do
    echo "  Dumping: $db"
    local dump_errors dump_exit
    dump_errors=$(dump_database "$db" "$WORK_DIR")
    dump_exit=$?

    if [ -n "$dump_errors" ] || [ $dump_exit -ne 0 ]; then
      local dump_msg="${dump_errors:-"exit code $dump_exit"}"
      errors="$errors\n[$db]: $dump_msg"
      echo "  WARNING: errors dumping '$db': $dump_msg"
    fi
  done

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
    echo "[$(date)] Backup complete: $ICLOUD_DIR/mysql_backup_$date.zip ($zip_size)"
    send_pushover \
      "✅ MySQL Backup OK" \
      "$db_count database(s): ${db_list}\nFile: mysql_backup_$date.zip ($zip_size)" \
      -1
  else
    echo "[$(date)] Backup completed with errors."
    send_pushover \
      "⚠️ MySQL Backup — Errors Occurred" \
      "$db_count database(s) attempted: ${db_list}\nErrors:$(echo -e "$errors")\nFile may be partial: mysql_backup_$date.zip" \
      1
  fi
}

main "$@"
