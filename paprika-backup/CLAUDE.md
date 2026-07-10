# Paprika Backup — Project Context

This repo contains a weekly automated backup solution for **Paprika Recipe Manager 3** running on a
Mac Mini home server (the same always-on host as the mysql-backup and raid-watch jobs). The Mac Mini
is not publicly exposed.

Paprika stores its data in a local SQLite database that syncs via iCloud/CloudKit. This job takes a
consistent snapshot of that database, generates a human-readable HTML export from it, zips both
together, copies the zip to iCloud for off-machine storage, and reports the outcome via a Pushover
push notification.

> **Two backup types, both implemented.** (1) The **SQLite snapshot** is the real backup — restores the
> full app state. (2) The **HTML export** (generated from that snapshot, with photos) is durability
> insurance: a copy you can read in any browser even if Paprika (the company / its sync servers) ever
> disappears. Its job is to *prove the DB holds all the recipe data* in readable form — completeness
> over styling. See "The HTML export" below.

---

## What the project does

- Snapshots the live Paprika SQLite database using `sqlite3 .backup` (safe while Paprika is running)
- Generates a self-contained HTML export from the snapshot (all recipes + photos) via `paprika_export.py`
- Packages both into a single dated zip file (`paprika_backup_YYYY-MM-DD.zip`)
- Moves the zip to an iCloud folder for cloud sync and long-term storage
- Sends a Pushover push notification to an iPhone on success or failure
- Runs weekly via macOS `launchd` (Sunday 03:00 by default)

---

## Files

| File | Committed | Purpose |
|------|-----------|---------|
| `paprika_backup.sh` | ✅ | Main backup script |
| `paprika_export.py` | ✅ | Generates the HTML export from the SQLite snapshot |
| `check_env.sh` | ✅ | Verifies required binaries exist on the host (run before deploying) |
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
- `export_recipes()` — runs `paprika_export.py` against the snapshot to build `export/` (index.html +
  photos); resolves the photos dir as `"$(dirname "$PAPRIKA_DB_DIR")/Photos"`; **non-fatal**
- `create_zip()` — packages the snapshot **and** `export/` into the dated zip. Zips explicit items
  (`Paprika.sqlite` + `export`), not `.`, so stray `-wal`/`-shm` sidecars are never included
- `move_to_icloud()` — creates the iCloud dir if needed and moves the zip into it
- `cleanup()` — removes the temp working directory (registered via `trap EXIT`)
- `main()` — orchestrates the full backup flow

The HTML generator, `paprika_export.py`, is a stdlib-only Python 3 script:
`paprika_export.py <snapshot.sqlite> <photos_dir> <output_dir>`. It opens the snapshot with
`immutable=1` (read-only, and never creates sidecar files), queries live recipes (`ZINTRASH = 0`),
copies each recipe's photos in, and writes one self-contained `index.html` (TOC + a section per
recipe with every meaningful field). It prints `recipes=N photos=M skipped=K`.

A failed snapshot is fatal (loud ❌ notification and exit), because it means there is nothing to back
up. The HTML export is **best-effort**: if it fails, the SQLite snapshot is still zipped and shipped
and the run reports ⚠️ instead of ❌. Errors in the zip/iCloud steps are likewise accumulated into a ⚠️.

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
to match. `sqlite3`, `zip`, and `python3` must be on the PATH — launchd does not inherit the user's
shell PATH. The plist PATH lists `/opt/homebrew/bin` (Apple-Silicon Homebrew) and `/usr/local/bin`
(Intel) so Homebrew's `python3` is found for the HTML export. Run `./check_env.sh` on a new host first
to confirm the required binaries are present.

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

The `/bin/bash` grant also covers the `python3` child that copies photos from `…/Data/Photos` for the
HTML export (child processes inherit the responsible-process grant). If a launchd run ever logs
`authorization denied` on photo copy, the export degrades gracefully — it still writes `index.html`
(text) and just reports skipped photos, so the backup never fails because of it.

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

The zip contains `Paprika.sqlite` at the root plus an `export/` folder (`index.html` + `photos/`).
Restore uses **only** `Paprika.sqlite`; the `export/` folder is read-only reference — open
`export/index.html` in a browser to read the recipes without the app. The snapshot is a single,
already-checkpointed `Paprika.sqlite` (the WAL is merged in at `.backup` time), so restoring does
**not** involve `-wal`/`-shm` files.

```bash
# Unzip
unzip paprika_backup_2026-07-10.zip -d ./restore

# Read the recipes in a browser (no app needed)
open ./restore/export/index.html

# Inspect the DB before restoring (optional)
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
| `python3` | HTML export generator (`paprika_export.py`, stdlib only). Homebrew on the Mac Mini — must be on the launchd PATH (`/opt/homebrew/bin`) |
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
- The **HTML export is best-effort / non-fatal**: any failure (missing `python3`, generator error,
  photo-copy permission block) yields a ⚠️ notification, but the SQLite snapshot is still backed up.
- The export **excludes trashed recipes** (`ZINTRASH = 0`); missing/undownloaded photos are skipped
  and counted (`skipped=K` in the summary), never fatal.
- If the Mac is asleep at the scheduled time, launchd will not run the job; it runs at the next
  scheduled time. Set `RunAtLoad` to `true` in the plist to catch up on next login if desired.
- A missing or unreadable database is fatal and sends a ❌ notification — a mis-configured
  `PAPRIKA_DB_DIR`, an uninstalled app, or a missing FDA grant all surface this way.

---

## The HTML export (`paprika_export.py`)

The export is **generated from the SQLite snapshot**, not driven from Paprika's UI. Paprika 3 ships
**no AppleScript dictionary** (`sdef` is empty), so its built-in export could only be automated with
fragile System Events UI scripting that additionally needs an active GUI login session + Accessibility
permission under launchd. Reading the snapshot ourselves is fully headless, robust, and needs no extra
permissions — and since the DB already holds every field, the generated page *is* the proof that the
backup is complete.

`paprika_export.py <snapshot.sqlite> <photos_dir> <output_dir>` (stdlib-only Python 3):
- Opens the snapshot with `immutable=1` (read-only; never creates `-wal`/`-shm` sidecars).
- Selects live recipes (`ZRECIPE` where `ZINTRASH = 0`), ordered by name.
- For each recipe: gathers categories (via the Core Data join table, discovered dynamically since its
  entity-numbered name/columns can vary), and photo filenames from `ZRECIPEPHOTO` (fallback
  `ZRECIPE.ZPHOTO`); copies existing photos from `<photos_dir>/<recipe ZUID>/<file>` into
  `<output_dir>/photos/<ZUID>/`.
- Writes one self-contained `index.html`: embedded CSS (light/dark, printable), a table-of-contents,
  then a section per recipe with every meaningful field (badges for servings/times/difficulty/rating/
  categories/date, photos, source link, description, ingredients, directions, notes, nutrition). All
  text is `html.escape`-d, then lightly formatted (newlines → `<br>`, `**bold**` → `<strong>`).
- Prints `recipes=N photos=M skipped=K`.

Design intent: completeness over styling — *basic but nice*. It will realistically almost never be
needed; if it ever is, the layout can be improved then.

**Schema reference** (Paprika = Core Data, `Z`-prefixed): recipe text in `ZRECIPE.Z{NAME,INGREDIENTS,
DIRECTIONS,NOTES,DESCRIPTIONTEXT,NUTRITIONALINFO}`; meta in `Z{SERVINGS,PREPTIME,COOKTIME,TOTALTIME,
DIFFICULTY,RATING,SOURCE,SOURCEURL}`; photos on disk at `…/Data/Photos/<recipe ZUID>/<ZFILENAME>`.

---

## Context notes

- Paprika syncs data via iCloud/CloudKit (`iCloud~com~hindsightlabs~paprika~ios~v3`); the SQLite files
  are the local copy of that cloud data.
- Paprika 3 has **no AppleScript dictionary**, which is why the HTML export is generated from the
  SQLite snapshot rather than by scripting the app's built-in export dialog.
- A Paprika MCP server (`paprika-3-mcp`, installed via Homebrew) was previously tried and removed; only
  log files remain and can be deleted if desired.
