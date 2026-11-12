# Glam Backup — Project Context

Weekly mirror of `/Volumes/Media/glam` (the Media SSD, APFS, about 58 GB in 23,000 files on 2026-11-12) to
`/Volumes/MediaOverflow/Backups/glam` (the MediaOverflow RAID 1, Journaled HFS+) on the production Mac Mini.
Additions **and deletions** are replicated; deleted and changed files are kept for 4 weeks.

---

## What the script does

Every Sunday at 05:00 (after MySQL 02:00, Paprika 03:00 and Paperless 04:00, which also writes to MediaOverflow):

1. **Volumes** — both `/Volumes/Media` and `/Volumes/MediaOverflow` must be mounted (checked with `mount`).
   Otherwise an unmounted target would leave an empty `/Volumes/MediaOverflow` folder on the internal disk for
   rsync to fill.
2. **Unicode names** — `nfd_rename.pl` renames every file and folder in `glam` whose name is not in Unicode NFD
   form to NFD (see below why).
3. **Mirror** — Homebrew's rsync 3:
   `rsync -a --delete --max-delete=500 --backup --backup-dir=…/glam-deleted/YYYY-MM-DD --exclude=.DS_Store`.
   Files deleted from `glam`, and the old version of changed files, are **moved** to
   `/Volumes/MediaOverflow/Backups/glam-deleted/YYYY-MM-DD/` (same relative paths) instead of being removed.
4. **Prune** — dated folders in `glam-deleted` that are 28 days old or older are removed, leaving the last 4
   weekly runs. Only folders named `YYYY-MM-DD` are touched.

Every changed item (`--itemize-changes`) and rsync's stats go to the log.

---

## Files

| File | Committed | Purpose |
|------|-----------|---------|
| `glam_backup.sh` | ✅ | The backup script |
| `nfd_rename.pl` | ✅ | Renames non-NFD names to NFD; `--dry-run` lists them. Uses macOS's `/usr/bin/perl` |
| `glam_backup.conf.template` | ✅ | Config template — production values filled in, Pushover keys empty |
| `glam_backup.conf` | ❌ | Local config with the Pushover keys — never committed |
| `com.jtarnvik.glambackup.plist` | ✅ | launchd agent, Sunday 05:00 |

---

## Setup (on the Mini)

```bash
brew install rsync           # /usr/bin/rsync is Apple's openrsync — not used

cd ~/scripts/glam-backup
cp glam_backup.conf.template glam_backup.conf
chmod 600 glam_backup.conf
# fill in PUSHOVER_USER_KEY and PUSHOVER_API_TOKEN — the same as in the other scripts' conf files

./glam_backup.sh --dry-run   # shows what would be renamed, copied, deleted and pruned; changes nothing
./glam_backup.sh             # run once by hand

cp com.jtarnvik.glambackup.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jtarnvik.glambackup.plist
launchctl list | grep glambackup
tail -f ~/scripts/glam-backup/glam_backup.log
```

The script reads and writes the external volumes itself (unlike the Paperless backup, which goes through
Docker), so the launchd run needs file access to removable volumes. `/bin/bash` has Full Disk Access on the
Mini (granted for the Paprika backup), which covers it.

---

## Notifications

| When | Pushover |
|------|----------|
| Backup done | quiet ✅ with files copied (and size), files deleted, names renamed to NFD, and the time taken |
| A volume is not mounted, `glam` cannot be read, rsync missing | loud ❌ |
| Renaming to NFD failed | loud ❌ — nothing was mirrored |
| More than `MAX_DELETE` (500) deletes | loud ❌ — rsync deleted (moved aside) 500 and stopped deleting; the rest of the mirror is updated |
| Any other rsync error | loud ❌ with the first error lines |
| Pruning `glam-deleted` failed | loud ❌ — the mirror itself is done |

Files that vanish while rsync runs (exit code 24, e.g. a download moved mid-run) are logged, not an error.

---

## Why names are renamed to NFD

A letter like `ö` can be written two ways in Unicode: precomposed (NFC, one code point) or decomposed (NFD,
`o` plus a combining diaeresis). APFS keeps a name exactly as it was written — browsers usually write NFC —
but HFS+ always stores NFD. rsync compares names byte for byte, so the mirror's copy of an NFC-named file looks
like a different file:

- with `--delete` (rsync 3's default *during*), every run deletes the copy and copies it again — and with
  `--backup-dir`, a fresh copy would land in `glam-deleted` every week;
- with `--delete-after` or `--delete-delay`, the file is **missing from the mirror** after every run.

Reproduced on an HFS+ disk image on 2026-11-12 (openrsync). On that day 4 of the 25 non-ASCII names in `glam`
were NFC. APFS treats both forms as the same name, so the rename is safe in place and nothing else notices.
**Do not switch rsync to `--delete-after`/`--delete-delay`** — if a name ever slips through, plain `--delete`
only costs a re-copy.

---

## Restoring

- **A file deleted or changed within the last 4 weeks:** it is in
  `/Volumes/MediaOverflow/Backups/glam-deleted/<date of the run that noticed>/` under its old relative path.
  Copy it back into `/Volumes/Media/glam`.
- **The whole folder** (e.g. a new SSD):
  `/opt/homebrew/bin/rsync -a /Volumes/MediaOverflow/Backups/glam/ /Volumes/Media/glam/`
- **Intentionally deleting more than 500 files:** run once by hand with a higher limit — temporarily raise
  `MAX_DELETE` in `glam_backup.conf`, run `./glam_backup.sh`, set it back.

---

## Known behaviour

- If the Mini is asleep at 05:00 Sunday the run is skipped until the next week (the Mini never sleeps).
- Running twice on the same day uses the same `glam-deleted/YYYY-MM-DD` folder; a file changed twice keeps only
  the version from before the second run.
- `.DS_Store` files are neither copied nor deleted on the mirror.
- Extended attributes (`com.apple.provenance`, quarantine) and ACLs are not copied — only contents, names,
  permissions and modification times.
