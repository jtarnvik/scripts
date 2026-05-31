# RAID Watch — Project Context

This repo contains a daily and weekly monitoring solution for Apple software RAID sets
running on a Mac Mini home server. The Mac Mini is not publicly exposed.

---

## What the project does

- Queries macOS Apple RAID status via `diskutil appleRAID list`
- **Daily check**: sends a Pushover alert only if a degraded/failed RAID is detected
- **Weekly heartbeat**: always sends a summary of all RAID sets regardless of status
- Runs via two macOS `launchd` agents (daily 08:00, weekly Monday 09:00)

---

## Files

| File | Committed | Purpose |
|------|-----------|---------|
| `raid_watch.sh` | ✅ | Main monitoring script |
| `raid_watch.conf.template` | ✅ | Template showing required config variables |
| `raid_watch.conf` | ❌ | Local secrets — created from template, never committed |
| `com.jtarnvik.daily.raidwatch.plist` | ✅ | macOS launchd agent for daily check (08:00) |
| `com.jtarnvik.weekly.raidwatch.plist` | ✅ | macOS launchd agent for weekly heartbeat (Mon 09:00) |
| `.gitignore` | ✅ | Excludes `*.conf` and `*.log` |

---

## Configuration

All secrets live in `raid_watch.conf` (excluded from git via `.gitignore`). To set up on a new machine:

```bash
cp raid_watch.conf.template raid_watch.conf
# edit raid_watch.conf and fill in both variables
```

Required variables:

| Variable | Description |
|----------|-------------|
| `PUSHOVER_USER_KEY` | Pushover user key (from pushover.net dashboard) |
| `PUSHOVER_API_TOKEN` | Pushover API token (from your registered app) |

---

## Script structure

`raid_watch.sh` is organised as functions:

- `load_config()` — sources `raid_watch.conf` and validates all variables are set
- `send_pushover()` — sends a push notification via the Pushover REST API
- `get_raid_status()` — runs `diskutil appleRAID list` and returns raw output
- `extract_summary()` — filters the raw output to lines useful for a status notification
- `extract_problems()` — filters the raw output for lines indicating a degraded/failed RAID
- `main()` — orchestrates the flow; mode is set by the first argument (`check` or `heartbeat`)

---

## Scheduling (launchd)

Two plist files register launchd agents for the current user (not system-wide).

```bash
# Install (copy plists and register — do once, survives reboot)
cp ~/scripts/raid-watch/com.jtarnvik.daily.raidwatch.plist ~/Library/LaunchAgents/
cp ~/scripts/raid-watch/com.jtarnvik.weekly.raidwatch.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jtarnvik.daily.raidwatch.plist
launchctl load ~/Library/LaunchAgents/com.jtarnvik.weekly.raidwatch.plist

# Unregister
launchctl unload ~/Library/LaunchAgents/com.jtarnvik.daily.raidwatch.plist
launchctl unload ~/Library/LaunchAgents/com.jtarnvik.weekly.raidwatch.plist

# Trigger manually (e.g. for testing)
launchctl start com.jtarnvik.daily.raidwatch
launchctl start com.jtarnvik.weekly.raidwatch

# Check if registered
launchctl list | grep jtarnvik

# View logs
tail -f ~/scripts/raid-watch/raid_watch.log
```

The plists have paths hardcoded for the Mac Mini (`~/scripts/raid-watch/`). If
deploying to a different machine, update the script path, log paths, and
`EnvironmentVariables/PATH` to match the new environment.

---

## Testing the script manually

```bash
chmod +x raid_watch.sh

# Daily check (only notifies if something is wrong)
./raid_watch.sh check

# Weekly heartbeat (always sends status)
./raid_watch.sh heartbeat
```

Output goes to stdout and to the log file defined in the plist when run via launchd.

---

## Dependencies

| Tool | Used for |
|------|----------|
| `diskutil` | Querying macOS Apple RAID status |
| `curl` | Pushover REST API calls |
| `launchd` | macOS-native job scheduling |
| Pushover | Push notifications to iPhone (one-time ~$5 app purchase, no subscription) |

---

## Known behaviour

- The daily `check` mode sends **no notification** when all RAID sets are healthy — silence is good news.
- The weekly `heartbeat` always sends, so you have a regular confirmation that the job is running.
- If `diskutil appleRAID list` returns empty output (e.g. the command fails), an error alert is sent.
- If the Mac is asleep at the scheduled time, launchd will not run the job. The job will run at the next scheduled time.
- Problem detection watches for: `Failed`, `Degraded`, `Offline`, `Missing`, `Rebuilding`.
