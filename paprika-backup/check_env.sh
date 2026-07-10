#!/bin/bash
# =============================================================================
# check_env.sh
#
# Verifies that the binaries the Paprika backup needs are present on THIS
# machine. Run it on the always-on Mac Mini (git pull first) before relying on
# the backup there — checking on a dev Mac is not enough, since the tools and
# their locations can differ.
#
# It prints each required tool with its path and version, checks that python3
# can import the stdlib modules the Step 2 export uses, and — for anything
# missing — prints how to install it. Exits non-zero if any REQUIRED tool is
# missing, so it is safe to use in other scripts.
#
# Usage:
#   ./check_env.sh
# =============================================================================

# ── Best-effort version string for a known tool ───────────────────────────────
version_of() {
  case "$1" in
    sqlite3) sqlite3 --version 2>/dev/null | awk '{print $1}' ;;
    python3) python3 --version 2>&1 | awk '{print $2}' ;;
    curl)    curl --version 2>/dev/null | head -1 | awk '{print $2}' ;;
    git)     git --version 2>/dev/null | awk '{print $3}' ;;
    zip)     zip -v 2>/dev/null | awk '/This is Zip/{print $4; exit}' ;;
    unzip)   unzip -v 2>/dev/null | awk 'NR==1{print $2; exit}' ;;
    *)       echo "" ;;
  esac
}

# ── Check one binary ──────────────────────────────────────────────────────────
# Usage: check_binary <name> <required|optional> <purpose> <install_hint>
# Prints a status row; on a missing REQUIRED tool, records the install hint and
# bumps the missing counter.
check_binary() {
  local name="$1" level="$2" purpose="$3" hint="$4"
  local path
  path="$(command -v "$name" 2>/dev/null)"

  if [ -n "$path" ]; then
    local ver
    ver="$(version_of "$name")"
    printf "  [✓] %-9s %-12s %s\n" "$name" "${ver:-present}" "$path"
  else
    if [ "$level" = "required" ]; then
      printf "  [✗] %-9s %s\n" "$name" "MISSING (required)"
      MISSING_REQUIRED=$((MISSING_REQUIRED + 1))
      HINTS+=("$name — $purpose"$'\n'"        install: $hint")
    else
      printf "  [–] %-9s %s\n" "$name" "missing (optional)"
      HINTS+=("$name (optional) — $purpose"$'\n'"        install: $hint")
    fi
  fi
}

# =============================================================================
# Main
# =============================================================================
MISSING_REQUIRED=0
HINTS=()

echo "Checking execution environment for paprika-backup..."
echo

# name       level      purpose                                install hint
check_binary sqlite3 required "Step 1 DB snapshot (.backup)"          "ships with macOS — xcode-select --install"
check_binary zip     required "packaging the backup zip"             "ships with macOS — xcode-select --install"
check_binary curl    required "Pushover notifications"               "ships with macOS"
check_binary python3 required "Step 2 HTML export generator"         "brew install python   (or: xcode-select --install)"
check_binary unzip   optional "inspecting/restoring a backup zip"    "ships with macOS"
check_binary git     optional "pulling this repo onto the machine"   "brew install git   (or: xcode-select --install)"

# ── python3 stdlib modules the export relies on ───────────────────────────────
if command -v python3 >/dev/null 2>&1; then
  echo
  if python3 -c "import sqlite3, html, shutil" >/dev/null 2>&1; then
    echo "  [✓] python3 stdlib   sqlite3, html, shutil importable"
  else
    echo "  [✗] python3 stdlib   MISSING one of: sqlite3, html, shutil"
    MISSING_REQUIRED=$((MISSING_REQUIRED + 1))
    HINTS+=("python3 stdlib — the Step 2 generator needs sqlite3/html/shutil"$'\n'"        install a full python3 build, e.g. brew install python")
  fi
fi

# ── Note about the launchd PATH (Homebrew python) ─────────────────────────────
PY_PATH="$(command -v python3 2>/dev/null)"
case "$PY_PATH" in
  /opt/homebrew/*|/usr/local/*)
    echo
    echo "  Note: python3 resolves under a Homebrew prefix ($(dirname "$PY_PATH"))."
    echo "        launchd does not inherit your shell PATH, so the Step 2 export"
    echo "        (which calls python3) needs this directory in the plist's"
    echo "        EnvironmentVariables/PATH — verify it is listed in"
    echo "        com.jtarnvik.paprikabackup.plist before relying on the export."
    ;;
esac

# ── Summary ───────────────────────────────────────────────────────────────────
echo
if [ ${#HINTS[@]} -gt 0 ]; then
  echo "Install hints:"
  for h in "${HINTS[@]}"; do
    echo "  • $h"
  done
  echo
fi

if [ "$MISSING_REQUIRED" -eq 0 ]; then
  echo "Result: all required tools present. Environment OK. ✅"
  exit 0
else
  echo "Result: $MISSING_REQUIRED required item(s) missing — see hints above. ❌"
  exit 1
fi
