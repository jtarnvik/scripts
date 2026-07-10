# Paprika Backup — Project Context

This repo contains a weekly automated backup solution for **Paprika Recipe Manager 3** running on a
Mac Mini home server (the same always-on host as the mysql-backup and raid-watch jobs). The Mac Mini
is not publicly exposed.

Paprika stores its data in a local SQLite database that syncs via iCloud/CloudKit. This job takes a
consistent snapshot of that database, zips it, copies the zip to iCloud for off-machine storage, and
reports the outcome via a Pushover push notification.

> **Two backup types are planned.** Step 1 (this doc, implemented) backs up the **SQLite database**.
> Step 2 (pending) adds an **HTML export** via Paprika's built-in export — a human-readable copy that
> survives even if Paprika the company shuts its sync servers down. See "Planned — Step 2" below.

---

## What the project does

- Snapshots the live Paprika SQLite database using `sqlite3 .backup` (safe while Paprika is running)
- Packages the snapshot into a single dated zip file (`paprika_backup_YYYY-MM-DD.zip`)
- Moves the zip to an iCloud folder for cloud sync and long-term storage
- Sends a Pushover push notification to an iPhone on success or failure
- Runs weekly via macOS `launchd` (Sunday 03:00 by default)

---

## Files

| File | Committed | Purpose |
|------|-----------|---------|
| `paprika_backup.sh` | ✅ | Main backup script |
| `paprika_backup.conf.template` | ✅ | Template showing required config variables |
| `paprika_backup.conf` | ❌ | Local secrets/paths — created from template, never committed |
| `com.jtarnvik.paprikabackup.plist` | ✅ | macOS launchd agent for scheduling |
| `.gitignore` | ✅ | Excludes `*.conf` and `*.log` |

---

## Configuration

All secrets and environment-specific paths live in `paprika_backup.conf` (excluded from git via
`.gitignore`). To set up on a new machine:

```bash
cp paprika_backup.conf.template paprika_backup.conf
# edit paprika_backup.conf and fill in all five variables
```

Required variables:

| Variable | Description |
|----------|-------------|
| `PAPRIKA_DB_DIR` | Directory holding `Paprika.sqlite` (see default path below) |
| `BACKUP_DIR` | Absolute path where the zip is staged before moving to iCloud |
| `ICLOUD_DIR` | iCloud folder the zip is moved to for cloud sync / long-term storage |
| `PUSHOVER_USER_KEY` | Pushover user key (from pushover.net dashboard) |
| `PUSHOVER_API_TOKEN` | Pushover API token (from your registered app) |

Default `PAPRIKA_DB_DIR` (verified on the Mac Mini, macOS convention — same on any Mac running the app):

```
~/Library/Group Containers/72KVKW69K8.com.hindsightlabs.paprika.mac.v3/Data/Database
```

---

## Script structure

`paprika_backup.sh` is organised as functions:

- `load_config()` — sources `paprika_backup.conf` and validates all variables are set
- `send_pushover()` — sends a push notification via the Pushover REST API
- `backup_sqlite()` — snapshots the live DB with `sqlite3 "$DB" ".backup ..."` into a single
  checkpointed `Paprika.sqlite`; guards for a missing DB file and returns sqlite3's exit code
- `create_zip()` — packages the snapshot into the dated zip
- `move_to_icloud()` — creates the iCloud dir if needed and moves the zip into it
- `cleanup()` — removes the temp working directory (registered via `trap EXIT`)
- `main()` — orchestrates the full backup flow

A failed snapshot is fatal (loud ❌ notification and exit), because it means there is nothing to back
up. Errors in the later zip/iCloud steps are accumulated and reported as a ⚠️ notification.

---

## Scheduling (launchd)

The plist file registers a launchd agent for the current user (not system-wide).

```bash
# Install (copy plist and register — do once, survives reboot)
cp ~/scripts/paprika-backup/com.jtarnvik.paprikabackup.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jtarnvik.paprikabackup.plist

# Unregister
launchctl unload ~/Library/LaunchAgents/com.jtarnvik.paprikabackup.plist

# Trigger manually (e.g. for testing)
launchctl start com.jtarnvik.paprikabackup

# Check if registered
launchctl list | grep jtarnvik

# View logs
tail -f ~/scripts/paprika-backup/paprika_backup.log
```

The schedule is **Sunday 03:00**, deliberately one hour after the mysql backup (02:00) so the two
Mac-Mini jobs don't overlap. The plist has paths hardcoded for the Mac Mini (`~/scripts/paprika-backup/`).
If deploying to a different machine, update the script path, log paths, and `EnvironmentVariables/PATH`
to match. `sqlite3` and `zip` must be on the PATH — launchd does not inherit the user's shell PATH.

### ⚠️ Full Disk Access is required for the launchd run

Paprika's database lives in `~/Library/Group Containers/…`, which macOS protects under **Full Disk
Access** (TCC). A manual run in Terminal works because it inherits Terminal's FDA grant, but the
launchd run (`launchd → /bin/bash → sqlite3`) has no grant and fails with:

```
ERROR: could not snapshot Paprika database: Error: unable to open database
"…/Paprika.sqlite": authorization denied
```

**Fix:** System Settings → Privacy & Security → **Full Disk Access** → `+` → add **`/bin/bash`**
(the interpreter named in the plist's ProgramArguments) and enable it. The grant applies to the next
process launch — no unload/load or reboot needed. This grant covers all bash scripts launched this
way; acceptable on a private home server. Re-do this on any new machine the backup is deployed to.

---

## Testing the script manually

```bash
chmod +x paprika_backup.sh
./paprika_backup.sh
```

A manual run inherits Terminal's Full Disk Access, so it works even before the launchd FDA grant
above is in place. Output goes to stdout; the `paprika_backup.log` file is only written when the job
runs via launchd. On success you should see a new `paprika_backup_<date>.zip` in `ICLOUD_DIR` and a
✅ Pushover notification.

---

## Restoring from a backup

The snapshot is a single, already-checkpointed `Paprika.sqlite` (the WAL is merged in at `.backup`
time), so restoring does **not** involve `-wal`/`-shm` files.

```bash
# Unzip
unzip paprika_backup_2026-07-10.zip -d ./restore

# Inspect before restoring (optional)
sqlite3 ./restore/Paprika.sqlite "PRAGMA integrity_check;"
sqlite3 ./restore/Paprika.sqlite "SELECT COUNT(*) FROM ZRECIPE;"   # recipe count
```

To restore into the live app:
1. **Quit Paprika** completely.
2. Replace `Paprika.sqlite` in the Database dir with the one from the zip.
3. **Delete** the now-stale `Paprika.sqlite-wal` and `Paprika.sqlite-shm` in that dir (the restored
   file is already checkpointed; leaving old WAL/SHM would corrupt the view of the DB).
4. Relaunch Paprika.

The recipe name is stored in `ZRECIPE.ZNAME` (Paprika uses a Core Data `Z`-prefixed schema):

```sql
SELECT ZNAME FROM ZRECIPE ORDER BY ZNAME;   -- list all recipe names
```

---

## Dependencies

All dependencies are standard macOS tooling — no additional software or subscriptions required.

| Tool | Used for |
|------|----------|
| `sqlite3` | Consistent snapshot of the live database (`.backup`) — ships with macOS |
| `zip` | Packaging the backup |
| `curl` | Pushover REST API calls |
| `launchd` | macOS-native job scheduling |
| Pushover | Push notifications to iPhone (one-time ~$5 app purchase, no subscription) |

---

## Known behaviour

- `sqlite3 .backup` produces a consistent snapshot even while Paprika is open and syncing — there is
  no need to quit the app for the backup, and no torn-copy risk from the live WAL.
- Only one backup zip is kept per date. Running the script twice on the same day overwrites the first.
- **Full Disk Access must be granted to `/bin/bash`** for the launchd run to read Paprika's Group
  Container (see the Scheduling section). Manual Terminal runs are unaffected.
- If the Mac is asleep at the scheduled time, launchd will not run the job; it runs at the next
  scheduled time. Set `RunAtLoad` to `true` in the plist to catch up on next login if desired.
- A missing or unreadable database is fatal and sends a ❌ notification — a mis-configured
  `PAPRIKA_DB_DIR`, an uninstalled app, or a missing FDA grant all surface this way.

---

## Planned — Step 2: HTML export

A second backup type, not yet implemented. Paprika's built-in **HTML export** produces a
human-readable copy of all recipes that does not depend on the app or its sync servers — insurance
against Paprika the company shutting down. The SQLite snapshot is complete and consistent, but only
useful while Paprika (or a compatible reader) still exists; the HTML export is the durable, portable
fallback.

Planned approach:
- `paprika_export.applescript` — drives File → Export in Paprika Recipe Manager 3 via System Events,
  fills the save dialog (filename, folder = the script's temp work dir), format **HTML**, category
  **"Alla recept"** (All recipes), clicks Export. AppleScript was chosen over the paid Keyboard Maestro;
  **Hammerspoon** is the fallback if AppleScript proves too fragile.
- Extend `paprika_backup.sh`'s `main()` to run the export (`osascript`) and fold its output into the
  **same** dated zip alongside `Paprika.sqlite`, so one zip carries both backup types. Export failures
  report via the same Pushover error path.
- Add `osascript` and the System Events Accessibility permission to the Dependencies/Known behaviour
  sections when built. Paprika is left running (the export needs it open).

---

## Context notes

- Paprika syncs data via iCloud/CloudKit (`iCloud~com~hindsightlabs~paprika~ios~v3`); the SQLite files
  are the local copy of that cloud data.
- The export dialog offers format HTML and category "Alla recept" (All recipes).
- A Paprika MCP server (`paprika-3-mcp`, installed via Homebrew) was previously tried and removed; only
  log files remain and can be deleted if desired.
