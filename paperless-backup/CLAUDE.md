# Paperless Backup — Project Context

Weekly backup of Paperless-ngx running in Docker on the production Mac Mini — decision 14 and step 11 of the
Paperless plan in `home-infra-manual.md` (personal repo). Paperless's database lives in a Docker volume and its
documents in `media/` on the Media SSD; this export is the only backup of Paperless's own work (titles, dates,
tags, correspondents, workflows, users).

---

## What the script does

Every Sunday at 04:00 (after the MySQL backup at 02:00 and Paprika at 03:00):

1. **Export** — `docker exec paperless-webserver document_exporter … --zip` writes
   `paperless-export-YYYY-MM-DD.zip` into the staging folder `~/Backups/paperless` on the internal disk, which
   `docker-compose.yml` bind-mounts as the webserver's export folder. The zip holds everything a fresh install
   needs: `manifest.json` (all database contents), originals, archive copies (the OCR'd PDFs) and thumbnails.
2. **Mirror** — a short-lived `alpine` container mounts the staging folder (read-only) and
   `/Volumes/MediaOverflow/Backups/paperless`, copies the zip under a `.partial` name, compares it byte for byte,
   renames it, and deletes all but the **4 newest** zips there. `--mount` fails if the mirror is not mounted,
   rather than creating the folder on the internal disk the way `-v` would. The script itself never touches the
   external disk, so launchd needs no Removable Volumes permission.
3. **iCloud** — the zip is moved from staging into `iCloud Drive/PrivateData/Backups/Paperless`, then the
   previous zip there is deleted: **only the latest** is kept in iCloud. The MacBook downloads all of
   `PrivateData`, so it holds a copy too, and the quarterly SSD backup picks it up.

Dated zips rather than one folder kept in step, so a mistake in Paperless (documents deleted) does not reach
every copy at the next run.

---

## Files

| File | Committed | Purpose |
|------|-----------|---------|
| `paperless_backup.sh` | ✅ | The backup script |
| `paperless_backup.conf.template` | ✅ | Config template — production values filled in, Pushover keys empty |
| `paperless_backup.conf` | ❌ | Local config with the Pushover keys — never committed |
| `com.jtarnvik.paperlessbackup.plist` | ✅ | launchd agent, Sunday 04:00 |

---

## Setup (on the Mini)

```bash
cd ~/scripts/paperless-backup
cp paperless_backup.conf.template paperless_backup.conf
chmod 600 paperless_backup.conf
# fill in PUSHOVER_USER_KEY and PUSHOVER_API_TOKEN — the same as in ../paperless-ingest/paperless_ingest.conf

./paperless_backup.sh        # run once by hand

cp com.jtarnvik.paperlessbackup.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jtarnvik.paperlessbackup.plist
launchctl list | grep paperlessbackup
tail -f ~/scripts/paperless-backup/paperless_backup.log
```

The plist's `PATH` includes `/usr/local/bin` (where `docker` links into Docker.app) and Docker.app's own `bin`
folder, since launchd starts jobs with a minimal `PATH`.

---

## Notifications

| When | Pushover |
|------|----------|
| Backup done | quiet ✅ with the zip's size, the time taken and the number of zips on the mirror |
| Webserver not running or Docker unreachable | loud ❌ |
| `document_exporter` failed, or wrote no zip | loud ❌ |
| Copy to the mirror failed (not mounted, disk full, copy differs) | loud ❌ — the zip stays in staging and the next run removes it |
| Move to iCloud failed | loud ❌ — the zip is on the mirror already |

---

## Restoring

Paperless's documented path is `document_importer` into an **empty** Paperless of the **same version**
(3.4.1 at the time of writing):

```bash
# unzip, then point the importer at the folder (it reads manifest.json)
unzip paperless-export-YYYY-MM-DD.zip -d ~/Backups/paperless/restore
docker exec paperless-webserver document_importer /usr/src/paperless/export/restore
```

The import refuses a database that already holds documents. For a full rebuild: stop the stack, remove the
Postgres volume and `media/` contents, start the stack (migrations create an empty database), create no user,
and import — the export brings the users back.

---

## Known behaviour

- If the Mini is asleep at 04:00 Sunday the run is skipped until the next week (the Mini never sleeps).
- Running twice on the same day overwrites that day's zip in every place.
- The export holds documents in clear: the iCloud copy is as private as the iCloud account.
