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
| `restoretest-compose.yml` | ✅ | Throwaway Paperless for testing a zip (see Restore test) |

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

`document_importer` reads the zip directly, into an **empty** Paperless of the **same version** that exported
it (3.4.1 at the time of writing). The export brings back everything — documents with their files, tags,
correspondents, types, workflows, users and settings — so the empty instance needs no user of its own
(leave `PAPERLESS_ADMIN_USER` unset).

A full rebuild of production, in outline: stop the stack, remove the Postgres volume and the contents of
`media/`, start the stack (migrations create an empty database), then

```bash
docker exec paperless-webserver document_importer /usr/src/paperless/export/paperless-export-YYYY-MM-DD.zip
```

with the zip first copied into `~/Backups/paperless` (the export folder the webserver sees).

## Restore test

`restoretest-compose.yml` starts a throwaway Paperless beside production (see the file's header). First run
2026-11-04, on the first backup: import 32 s; documents, tags, correspondents, types, workflows, users and a
fingerprint over every document's ID, title, date, correspondent, type and tags all equal to production;
`document_sanity_checker` gave no errors or warnings on either (the same 14 infos, "no OCR data", on both).

```bash
mkdir ~/paperless-restoretest && cp ~/scripts/paperless-backup/restoretest-compose.yml ~/paperless-restoretest/
cd ~/paperless-restoretest
RESTORETEST_SECRET_KEY=$(openssl rand -hex 32) docker compose -f restoretest-compose.yml up -d
docker inspect -f '{{.State.Health.Status}}' paperless-restoretest-webserver-1    # wait for "healthy", ~35 s
docker exec paperless-restoretest-webserver-1 document_importer /backup/paperless-export-YYYY-MM-DD.zip
docker exec paperless-restoretest-webserver-1 document_sanity_checker --no-progress-bar | grep -E 'ERROR|WARNING|Found'

# compare with production: run in both containers
docker exec <container> python3 /usr/src/paperless/src/manage.py shell -c \
  "from documents.models import Document, Tag; print(Document.objects.count(), Tag.objects.count())"

RESTORETEST_SECRET_KEY=x docker compose -f restoretest-compose.yml down -v && cd ~ && rm -r ~/paperless-restoretest
```

---

## Known behaviour

- If the Mini is asleep at 04:00 Sunday the run is skipped until the next week (the Mini never sleeps).
- Running twice on the same day overwrites that day's zip in every place.
- First run 2026-11-04 (2,528 documents): 3 min, a 2.8 GB zip of 5,973 files — PDFs barely compress.
- The export holds documents in clear: the iCloud copy is as private as the iCloud account.
