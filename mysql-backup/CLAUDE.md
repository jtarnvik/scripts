# MySQL Backup — Project Context

This repo contains a weekly automated backup solution for a Dockerised MySQL
instance running on a Mac Mini home server. The Mac Mini is the Docker host and
is not publicly exposed.

---

## What the project does

- Discovers all user databases in a MySQL Docker container automatically
- Dumps each database using `mysqldump` into a `.sql` file
- Packages all dumps into a single dated zip file (`mysql_backup_YYYY-MM-DD.zip`)
- Sends a Pushover push notification to an iPhone on success or failure
- Runs weekly via macOS `launchd` (Sunday 02:00 by default)

---

## Files

| File | Committed | Purpose |
|------|-----------|---------|
| `mysql_backup.sh` | ✅ | Main backup script |
| `mysql_backup.conf.template` | ✅ | Template showing required config variables |
| `mysql_backup.conf` | ❌ | Local secrets — created from template, never committed |
| `com.jtarnvik.mysqlbackup.plist` | ✅ | macOS launchd agent for scheduling |
| `.gitignore` | ✅ | Excludes `*.conf` and `*.log` |

---

## Configuration

All secrets and environment-specific paths live in `mysql_backup.conf` (excluded
from git via `.gitignore`). To set up on a new machine:

```bash
cp mysql_backup.conf.template mysql_backup.conf
# edit mysql_backup.conf and fill in all six variables
```

Required variables:

| Variable | Description |
|----------|-------------|
| `MYSQL_CONTAINER` | Docker container name or ID |
| `MYSQL_USER` | MySQL user (typically `root`) |
| `MYSQL_PASSWORD` | MySQL password |
| `BACKUP_DIR` | Absolute path where zip files are saved |
| `PUSHOVER_USER_KEY` | Pushover user key (from pushover.net dashboard) |
| `PUSHOVER_API_TOKEN` | Pushover API token (from your registered app) |

---

## Script structure

`mysql_backup.sh` is organised as functions:

- `load_config()` — sources `mysql_backup.conf` and validates all variables are set
- `send_pushover()` — sends a push notification via the Pushover REST API
- `discover_databases()` — queries MySQL for all user databases, excluding system DBs
- `dump_database()` — runs `mysqldump` for one database, capturing errors separately from SQL output
- `create_zip()` — packages all `.sql` files into the dated zip
- `cleanup()` — removes the temp working directory (registered via `trap EXIT`)
- `main()` — orchestrates the full backup flow

---

## Scheduling (launchd)

The plist file registers a launchd agent for the current user (not system-wide).

```bash
# Register (do once, survives reboot)
launchctl load ~/Library/LaunchAgents/com.jtarnvik.mysqlbackup.plist

# Unregister
launchctl unload ~/Library/LaunchAgents/com.jtarnvik.mysqlbackup.plist

# Trigger manually (e.g. for testing)
launchctl start com.jtarnvik.mysqlbackup

# Check if registered
launchctl list | grep jtarnvik

# View logs
tail -f ~/scripts/mysql_backup.log
```

The plist must be placed in `~/Library/LaunchAgents/` and the script path
inside it must be updated to match where the repo is cloned.

---

## Testing the script manually

```bash
chmod +x mysql_backup.sh
./mysql_backup.sh
```

Output goes to stdout and to the log file defined in the plist when run via
launchd.

---

## Restoring from a backup

```bash
# Unzip
unzip mysql_backup_2026-05-18.zip -d ./restore

# Restore a single database
docker exec -i <container> mysql -uroot -ppassword <db_name> < ./restore/<db_name>.sql

# Restore all databases
for f in ./restore/*.sql; do
  DB=$(basename "$f" .sql)
  echo "Restoring $DB..."
  docker exec -i <container> mysql -uroot -ppassword "$DB" < "$f"
done
```

---

## Dependencies

All dependencies are standard macOS / Docker tooling — no additional software
or subscriptions required.

| Tool | Used for |
|------|----------|
| `docker` | Running MySQL and executing `mysqldump` inside the container |
| `mysqldump` | Run inside the container via `docker exec` |
| `zip` | Packaging the backup |
| `curl` | Pushover REST API calls |
| `launchd` | macOS-native job scheduling |
| Pushover | Push notifications to iPhone (one-time ~$5 app purchase, no subscription) |

---

## Known behaviour

- `mysqldump` always emits a password-on-command-line warning to stderr. This is
  filtered out in `dump_database()` and does not trigger the error notification.
- If the Mac is asleep at the scheduled time, launchd will not run the job. The
  job will run at the next scheduled time. To catch up on missed runs, set
  `RunAtLoad` to `true` in the plist — this will trigger the job on next login.
- Only one backup zip is kept per date. Running the script twice on the same day
  will overwrite the first zip.
- System databases (`information_schema`, `performance_schema`, `mysql`, `sys`)
  are always excluded from the backup.
