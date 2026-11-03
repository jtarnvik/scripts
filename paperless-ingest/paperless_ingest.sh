#!/bin/bash
# =============================================================================
# paperless_ingest.sh
#
# launchd entry point for paperless_ingest.py: loads paperless_ingest.conf into
# the environment and runs the Python script, which does all the work.
#
# Why a bash wrapper rather than starting python3 directly from launchd:
#   - the .conf is a bash file, like the other scripts in this repo;
#   - Python then runs as a child of /bin/bash, which has Full Disk Access on
#     the Mini, and so may read the ingest folder in iCloud Drive.
#
# Secrets and paths are read from paperless_ingest.conf (not committed to git).
# See paperless_ingest.conf.template for required variables.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/paperless_ingest.conf"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: Config file not found: $CONFIG_FILE"
  echo "       Copy paperless_ingest.conf.template to paperless_ingest.conf and fill in the values."
  exit 1
fi

source "$CONFIG_FILE"
export PAPERLESS_URL PAPERLESS_TOKEN_FILE INGEST_DIR FAILED_DIR STATE_DIR
export PUSHOVER_USER_KEY PUSHOVER_API_TOKEN

# Not exec: Python must stay a child of bash to keep bash's Full Disk Access
/usr/bin/python3 "$SCRIPT_DIR/paperless_ingest.py"
