# RAID Watch — Project Context

This repo contains a daily and weekly monitoring solution for Apple software RAID sets
running on Mac Mini home servers. It runs on two machines, each with its own
`raid_watch.conf`: the production Mini (192.168.1.204) and the media server Mini
(192.168.1.96). Neither is publicly exposed.

---

## What the project does

- Queries macOS Apple RAID status via `diskutil appleRAID list -plist`
- **Daily check**: sends a Pushover alert only if a problem is detected
- **Weekly heartbeat**: sends a summary of all RAID sets — or, if there is a problem, the same alert as the
  daily check
- Runs via two macOS `launchd` agents (daily 08:00, weekly Monday 09:00)

A problem is any of:

- a RAID set whose status is not `Online`
- a member whose status is not `Online` — `Failed`, `Rebuilding`, or a disk that is gone (shown as `Missing`)
- a mirror with fewer than two members. A mirror whose failed member has been removed reports `Online`
  with a single disk, so its status alone is not enough
- a RAID set listed in `EXPECTED_RAID_SETS` that is not present at all

## Notifications

Titles name the machine (`HOST_LABEL`) and use only ✅ and ❌:

| Title | When | Priority |
|-------|------|----------|
| `✅ RAID OK - <HOST_LABEL>` | Weekly heartbeat, no problems | 0 |
| `❌ RAID problem - <HOST_LABEL>` | Any problem, daily or weekly | 1 (high) |
| `❌ RAID watch error - <HOST_LABEL>` | diskutil failed, hung, or returned something unreadable | 1 (high) |

The body lists the problems first, then the status of every set, for example:

```
Problems:
MediaBack2: status is Degraded
MediaBack2: member disk5s2 is Rebuilding 37%

Status:
MediaStore: Online (Mirror, rebuild Automatic)
  disk8s2: Online
  disk7s2: Online
MediaBack2: Degraded (Mirror, rebuild Automatic)
  disk4s2: Online
  disk5s2: Rebuilding 37%
```

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
# edit raid_watch.conf and fill in all variables
chmod 600 raid_watch.conf
```

Required variables:

| Variable | Description |
|----------|-------------|
| `PUSHOVER_USER_KEY` | Pushover user key (from pushover.net dashboard) |
| `PUSHOVER_API_TOKEN` | Pushover API token (from your registered app) |
| `HOST_LABEL` | Machine name shown in notification titles, e.g. `Production`, `Media server` |
| `EXPECTED_RAID_SETS` | Bash array of the RAID set names that must be present, e.g. `("MediaStore" "MediaBack2")` |

The config file is sourced by bash, so `EXPECTED_RAID_SETS` is a real array and names may contain spaces.
A set that is present but not listed is still checked; listing it only adds the "not found" check.

---

## Script structure

`raid_watch.sh` is organised as functions:

- `load_config()` — sources `raid_watch.conf` and validates all variables are set
- `send_pushover()` — sends a push notification via the Pushover REST API; logs an error if sending fails
- `get_raid_plist()` — runs `diskutil appleRAID list -plist`, killed after `DISKUTIL_TIMEOUT_SECONDS` (120)
- `plist_value()` — reads one value from the plist with `plutil -extract` (an array gives its length)
- `contains()` — list membership helper
- `analyse_raid()` — walks the RAID sets and their members, and sets the globals `SUMMARY` and `PROBLEMS`
- `main()` — orchestrates the flow; mode is set by the first argument (`check` or `heartbeat`)

The plist is parsed rather than the human-readable output: it has fixed key names (`Status`, `Members`,
`MemberStatus`, `MemberRebuildStatus` …), and a member whose disk is gone simply lacks `BSD Name` and
`MemberStatus`. When there are no RAID sets at all, the plist is an empty dict without `AppleRAIDSets`.

macOS ships bash 3.2 as `/bin/bash`, which the plists use — so no associative arrays or `mapfile`, and
`source <(...)` does not work.

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
| `plutil` | Reading values from diskutil's plist output |
| `perl` | Timeout around `diskutil` (macOS has no `timeout` command) |
| `curl` | Pushover REST API calls |
| `launchd` | macOS-native job scheduling |
| Pushover | Push notifications to iPhone (one-time ~$5 app purchase, no subscription) |

---

## Known behaviour

- The daily `check` mode sends **no notification** when all RAID sets are healthy — silence is good news.
- The weekly `heartbeat` always sends, so you have a regular confirmation that the job is running.
- A rebuilding mirror is reported as a problem every day until the rebuild finishes. That is intended: the set
  has no redundancy until then.
- If `diskutil` fails, does not answer within 120 seconds, or returns something that is not a plist, an error
  alert is sent. diskutil has been seen to hang while a RAID member was not responding (2026-11-03).
- A failed Pushover send is logged as `ERROR: Pushover notification failed: …`. The weekly heartbeat is the way
  to notice if notifications stop arriving.
- If the Mac is asleep at the scheduled time, launchd will not run the job. The job will run at the next scheduled time.
