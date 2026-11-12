# Paperless-ngx Introduction Plan

*Planned 2026-10-23, written 2026-10-30, revised 2026-10-31 with the lessons from moving Alloy out of
Docker, all decisions settled 2026-11-03. Steps 1–11 done 2026-11-04, step 12 on 2026-11-05 — **the plan is
done.** Paperless is in operation; how to run it is in `home-infra-manual.md` (personal repo), under Mac Mini —
Local Services → Paperless-ngx. This file stays as the record of why it is built the way it is.*

*This plan lived in `home-infra-manual.md` until 2026-11-07. Where the text says "here", "Overview", "Follow-ups"
or "Log retention → History", it means that manual.*

Paperless-ngx is an open-source, self-hosted document archive: it OCRs everything it ingests, makes it
full-text searchable, and classifies it with tags, correspondents and document types. It is to run in Docker on
the Mini and hold searchable **copies** of the documents filed by Hazel on the MacBook Pro. The archive is
`iCloud Drive/PrivateData/Dokument Arkiv`: 2,650 files, 2.5 GB (2026-11-03) — 2,481 PDFs, about 100 images,
about 25 Office files — which becomes roughly 5–6 GB in Paperless, since it keeps each original plus an archived
PDF/A copy and thumbnails.

## Settings at a glance

| What | Value |
|---|---|
| Base folder | `/Volumes/Media/production/paperless` — compose files, `media/`, `consume/` |
| Web UI | http://192.168.1.204:9400 — **LAN only**, never a Caddy block or a port forward |
| Admin user | `jesper`, with a proper password set at first login (not written here). The only user |
| Ingest folder | `iCloud Drive/PrivateData/_paperless_uploads` (Keep Downloaded on the Mini) |
| Rejected files | `iCloud Drive/PrivateData/_paperless_failed` |
| Scripts | `scripts` repo: `paperless-ingest/` (ingest on the Mini, launchd every 60 s; backfill on the MacBook), `paperless-backup/` |
| Backups | Weekly zip: last 4 on `/Volumes/MediaOverflow/Backups/paperless`, latest in `iCloud Drive/PrivateData/Backups/Paperless` |
| Capacity | `PAPERLESS_TASK_WORKERS=2`, `PAPERLESS_THREADS_PER_WORKER=2`; Docker Desktop's VM stays at 8 GB |

## Decisions

1. **Paperless holds copies, not the originals.** The Hazel workflow on the MacBook Pro stays the system of
   record: Downloads → *Nya - ej hanterade*, where Hazel renames documents from recurring sources (e.g.
   `SBAB Lön - 2603.pdf`) and the rest are renamed by hand → moved into a folder named *Införda* → Hazel moves
   them flat into the year folder (its rule is updated every year; a name clash gets ` (1)`). Some are later moved
   by hand into subfolders such as `Årsbesked`. The year folders, plus the quarterly copy to an SSD, remain the
   backup. Paperless may ingest and delete what it is given.
2. **Transport is iCloud Drive.** When Hazel moves a document into a year folder, it also copies it to
   **`PrivateData/_paperless_uploads`**, which syncs to the Mini. Keep Downloaded is set on that folder on the
   Mini (checked 2026-11-03: `xattr` shows `com.apple.fileprovider.pinned#PX`), so files arrive with their content
   rather than as placeholders. The copies keep their names; Hazel's rename rules are not changed for Paperless.
3. **Documents go in through Paperless's API, not a watched folder.** On the Mini, a **launchd job** running
   every 60 seconds uploads each file in the ingest folder to `/api/documents/post_document/`, and deletes it
   once Paperless has consumed it (a failed one goes to `_paperless_failed`). `QueueDirectories` was the plan,
   but launchd's folder trigger never fires for a folder in iCloud Drive (step 4). Hazel is not installed on the Mini
   and is not needed there. Paperless never reads iCloud, and never reads a folder that a host process writes to:
   - iCloud can leave a synced file as a placeholder whose content is fetched only on open, and mounting
     `~/Library/Mobile Documents/...` is fragile.
   - A container reads host-written files through Docker Desktop's file sharing, which showed Alloy stale sizes
     and modification times. Paperless decides a file in its consume folder is complete when its size and time
     stop changing, so a stale view could hand it a half-copied file. A late or missed file would be as silent as
     Alloy's lag was.
   - An upload either succeeds or fails. The response is a task UUID; `/api/tasks/?task_id=<uuid>` shows how
     consumption went, including the new document's ID (version 3: lowercase `status`, `related_document_ids` —
     see step 3).
   - The job touches only the internal disk, so no Removable Volumes permission is needed. The MySQL backup
     already writes into iCloud Drive from launchd every week.

   Authentication is a header, `Authorization: Token <token>`; the token comes from *My Profile* in the web UI.
4. **Docker Compose, written by hand** from the official `docker-compose.postgres-tika.yml` template, not
   the install script (which targets Linux). Image tags pinned, as for Loki and Grafana. Paperless stays in
   Docker even though Alloy left it: a native install on macOS is unsupported (Python, Redis, Postgres, Tesseract,
   Ghostscript, plus Tika and Gotenberg), and with uploads through the API nothing in Paperless reads files a host
   process writes. Its own `media/` is written by the container itself, which file sharing handles fine — the
   same reason Loki and Grafana stayed.
5. **Database: Postgres, from the template**, in a **Docker-managed named volume**, not a bind mount on the
   external SSD — Docker Desktop has permission and write-safety problems with databases there. This splits
   Paperless across two disks: the database inside Docker's disk image on the internal drive, the documents in
   `media/` on the SSD. The two must never run apart — see decision 10.
6. **Tika + Gotenberg** included, for the Office files in the archive (`.doc`, `.docx`, `.xlsx`, `.rtf`).
7. **OCR in Swedish and English.** Needs two settings: `PAPERLESS_OCR_LANGUAGES=swe` installs the Swedish
   pack (not bundled), `PAPERLESS_OCR_LANGUAGE=swe+eng` uses both. Without the first, OCR fails.
8. **`consume/` stays, for occasional hand drops only.** The template mounts it, and it is handy for a single
   file. It needs `PAPERLESS_CONSUMER_POLLING_INTERVAL=30` (named `PAPERLESS_CONSUMER_POLLING` before version 3 —
   the old name is silently ignored): Docker Desktop on macOS does not pass file events through bind
   mounts, so without it the folder is silently never read. Check that a hand-dropped file arrived.
9. **`media/` on the Media SSD, not on the MediaOverflow mirror.** The mirror's disks sleep after 10 minutes, so
   every first look after a quiet spell would wait for them to spin up; everything in `media/` is a copy; and the
   export (decision 14) is the protection for Paperless's own work. Every other service lives on the SSD too.
10. **Bind mounts that cannot run against the wrong folder.** Every bind mount uses the long syntax with
    `create_host_path: false`. If the Media SSD is not mounted when Docker Desktop starts after a power cut, the
    containers then fail to start, rather than Docker creating an empty folder at the same path on the internal
    disk — which would leave Paperless with a database pointing at files that are not there, and could make the
    real SSD mount as `Media 1`. **Never delete and recreate** a mounted folder: a bind mount keeps the folder
    that existed when the container started (see Log retention → History). Paperless itself does **not** mount
    the MediaOverflow mirror, so a missing or degraded mirror can fail the weekly backup but never stop
    Paperless (decision 14).
11. **Dates are set by the upload scripts, from the filename.** Paperless's own filename parsing cannot read
    the archive's `2603` form, and the date it otherwise reads from the content is often the first one it finds,
    such as a period start. The daily ingest and the backfill share one parser and send the result as `created`.
    In order:
    - a full date in the name (`2026-03-01`, `20260301`; 29 files) — as it is;
    - a name ending ` - NNNN` where NNNN is a valid `YYMM` — month 01–12, and 20YY at most one year from the year
      folder (the current year, for new documents) — becomes the first of that month: `SBAB Lön - 2603` →
      2026-03-01. About 515 files; 509 of them sit in the year folder of their own year. The year check keeps
      `… - 2012` in a 2012 folder from being read as December 2020;
    - a name ending ` - YYYY` (about 20 files, mostly årsbesked) — 31 December of that year, the year it covers;
    - no date in the name (about 560 files) — nothing is sent, and Paperless reads the date from the content.
      After the backfill, the script lists documents whose date is more than a year from their year folder, for
      review.

    A trailing ` (1)`-style suffix from Hazel (25 files) is removed from the title, which is otherwise the
    filename. `PAPERLESS_FILENAME_DATE_ORDER` is not set.
12. **Classification of new documents, in three layers.** The ingest folder is flat: the copy is made when Hazel
    moves a document flat into the year folder, so no folder information exists at that moment, and later moves
    by hand (into `Årsbesked`, `Införda i Förmögenhetsdoc` …) are invisible to Paperless.
    - **A filename workflow per Hazel rename rule** — trigger *Consumption started*, source including API
      uploads, filename filter such as `SBAB Lön*`, assigning correspondent, document type and tags. A new Hazel
      rename rule gets a matching workflow.
    - **Automatic matching** (*Auto*) on correspondents, document types and tags, trained on the backfill — over
      600 årsbesked and about 460 Nordea documents.
    - **An inbox tag**, given to every new document, which the owner reviews in the web UI: correct what is
      wrong (which also trains the matching), then remove the tag.

    These are expected to be adjusted once in use; the backfill is the part to get right.
13. **The backfill runs from the MacBook Pro**, not from the quarterly SSD on the Mini. The MacBook has the whole
    archive downloaded (no placeholders, checked 2026-11-03) and reaches the API over the LAN, so the script reads
    `Dokument Arkiv` directly — nothing is copied or moved. Uploading takes minutes; Paperless then works through
    its queue on its own, for hours, and the MacBook may sleep.
    - **Skipped:** the folder **`Dokument Arkiv/_Paperless-ignored`**, as a whole — it holds `HelensDoc`, `Max`
      (historical, and kept for others) and `Manual` (moved there 2026-11-04; 35 files, 200 MB). Anything wanted
      from there goes through the normal ingest, and anything moved there later is skipped too. Also skipped:
      hidden files, and types Paperless cannot take (`psd`, `zip`, `pxm`; 36 files).
    - **Scope** (2026-11-04): 2,617 files, 2.5 GB — 2,461 PDF, 88 images (`jpg`, `jpeg`, `png`, `tif`), 26 Office
      files (`doc`, `docx`, `rtf`, `xlsx`), 5 `txt`, and the 36 skipped. One Word file has no extension
      (`Husbygge/Before sort/Privat ombyggnad - Huset/inredning toalett`); the dry run shows whether Paperless
      takes it as it is.
    - **Classified by a mapping file:** folder name → correspondent (`Nordea`, `SEB`, `Skandia`,
      `Länsförsäkringar`, `Trygg Hansa`, `Telia` …), document type (`Årsbesked`, `Lön`, `Kvitton`, `Kontrakt`,
      `AktieNota` …) or tag (`Husbygge`, `Semester`, `Inbrott` …). A file gets the mapping of every folder in its
      path, so `2020/Årsbesked/Införda/x.pdf` gets the type Årsbesked; unlisted folders (years, `Införda`,
      `Before sort` …) add nothing. Variants such as `Trygg hansa`/`Trygg Hansa` map to one name.
    - **Tagged `Backfill`**, so the imported documents — and the test batch — can be found and handled together.
    - `--dry-run` lists every file with the classification, date and title it would get, and uploads nothing.
      A path option selects the test batch. A done-list makes a re-run skip what is already uploaded (Paperless
      also refuses duplicates by checksum, through `PAPERLESS_CONSUMER_DELETE_DUPLICATES=true` — see step 4). At the end it compares what it uploaded with what Paperless consumed
      and lists failed tasks.
14. **Backups: a weekly export, kept in two places.** `document_exporter` writes everything a fresh install needs
    — database contents (tags, correspondents, document types, workflows, users, settings), originals and archive
    copies. The Postgres volume is backed up only through this. A launchd job, Sunday 04:00 (after the MySQL backup
    at 02:00 and Paprika at 03:00):
    1. `docker exec <webserver> document_exporter … -z` writes a dated zip, `paperless-export-YYYY-MM-DD.zip`,
       into a **staging folder on the internal disk**, `~/Backups/paperless` (bind-mounted into the webserver).
    2. A short-lived container (`docker run --rm`, a small pinned image) with the staging folder and
       `/Volumes/MediaOverflow/Backups/paperless` mounted copies the zip to the mirror and deletes all but the
       **4 newest** there. It uses `--mount`, which fails if the mirror is not mounted, rather than `-v`, which
       would create the folder on the internal disk.
    3. The script moves the zip from staging into `iCloud Drive/PrivateData/Backups/Paperless`, then deletes the
       previous one there — **only the latest** is kept in iCloud. The MacBook downloads all of `PrivateData`,
       so it holds a copy too, and the quarterly SSD backup picks it up.

    The launchd script itself never touches an external disk — Docker does all reading and writing there — so no
    silent Removable Volumes prompt. Dated zips rather than one folder kept in step, so a mistake in Paperless
    (documents deleted) does not reach every copy at the next run. Pushover: a quiet ✅ on success, a loud ❌ on
    any failure.
15. **Pushover only when something needs attention.** The ingest job alerts on a rejected file (moved to
    `_paperless_failed`) and when Paperless has been unreachable for about 30 minutes — not on every retry, and
    not on successful uploads. The backup job sends its weekly ✅/❌.

## Steps

1. ✅ **Prepare the Mini.** *Done 2026-11-03.*
   - Create the base folder `/Volumes/Media/production/paperless` with `media/` and `consume/`.
   - Create `/Volumes/MediaOverflow/Backups/paperless` and `~/Backups/paperless`.
   - Create `iCloud Drive/PrivateData/_paperless_failed`. (`_paperless_uploads` exists, with Keep Downloaded.)
2. ✅ **Write `docker-compose.yml` and `docker-compose.env`** from the official Postgres + Tika template.
   *Done 2026-11-03.* Both are in the base folder (not a git repository; back up changes as `<file>.bak-<reason>`),
   and each explains its own deviations from the template. `docker compose config` accepts them; nothing has
   been started.
   - **Versions, all pinned:** `paperless-ngx:3.4.1` (released 2026-10-31), `postgres:18.6`,
     `valkey:9.1.3-alpine` (the template's Redis-compatible broker), and the template's own `gotenberg:8.37.0`
     and `tika:3.3.1.0`. The template is identical at the `v3.4.1` tag and on `main`.
   - **Compose:** project `name: paperless` (in place of the template's `.env`); `restart: unless-stopped`;
     Postgres, the broker and Paperless's `data` (search index, classifier, logs) in Docker volumes; bind mounts
     `media/`, `consume/` and `~/Backups/paperless` (as the export folder), long syntax with
     `create_host_path: false`; container name `paperless-webserver`; port `9400:8000`. The template's Postgres
     password is kept: Postgres publishes no port.
   - **Env (`chmod 600`):** `USERMAP_UID=501`/`USERMAP_GID=20` (jesper/staff), `PAPERLESS_URL`,
     `PAPERLESS_ALLOWED_HOSTS=127.0.0.1`, the secret key, `PAPERLESS_TIME_ZONE=Europe/Stockholm`, the two OCR
     language settings, `PAPERLESS_CONSUMER_POLLING_INTERVAL=30`, the worker and thread counts.
     **`PAPERLESS_URL` also limits the host names Paperless answers to** — only its own, plus `localhost`, which is
     always allowed — so without `PAPERLESS_ALLOWED_HOSTS=127.0.0.1` every call to `127.0.0.1:9400` (the ingest
     script, the smoke test) would be refused.
   - **The secret key** was generated on the Mini with `openssl rand -hex 32` and written straight into the env
     file; it has never been printed or copied.
3. ✅ **Start and smoke-test.** *Done 2026-11-03.* `docker compose pull`, `docker compose up -d`: healthy after
   about 30 seconds, the Swedish language pack installed on first start (`tesseract --list-langs` shows `swe`),
   `consume/` watched "using polling (interval: 30.0s)". About 1.1 GB of memory for the stack at rest.
   - **Admin user:** with no users, version 3 forwards the web UI to a **first-run signup page**
     (`/accounts/signup/`); `jesper` was created there, as a superuser, and the page closes once a user exists.
     The API token (*My Profile* → *API Auth Token*) was stored by the owner, from the MacBook's clipboard,
     without passing through Claude:
     `pbpaste | ssh jesper@192.168.1.204 'umask 077; tr -d "[:space:]" > ~/.config/paperless/token'`
     (`~/.config/paperless/` is `700`). Without the token the API answers `401`.
   - **Smoke test**, uploaded with
     `curl -sf -H "Authorization: Token $(cat ~/.config/paperless/token)" -F "document=@<file>" http://127.0.0.1:9400/api/documents/post_document/`:
     a Swedish PDF with a text layer (read as is, no OCR, so no archive copy), an English page as an image-only
     PDF (OCR correct but for "lazy" → "Lay"), a photographed Swedish letter as `.jpg` (OCR'd into a PDF;
     readable, noisy), a `.docx` (converted to PDF through Tika/Gotenberg) — and a PDF dropped into `consume/`,
     picked up by polling. All consumed. Then deleted, and the trash emptied (`POST /api/trash/`,
     `{"action": "empty"}`), so the backfill can import them again — a document in the trash still counts as a
     duplicate.
   - **Missing-disk test**, without unmounting the Media SSD (that would stop nearly everything on the Mini): a
     throwaway one-container project, bind-mounting `/Volumes/NoSuchDisk/...` with `create_host_path: false`.
     `docker compose up` failed with `bind source path does not exist`, and nothing was created under `/Volumes`.
     The real thing — Docker Desktop starting before the SSD after a power cut — belongs with the power-cut
     recovery test under Follow-ups.
   - **Found for the later steps:**
     - **The tasks API changed in version 3** (API version 10): `status` is lowercase (`success`, `failure` …), and
       the file name, result and document ID are in `input_data`, `result_data` and `related_document_ids`. A
       script that waits for `SUCCESS` waits for ever.
     - **A `.docx` got its date from the file's own metadata** (2025-12-28) although its name starts `2018 - …`.
       Names that *start* with a year are not covered by decision 11; the backfill dry run will show how many
       there are.
     - **A document with no date in its name or content gets the upload date.** In the backfill these appear on
       the review list.
     - Memory during OCR was not measured — measure it during the test batch (step 9).
4. ✅ **`scripts/paperless-ingest/`: the ingest script and its launchd job** on the Mini. *Done 2026-11-03.*
   The folder's `CLAUDE.md` describes it in full; in short:
   - **Python, behind a bash wrapper.** `paperless_ingest.py` (macOS's `/usr/bin/python3`, 3.9, standard library
     only) does the work; `paperless_ingest.sh` loads `paperless_ingest.conf` and runs it as a child of `/bin/bash`,
     which has Full Disk Access and so may read iCloud Drive. `paperless_names.py` holds decision 11's rules, with
     unit tests, and is shared with the backfill. The conf (`600`) holds the Pushover keys, filled in by the owner,
     and points at the token file `~/.config/paperless/token`.
   - **launchd job `com.jtarnvik.paperlessingest`, every 60 seconds** (`StartInterval`, plus `RunAtLoad`). Log:
     `~/scripts/paperless-ingest/paperless_ingest.log`. An idle run lists `_paperless_uploads` and
     `_paperless_failed` and exits — no API call, no log line, no write; everything it reads is on the internal
     SSD.
   - **A file is kept until Paperless has consumed it**, not only until the upload is accepted: an accepted upload
     moves to `~/.local/state/paperless-ingest/pending/<task id>/`; the next run deletes it when the task succeeds,
     or moves it to `_paperless_failed` with an alert when it fails (a duplicate, a corrupt file …).
   - Skipped until downloaded: files iCloud has only as placeholders (the `SF_DATALESS` flag). Moved to
     `_paperless_failed` with an alert: files Paperless refuses at upload (HTTP 4xx, e.g. a `.zip`), subfolders,
     hidden files. `.DS_Store` is deleted.
   - Paperless unreachable: files wait and are retried every minute; one alert after 30 minutes. A refused token
     alerts at once. Lasting conditions alert once until they clear (`state.json`).
   - **Tested** against a test folder (dates and titles, a decomposed `å`, refused `.zip`, hidden file, subfolder,
     duplicate, unreachable, wrong token) and live through `_paperless_uploads` under launchd. A plain date in
     `created` (`2026-11-01`) is stored as that day. Test documents deleted, trash emptied.

   **Found while writing it:**
   - **`QueueDirectories` does not work on a folder in iCloud Drive.** launchd's folder trigger never fired for
     `_paperless_uploads` — neither for a new file nor a touched one — while the same trigger on a plain folder
     fired within a second. Hence the 60-second timer, which does not depend on how macOS reports changes inside
     iCloud Drive (decision 3 updated).
   - **Paperless 3 does not refuse duplicates by default.** A second upload of the same content was consumed as a
     new document; the two were only linked (`duplicate_documents`). Set `PAPERLESS_CONSUMER_DELETE_DUPLICATES=true`
     in `docker-compose.env` (backup `docker-compose.env.bak-delete-duplicates`) and recreated the webserver: now
     the task fails with `result_data` `{"duplicate_of": <id>, "duplicate_in_trash": …}`, and the ingest script
     moves the file to `_paperless_failed` with "Duplicate of document #<id>". A document in the trash counts too.
   - The Mini does not hold all of `PrivateData` downloaded — over 2,000 files there are placeholders (e.g. in
     `Book Series`). Only `_paperless_uploads` needs Keep Downloaded, and has it.
5. ✅ **Hazel on the MacBook Pro: also copy to `_paperless_uploads`** in the rule that moves documents into the
   year folder. Names unchanged. *Done 2026-11-04.*
   - The filing rules are the two in the Hazel folder `Nya - ej hanterade/Införda`: `Flytta Införda Nordea till
     2020 Nordea` (→ `Dokument Arkiv/2026/Nordea`) and `Flytta Införda filer till 2020` (→ `Dokument Arkiv/2026`;
     the names still say 2020). Each got a **Copy** action to `PrivateData/_paperless_uploads` **after** its Move,
     so the copy carries the final name, including a clash's ` (1)`; on a name clash in the ingest folder the copy
     is renamed, not replaced. The yearly update of the Move targets leaves the Copy alone.
   - No other Hazel folder watches `_paperless_uploads` or a folder above it.
   - **Found:** the rename rule `Byt namn på IF Hemförsäkr` (folder `Nya - ej hanterade`) had a literal `.` before
     the extension token, which already includes the dot, giving `IF - Hemförsäkr - 2609..pdf` — a name decision 11
     reads no date from. Fixed in Hazel, and the only two such files (`2026/IF - Hemförsäkr - 2609`, `2610`)
     renamed. The other seven rename rules are `<prefix> - ` + the filing date as `YYMM` + extension.
   - Checked by decoding Hazel's rule files (`~/Library/Application Support/Hazel/<folder id>.hazelrules`: a
     `HZLR` header, then a keyed-archive plist), read-only.
6. ✅ **End-to-end test:** file one new document the normal way and confirm it appears in Paperless within a
   minute or two of iCloud syncing, with the right date. Stop Paperless (`docker compose stop`), file another,
   check that it waits in the ingest folder and is uploaded once Paperless is back. Put an unsupported file in the
   ingest folder and check that it ends up in `_paperless_failed` with an alert. *Done 2026-11-04.*
   - **Filed normally:** `El - Ellevio - 2611.pdf` moved into `Införda` → uploaded about 35 seconds later, consumed
     at the next run: document 13, `El - Ellevio - 2611`, 2026-11-01 (the invoice itself says 3 November; the
     ` - YYMM` rule gives the first of the month, as decided), OCR readable. About 1.5 minutes in all, and the copy
     gone from `_paperless_uploads` on both machines.
   - **Paperless stopped** (`docker compose stop`): `Polisrapport - Vigselring forlust-2026-10-26.pdf` waited, with
     `Paperless unreachable, will retry` in the log each minute. After `docker compose start` the webserver was
     healthy in 31 seconds and the next run uploaded it: document 14, dated 2026-10-26 (full date in the name). The
     outage was kept under 30 minutes, so no alert.
   - **Unsupported file:** a `.zip` copied into `_paperless_uploads` on the MacBook was refused ("File type
     application/octet-stream not supported"), moved to `_paperless_failed` (synced back to the MacBook), and the
     Pushover alert arrived. Test `.zip` deleted.
   - Documents 13 and 14 are real and stay; the backfill will see them as duplicates, which is expected.
   - **Found:** at 21:07 the evening before, listing the ingest folder failed once with `Interrupted system call`
     (`EINTR`) from iCloud Drive, and the script sent a "script error" alert. Nothing was lost — the next run a
     minute later was clean. The script now treats an interrupted call like an outage: it stops that run, retries a
     minute later, and alerts only if it persists for 30 minutes.

7. ✅ **Classification.** Draft the mapping file from the archive's folders and the workflows from the recurring
   filename patterns (`SBAB Lön`/`SBAB - Lön`, `El - Ellevio`/`Ellevio`, `El - Skellefteåkraft`,
   `Nordea - Gold`/`Nordea Gold`, `Coop - Faktura`, `SAS Eurobonus`, `SBAB Bolån`, `ICA - Faktura`, `Volvo`,
   `Faktura Hertz` …), and review them — including any recent Hazel rules whose documents are not yet in the
   archive. Create the correspondents, document types and tags (matching *Auto*), the `Backfill` tag, and the
   workflows. The **inbox tag waits until after the backfill**, or every imported document would land in the
   inbox. *Done 2026-11-04.*
   - **Defined in `scripts/paperless-ingest/paperless_classification.py`** and created by `paperless_setup.py`
     (run on the Mini; it only adds what is missing, by name). The folder's `CLAUDE.md` explains both.
   - **Document types (11):** Årsbesked, Deklaration, Lönebesked, Faktura, Betalning, Kvartalsbesked, Kvitto,
     Avtal, Försäkring, Intyg, Aktienota.
   - **Correspondents (31):** Nordea, SEB, SBAB (both employer and mortgage — the type tells them apart), SPP,
     Skandia, Avanza, Collectum, Pensionsmyndigheten (`PPM`), Länsförsäkringar, Trygg Hansa, IF, Skatteverket
     (deklarationer, slutskattebesked, orange kuvert — not `Kontrolluppgift`, which comes from many payers and gets
     no correspondent), Försäkringskassan, Ellevio, Skellefteå Kraft, Coop, ICA, SAS EuroBonus, Apple, Volvo
     (not `Volvia`, which is left out), Hertz, Telia, SATS, CityOptik, Securitas, Bromma Flygfält, Unoform,
     Pricer, Convini, Microsoft, AWS. Spelling variants map to one name.
   - **Tags:** `Husbygge`, `Semester`, `Inbrott`, `Bil`, `Hälsa`, `Pension`; the people `Max`, `Tilde`, `Helen`,
     `Folke` (the owner is the default, without a tag); and `Backfill`. All but `Backfill` match *Automatic*.
     Not `Kök`: the kitchen papers sit in `2010 - Årsbesked och Kök` beside its årsbesked.
   - **Rules, in three layers** — a later one overrides an earlier one's correspondent and type, tags add up:
     folder names (backfill only), historical filename patterns (backfill only — senders and spellings that will
     not recur, the person tags as whole words), and **35 Paperless workflows** for names that keep coming
     (`Avsändare: …`, `Typ: …`, `Tagg: …`, trigger *Consumption started* from consume folder, API and web UI).
     This extends decision 13, which had only the folder mapping. All eight Hazel rename rules have workflows.
   - **Simulated over the 2,581 files the backfill will upload:** 77% get a correspondent, 74% a type; the 373
     with neither are mostly in `Husbygge`, `Semester` and `Inbrott`, which get their tag. Known imprecision: the
     `Årsbesked` folders also hold some receipts and other papers, which get the type Årsbesked.
   - **Checked live** with six uploads: each got the expected correspondent, type and tags, and an upload that
     sent correspondent Nordea, type Årsbesked and tag `Backfill` for `Apple - …` came out Apple, Årsbesked,
     `Backfill` — **a workflow overrides the uploaded correspondent and type; tags are combined.** Test
     documents deleted, trash emptied.
   - Documents 13 (Ellevio) and 14 (Polisrapport) were consumed before the workflows existed. Ellevio was then
     set by hand to what the rules give (Ellevio, Faktura); the Polisrapport matches no rule and stays as it is.
8. ✅ **The backfill script**, in `scripts/paperless-ingest/` beside the ingest script, with its own token stored by
   the owner on the MacBook. Run `--dry-run` over the whole archive and review the classifications, dates and
   titles. *Done 2026-11-04.*
   - **`paperless_backfill.py`**, run on the MacBook with `/usr/bin/python3` (3.9, as on the Mini): `--dry-run`,
     upload (all, or `--path` for parts), and `--check` after Paperless has worked through its queue. State and
     reports in `~/.local/state/paperless-backfill/` on the MacBook: `done.tsv` (every upload, so a re-run skips
     it), `dry-run-<time>.tsv`, `check-<time>.tsv`. The folder's `CLAUDE.md` describes it.
   - **Not its own token:** Paperless has **one API token per user**, and regenerating it would break the Mini's
     ingest. The MacBook holds a copy of the same token in `~/.config/paperless/token` (`600`, folder `700`),
     copied by the owner straight from the Mini without printing it:
     `ssh jesper@192.168.1.204 'cat ~/.config/paperless/token' | (umask 077; mkdir -p ~/.config/paperless && cat > ~/.config/paperless/token)`.
   - **Dates, extended (decision 11):** a six-digit date — ` - YYMMDD` as a part of its own (`Nordea - Betalning -
     181108`) or `YY-MM-DD` (`FH 17-06-03`) — now gives the exact day, after a full date and before ` - YYMM`. The
     **reference year** is the year folder (not `2019 och tidigare`), or, for a file in no year folder, a year at
     the start of its name (`2017 - Bergvärme`; 209 such names, mostly in `Husbygge`, `Semester`, `Kvitton`).
     File modification times were not used: in year folders only 1,587 of 2,188 lie within a year of the folder.
   - **The Paperless client moved** from the ingest script to `paperless_api.py`, shared by both; the ingest was
     re-tested against Paperless (upload, refusal, follow-up) and runs on the Mini with it.
   - **Dry run over the whole archive:** 2,617 files — 2,581 to upload, 36 skipped by type. 1,997 get a
     correspondent and 1,913 a document type (as simulated in step 7). Dates from the name: ` - YYMM` 513,
     six-digit 354, full date 30, ` - YYYY` 12; none for 1,672 (mostly numbered årsbesked such as `SEB - 5.pdf`,
     deklarationer and `Husbygge` papers), which Paperless dates from the content — 1,501 of those have a
     reference year, so `--check` puts any whose date lands more than a year off on the review list. No date from a
     name lies more than a year from its year folder. Reviewed by the owner (spot checks).
9. ✅ **Test batch of ~50 mixed documents** with the path option. Check titles, dates, classification and OCR
   quality, and that the matching does not overrule what the mapping set. Delete the test batch (tag
   `Backfill`) **and empty the trash** — otherwise the duplicate check refuses those documents in step 10.
   *Done 2026-11-04.*
   - **54 files:** `2023/Nordea` (19), `2026/Årsbesked` with its subfolder (27), and eight single files of other
     kinds (a `Husbygge` budget with `FH 17-06-03`, an Apple receipt, a `Semester` booking, a 1978 school report,
     a `.JPG`, a `.docx`, a `.txt`, the extensionless Word file). Uploaded in 7 seconds, consumed in under 2
     minutes.
   - **As in the dry run:** every title, every date from a name, and everything the rules set. Matching did not
     overrule a rule — it only fills empty fields.
   - **OCR:** clean on modern PDFs, usable on the 1978 scan, nothing on a photo without text.
   - **Memory:** webserver peak 1.1 GiB (0.84 at rest), Tika 0.4 GiB, CPU up to 427 % (2 workers × 2 threads) —
     well inside Docker Desktop's 7.75 GiB VM.
   - **Found, and fixed:**
     - **Automatic matching guessed wrong** where the rules left a field empty: four documents (the `Husbygge`
       budget, the Lufthansa booking, two försäkringsbesked) got Ellevio/Faktura — the classifier had been trained
       on the only labelled document, the Ellevio invoice. It retrains every hour, so during the backfill it
       would guess from a partial archive. `paperless_setup.py --set-matching none|auto` added; **matching is set
       to None for all correspondents, types and tags since 2026-11-04**, until after the backfill.
     - **Paperless dates a document with no date in name or content as the day it was added** (the photo, the
       `.txt`, the booking). `paperless_backfill.py --check --fix-dates` recognises these (`created` = day added,
       no date in the name) and sets 1 January of the reference year; the other review cases (here two Nordea ISK
       årsbesked from the 2026 folder, read as 2024-11-30) stay for a person.
     - **The extensionless Word file failed** ("Error while converting document to PDF"). Renamed in the archive
       to `inredning toalett.doc` — it is in fact an old Excel sheet, which Paperless then converted. It was the
       only file without a real extension; 30 others only have their extension hidden in Finder, which does not
       matter.
   - Deleted afterwards (all 54 `Backfill` documents), trash emptied; the batch's record is kept as
     `~/.local/state/paperless-backfill/done-testbatch-20261104.tsv` on the MacBook, so the backfill starts fresh.
10. ✅ **The backfill.** Run it over the whole archive. Afterwards: the count check, the failed tasks, and the
    review list of documents dated more than a year from their year folder. Then create the inbox tag.
    In detail, from step 9:
    1. Matching is None (set in step 9; check with `paperless_setup.py --set-matching none --dry-run`).
    2. `paperless_backfill.py --dry-run` once more, then `paperless_backfill.py` from the MacBook.
    3. Wait until `--check` shows nothing `not finished`, then `--check --fix-dates`; go through the failed
       tasks and the review list.
    4. `paperless_setup.py --set-matching auto`, then train the classifier on the whole archive:
       `docker exec paperless-webserver document_create_classifier`.
    5. `docker exec paperless-webserver document_retagger -c -T -t --suggest` (suggestions only, for empty fields)
       — review, and apply only if they are good.
    6. Create the inbox tag.

    *Done 2026-11-04.*
   - **Upload:** 2,581 files from the MacBook in five minutes (10:22–10:27), none refused. **Paperless** took
     about five hours (finished before 15:54): about 5 documents a minute through the scanned deklarationer of the
     early 2000s, which need OCR, then about 8 a minute. 2,528 documents; the server stayed at about 190–260 % CPU
     and 1.2–1.3 GiB.
   - **53 failed, all duplicates** (byte-identical files under two names, so nothing is lost), among them the
     expected Ellevio 2611 and Polisrapport (documents 13 and 14 from step 5). Where the copy under `Husbygge/`
     was the one refused, the kept original lacked the tag: **17 documents given `Husbygge`** by hand. A few
     duplicates point at filing slips in the archive (`Skellefteåkraft - 1907` = `1905`, `Nordea, överfört till
     SBAB` = `Nordea - 210818 - Welcome inside`), left as they are.
   - **Dates:** `--fix-dates` set 450 undated documents to 1 January of their year folder. The **review list**
     (82) held content dates that were mostly wrong — deklarationer read as 1 November two years earlier, personal
     identity numbers and birth dates, boilerplate dates (Collectum 2007-06-30 every year, UC 1999-04-01): **74
     set to 1 January of the folder year, 6 kept** (SATS contracts ×2, SBAB personallån, Nordea fondförsäljning,
     FIC Grand Plaza, Tandborste kvitto).
   - **A gap in the check, fixed:** documents dated the day they were added were only caught when that was more
     than a year from the folder year — 122 slipped through (43 in the 2025/2026 folders, 79 without a reference
     year, mostly `Betyg och anställningar`). The check now catches them whatever the distance; `--fix-dates`
     gave the 43 1 January of their year and the 79 the placeholder **1970-01-01** — filter on *created before
     1971* to date them by hand.
   - **Deleted:** the two `Password before Lastpass switch` text files (2023), which had become searchable; trash
     emptied.
   - **Classifier** trained in 12 s. The retagger suggested 31 field changes for empty fields over 28 documents:
     **25 applied**, 6 skipped as wrong (Knivbrev and Smycken → Microsoft, Smycken → Intyg, Vaccin →
     Länsförsäkringar, `2A` → Intyg, Volvo XC40 Beställning → Faktura).
   - **Inbox tag `Inkorg`** created by `paperless_setup.py` (inbox tag, matching None); from now on every new
     document gets it.
11. ✅ **`scripts/paperless-backup/` and its launchd job** (decision 14). Give the job the full `PATH`
    (`/opt/homebrew/bin` and Docker's), since launchd starts it with the same minimal one as SSH does (see SSH from
    the dev machine). Run it once by hand and restore-test the zip: `document_importer` into a throwaway
    container, or at least open the zip and check its manifest. *Done 2026-11-04.*
    - **Script:** `paperless_backup.sh` (bash, like the MySQL and Paprika backups) with
      `paperless_backup.conf` (mode 600; production paths in the template, the user's Pushover keys). The helper
      image is `alpine:3.24.3`, already on the Mini. The mirror copy goes to a `.partial` name, is compared byte
      for byte with `cmp`, and only then renamed, so a half copy never counts among the 4 kept. Zips left in
      staging by a failed run are removed at the start of the next. The previous iCloud zip is deleted only after
      the new one is in place.
    - **First run by hand:** 3 minutes, `paperless-export-2026-11-04.zip`, 2.8 GB with 5,973 files (PDFs barely
      compress), on the mirror and in iCloud, staging empty afterwards; the quiet ✅ arrived.
    - **Restore test:** `restoretest-compose.yml` (in the folder) starts a throwaway Paperless beside
      production — own project and broker, SQLite and media in Docker volumes, no ports, the mirror mounted
      read-only. Version 3 refuses to start without `PAPERLESS_SECRET_KEY` (a random one per test, stored
      nowhere), and `PAPERLESS_OCR_LANGUAGE=swe+eng` without `PAPERLESS_OCR_LANGUAGES=swe` fails the start-up
      checks (decision 7), so the test instance sets no OCR language. `document_importer` read the zip directly
      in 32 s. Documents (2,528), tags, correspondents, types, workflows, users and a fingerprint over every
      document's ID, title, date, correspondent, type and tags all equal to production; `document_sanity_checker`
      found no errors or warnings on either. Removed afterwards with `down -v`.
    - **launchd:** `com.jtarnvik.paperlessbackup` installed and loaded (by the user — loading a launch agent
      needs their go-ahead), Sunday 04:00; first scheduled run Sunday 2026-11-08.
12. ✅ **Document it here:** a row in Overview, an operating section under Mac Mini — Local Services (status,
    restart, upgrade, ingest, backup and restore, token renewal), and mark this plan done. *Done 2026-11-05.*
    Overview row, port table and the Weekly backups section point to the new section **Paperless-ngx — document
    archive (LAN only)**: where things are, status, logs, restart, upgrade (including the restore-test image tag,
    and why a Postgres major version needs export/import), daily use (`Inkorg`, the 1970-01-01 placeholders), new
    Hazel rules, the ingest alerts, backup and restore, and token renewal.
