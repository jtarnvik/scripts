# Paprika Backup — Implementation Plan

> **Status: planned, not yet built.** This doc holds the agreed plan. Once Step 1 is implemented,
> rewrite this file into the sibling *usage* structure (see "CLAUDE.md handling" below).

## Context

`scripts/paprika-backup/` currently contains **only** this `CLAUDE.md`. Its sibling folders
`scripts/mysql-backup/` and `scripts/raid-watch/` are fully implemented and follow a shared,
mature pattern: a single main `*.sh` script organised into functions, a `*.conf.template` +
gitignored `*.conf` for secrets/paths, a `com.jtarnvik.*.plist` launchd agent, and a
*usage-oriented* `CLAUDE.md` (What it does / Files / Configuration / Script structure / Scheduling /
Testing / Restoring / Dependencies / Known behaviour).

The goal is to bring Paprika up to the same standard. Paprika needs **two** backup types:
(1) a copy of the app's live SQLite DB, (2) an export via the app's built-in HTML export
(AppleScript-driven). **Step 1 is the SQLite backup + launchd agent only.** The AppleScript export
is deferred to Step 2 (outlined below, not built now).

The backup must behave like the mysql backup: produce a dated **zip**, **copy it to the iCloud
folder**, and send **Pushover** success/failure notifications — reusing the exact
`load_config` / `send_pushover` idioms from the sibling scripts.

Verified facts:
- DB files exist at `~/Library/Group Containers/72KVKW69K8.com.hindsightlabs.paprika.mac.v3/Data/Database/`
  (`Paprika.sqlite`, `Paprika.sqlite-wal`, `Paprika.sqlite-shm`) — **but this was verified on the
  development MacBook Pro only.** The backup actually runs on the **always-on Mac Mini home server**
  (same host as mysql/raid). The path/files must still be confirmed there — see Step 0.
- The DB is in **WAL mode and actively syncing** (`-wal` was 3.2 MB on the MacBook), so a hot copy
  needs a consistent-snapshot method.
- **Decision (user-confirmed):** copy via `sqlite3 "$DB/Paprika.sqlite" ".backup dest"` → a single
  consistent `Paprika.sqlite`, safe while Paprika runs. No torn-copy risk. `sqlite3` ships with macOS.
- Repo-level `.gitignore` already excludes `*.conf` and `*.log`; sibling folders each add their own too.

## Step 0 — verify the DB location on the always-on Mac Mini — ✅ DONE

Verified on the Mac Mini (2026-07-09):
- DB path matches the documented default exactly → `PAPRIKA_DB_DIR` is the default, no override needed.
- `Paprika.sqlite` / `-wal` / `-shm` all present; `-wal` ~1.1 MB (live WAL, confirms hot-copy method needed).
- `sqlite3` 3.43.2 present; opened the live DB and `.tables` returned the real Paprika/Core Data schema
  (`ZRECIPE`, `ZGROCERYLIST`, `ZMEAL`, …) → DB is populated + syncing, and `.backup` will work.
- Repo is deployed at `~/scripts` on the Mini, matching the plist path (`/Users/jesper/scripts/paprika-backup/`).

Original instructions (kept for reference / re-verification on any new machine):

The DB path was confirmed only on the development MacBook Pro. Since the backup runs on the Mac Mini,
confirm there **before** writing/deploying the script — a wrong or absent path is the single most
likely reason Step 1 fails silently. Manual check to run on the Mac Mini (the app, macOS version and
iCloud account may differ from the MacBook):

```bash
# 1. Is Paprika installed and has it created its group container + DB?
ls -la ~/Library/Group\ Containers/72KVKW69K8.com.hindsightlabs.paprika.mac.v3/Data/Database/
# Expect: Paprika.sqlite, Paprika.sqlite-wal, Paprika.sqlite-shm

# 2. sqlite3 present and can open the live DB (proves .backup will work)?
sqlite3 ~/Library/Group\ Containers/72KVKW69K8.com.hindsightlabs.paprika.mac.v3/Data/Database/Paprika.sqlite ".tables"
```

Confirm as well that **Paprika is actually installed and syncing on the Mac Mini** (otherwise the
SQLite copy backs up stale/empty data). If the path differs on the Mini, capture the real path — it
becomes the `PAPRIKA_DB_DIR` value in the conf. Only proceed to Step 1 once this is confirmed.

## Step 1 — files to create in `scripts/paprika-backup/`

Naming mirrors the mysql folder (snake_case script/conf, `com.jtarnvik.<name>.plist`).

### 1. `paprika_backup.sh` (main script)
Structure copied from `mysql_backup.sh`, same function layout and comment banners:

- `load_config()` — sources `paprika_backup.conf`, validates required vars
  (`PAPRIKA_DB_DIR BACKUP_DIR ICLOUD_DIR PUSHOVER_USER_KEY PUSHOVER_API_TOKEN`). Copy the
  missing-file / missing-var error handling verbatim from `mysql_backup.sh:14-40`.
- `send_pushover()` — **identical** to `mysql_backup.sh:45-57` (title/message/priority → Pushover REST).
- `backup_sqlite()` — new. `sqlite3 "$PAPRIKA_DB_DIR/Paprika.sqlite" ".backup '$WORK_DIR/Paprika.sqlite'"`,
  capturing stderr; return sqlite3's exit code and any error text (same pattern as `dump_database`).
  Guard first: if `$PAPRIKA_DB_DIR/Paprika.sqlite` is missing → error message (leads to failure
  notification, so a mis-path or Paprika-not-installed situation is loud, like mysql's "no databases").
- `create_zip()` — reuse `mysql_backup.sh:91-96` (`zip -j "$zip_file" "$WORK_DIR"/*`). Filename
  `paprika_backup_YYYY-MM-DD.zip`.
- `move_to_icloud()` — reuse `mysql_backup.sh:101-105` verbatim (mkdir -p iCloud dir, `mv` zip in).
- `cleanup()` + `trap cleanup EXIT` — reuse (`rm -rf "$WORK_DIR"`).
- `main()` — orchestrate: load_config → `WORK_DIR=$(mktemp -d)` → backup_sqlite → create_zip →
  move_to_icloud, accumulating `errors` exactly like mysql's main, then send the ✅ / ⚠️ Pushover
  (priority `-1` on success, `1` on error). Success message includes filename + `du -sh` size.

### 2. `paprika_backup.conf.template`
Same shape as `mysql_backup.conf.template`, with a header comment. Empty-string placeholders for:
`PAPRIKA_DB_DIR`, `BACKUP_DIR`, `ICLOUD_DIR`, `PUSHOVER_USER_KEY`, `PUSHOVER_API_TOKEN`, with a
one-line comment on each. Include the known default path for `PAPRIKA_DB_DIR` as a comment.
(Do **not** create the real `paprika_backup.conf` — it holds secrets and is gitignored; document
the `cp template → conf` step in CLAUDE.md, matching siblings.)

### 3. `com.jtarnvik.paprikabackup.plist`
Copy `com.jtarnvik.mysqlbackup.plist` and adapt:
- `Label` → `com.jtarnvik.paprikabackup`
- `ProgramArguments` script path → `/Users/jesper/scripts/paprika-backup/paprika_backup.sh`
- `StandardOutPath` / `StandardErrorPath` → `…/paprika-backup/paprika_backup.log`
- Schedule: **weekly, Sunday 03:00** (`Weekday 0, Hour 3`) — offset one hour after mysql's 02:00
  so the two Mac-Mini jobs don't overlap. `RunAtLoad` false.
- Update the header install/register/log comment block to the paprika paths.

### 4. `.gitignore`
Add `scripts/paprika-backup/.gitignore` (`*.conf` / `*.log`) to match `raid-watch/.gitignore` and
keep the folder self-contained (even though the repo-level one already covers it).

### CLAUDE.md handling — decision: rewrite during Step 1, not a separate step 3
Once Step 1 is built, replace this planning-style doc with the sibling *usage* structure
(What it does / Files / Configuration / Script structure / Scheduling / Testing / **Restoring** /
Dependencies / Known behaviour). Document **only what Step 1 builds** (the SQLite backup), and add a
short **"Planned — Step 2: HTML export"** section that preserves the motivating "why" (export survives
Paprika-the-company shutting down; AppleScript chosen over paid Keyboard Maestro; Hammerspoon fallback).
This keeps CLAUDE.md accurate at every commit rather than documenting unbuilt features. Step 2 then
fills in the export sections. Preserve these "why" notes when rewriting: iCloud/CloudKit sync context,
the removed `paprika-3-mcp` note, the two-backup rationale.

Restoring section (Step 1): stop Paprika → replace `Paprika.sqlite` in the Database dir with the one
from the zip and **delete** the stale `Paprika.sqlite-wal` / `Paprika.sqlite-shm` (the `.backup` file
is already checkpointed) → relaunch.

### Git
After creating each new file and once approved, `git add` it. Do **not** commit or push unless asked.

## Step 2 — HTML export (outline, build later)
- `paprika_export.applescript` — drives File → Export in Paprika Recipe Manager 3 (System Events),
  fills the save dialog (filename, folder = `$WORK_DIR`), format HTML, category "Alla recept", clicks Export.
- Extend `paprika_backup.sh` `main()` to call the AppleScript (`osascript`) and fold the exported
  output into the **same** dated zip alongside `Paprika.sqlite`, so one zip carries both backup types.
- Add `EXPORT_DIR` / any export config to the conf template if needed; report export failures via the
  same Pushover error path.
- Fill in the "HTML export" sections of CLAUDE.md (script structure, dependencies: `osascript`,
  Accessibility permission for System Events, known fragility notes).
- Open questions to settle then: leave Paprika running after export (yes — needed for export);
  retention (mirror mysql: one dated zip, overwrite same day, no auto-prune).

## Verification (Step 1)
1. `cp paprika_backup.conf.template paprika_backup.conf` and fill in real values (contains Pushover secrets).
2. `chmod +x paprika_backup.sh && ./paprika_backup.sh` — expect console progress lines, a
   `paprika_backup_<date>.zip` in `ICLOUD_DIR`, and a ✅ Pushover notification.
3. `unzip -l "$ICLOUD_DIR/paprika_backup_<date>.zip"` → confirms a single `Paprika.sqlite`.
4. `sqlite3 <extracted>/Paprika.sqlite ".tables"` → confirms the snapshot is a valid, readable DB.
5. Failure path: temporarily point `PAPRIKA_DB_DIR` at a bad path → expect a ⚠️ failure Pushover.
6. launchd: `cp` plist to `~/Library/LaunchAgents/`, `launchctl load …`, `launchctl start
   com.jtarnvik.paprikabackup`, then `tail -f paprika_backup.log` and re-check the iCloud zip.

## Context notes (preserved from original design doc)
- Paprika syncs data via iCloud/CloudKit (`iCloud~com~hindsightlabs~paprika~ios~v3`); the SQLite files
  are the local copy of that cloud data.
- The export dialog offers format HTML and category "Alla recept" (All recipes).
- A Paprika MCP server (`paprika-3-mcp`, Homebrew) was previously tried and removed; only log files
  remain and can be deleted if desired.
