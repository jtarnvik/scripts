# Paperless Ingest — Project Context

Uploads new documents to Paperless-ngx on the production Mac Mini (192.168.1.204). The design and its reasons are
in the "Paperless-ngx Introduction Plan" of `home-infra-manual.md` — decisions 2, 3, 11 and 15.

The backfill script (plan step 8) will live here too and reuse `paperless_names.py`.

---

## What the project does

- Hazel on the MacBook Pro copies each filed document to `iCloud Drive/PrivateData/_paperless_uploads`, which
  syncs to the Mini (Keep Downloaded is set on the folder there)
- A launchd agent on the Mini runs the script every 60 seconds (`StartInterval`)
- Each document is uploaded through the API (`/api/documents/post_document/`) with a title and date taken from its
  filename, then kept in a pending folder until Paperless has consumed it
- Consumed: the file is deleted. Refused or failed: it is moved to `iCloud Drive/PrivateData/_paperless_failed`
  with a Pushover alert

Paperless never reads iCloud or a folder a host process writes to — see decision 3 in the manual.

---

## Files

| File | Committed | Purpose |
|------|-----------|---------|
| `paperless_ingest.sh` | ✅ | launchd entry point: loads the conf, runs the Python script |
| `paperless_ingest.py` | ✅ | The ingest logic |
| `paperless_names.py` | ✅ | Title and date from a filename (decision 11), shared with the backfill |
| `test_paperless_names.py` | ✅ | Unit tests for `paperless_names.py` |
| `paperless_ingest.conf.template` | ✅ | Template showing required config variables |
| `paperless_ingest.conf` | ❌ | Local secrets — created from template, never committed |
| `com.jtarnvik.paperlessingest.plist` | ✅ | macOS launchd agent |

Why Python, in a repo of bash scripts: the script reads JSON from the tasks API, uploads multipart forms with
Swedish filenames, and shares its date rules with the backfill. It uses macOS's own `/usr/bin/python3` (3.9 on
the Mini) and the standard library only — nothing to install. Keep it 3.9-compatible: no `match`, no `X | None`.

Why a bash wrapper: the `.conf` is a bash file like the other scripts', and Python runs as a child of
`/bin/bash`, which has Full Disk Access on the Mini and so may read iCloud Drive. The wrapper must not `exec`
Python.

---

## Configuration

```bash
cp paperless_ingest.conf.template paperless_ingest.conf
# edit paperless_ingest.conf and fill in the Pushover keys
chmod 600 paperless_ingest.conf
```

| Variable | Description |
|----------|-------------|
| `PAPERLESS_URL` | `http://127.0.0.1:9400`. `127.0.0.1` must be in `PAPERLESS_ALLOWED_HOSTS` |
| `PAPERLESS_TOKEN_FILE` | File holding the API token, `~/.config/paperless/token` (`600`, folder `700`) |
| `INGEST_DIR` | Where Hazel's copies arrive, `iCloud Drive/PrivateData/_paperless_uploads` |
| `FAILED_DIR` | Where refused and failed documents go. Must not be inside `INGEST_DIR` |
| `STATE_DIR` | `~/.local/state/paperless-ingest`: pending uploads, `state.json`, a lock file |
| `PUSHOVER_USER_KEY` | Pushover user key |
| `PUSHOVER_API_TOKEN` | Pushover API token |

The token is kept in its own file rather than in the conf so it can be renewed without editing the conf: in the
web UI, *My Profile* → *API Auth Token*, then on the MacBook
`pbpaste | ssh jesper@192.168.1.204 'umask 077; tr -d "[:space:]" > ~/.config/paperless/token'`.

---

## How a run works

1. **Follow up on earlier uploads.** Each accepted upload waits in `STATE_DIR/pending/<task id>/<filename>`.
   The script asks `/api/tasks/?task_id=<id>` for its status (version 3 of the API: lowercase `status`,
   `result_data`, `related_document_ids`):
   - `success` — the file is deleted
   - `failure` or `revoked` — the file is moved to `FAILED_DIR`, with an alert giving Paperless's reason
   - still `pending`/`started` — left for the next run; an alert if it is still waiting after 24 hours (the
     backfill can queue Paperless for hours)
2. **Upload new files**, in name order:
   - `.DS_Store` (Finder's folder settings, if the folder is opened on the Mini) is deleted
   - a file iCloud has not finished downloading (the `SF_DATALESS` flag, or an old-style `.name.icloud`
     placeholder) is skipped; an alert if it is still not downloaded after 30 minutes
   - a subfolder or another hidden file is moved to `FAILED_DIR`, with an alert
   - everything else is uploaded with `title` and `created` from `paperless_names.py` and moved to `pending/`.
     A file Paperless refuses outright (HTTP 4xx — for example `File type application/octet-stream not
     supported` for a `.zip`) goes to `FAILED_DIR`, with an alert
3. **When Paperless does not answer** (connection refused, timeout, HTTP 5xx), the run stops and files stay where
   they are, for the next run a minute later. An alert after 30 minutes. A refused token (HTTP 401/403) alerts at once.

A name clash in `FAILED_DIR` gets a ` (2)`-style suffix. A lock file stops a run by hand from overlapping
launchd's.

### Dates and titles (`paperless_names.py`)

In order: a full date anywhere in the name (`2026-03-01`, `20260301`); a name ending ` - YYMM` with a valid
month and 20YY at most one year from the reference year → the first of the month; a name ending ` - YYYY` →
31 December; otherwise no date is sent and Paperless reads it from the content. The reference year is the current
year here, the year folder in the backfill. The title is the filename without extension and without Hazel's
trailing ` (1)`, normalised to NFC. `created` is sent as a plain date (`2026-11-01`), which Paperless stores as
that day.

```bash
python3 -m unittest test_paperless_names
```

---

## Notifications

Only when something needs attention (decision 15). Lasting conditions are reported once, and again only after
they have cleared; `state.json` remembers which were sent.

| Title | When | Priority |
|-------|------|----------|
| `❌ Paperless ingest - document refused` | Upload answered with HTTP 4xx | 0 |
| `❌ Paperless ingest - document failed` | Consumption failed (corrupt file, OCR error …) | 0 |
| `❌ Paperless ingest - not a document` | A subfolder or hidden file in the ingest folder | 0 |
| `❌ Paperless ingest - file not downloaded` | Still an iCloud placeholder after 30 minutes | 0 |
| `❌ Paperless ingest - upload not consumed` | Task still not finished after 24 hours | 0 |
| `❌ Paperless ingest - Paperless unreachable` | No answer for 30 minutes | 1 |
| `❌ Paperless ingest - token refused` | HTTP 401/403 | 1 |
| `❌ Paperless ingest - folder missing` | `INGEST_DIR` or `FAILED_DIR` does not exist | 1 |
| `❌ Paperless ingest - folder access interrupted` | File system calls interrupted (`EINTR`) for 30 minutes | 1 |
| `❌ Paperless ingest - script error` | Any unexpected exception | 1 |

Every alert is also written to the log as `Alert: …`; a failed send as `ERROR: Pushover notification failed: …`.

---

## Scheduling (launchd)

```bash
# Install (do once, survives reboot)
cp ~/scripts/paperless-ingest/com.jtarnvik.paperlessingest.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jtarnvik.paperlessingest.plist

# Unregister
launchctl unload ~/Library/LaunchAgents/com.jtarnvik.paperlessingest.plist

# Run now
launchctl start com.jtarnvik.paperlessingest

# Check
launchctl list | grep paperlessingest
tail -f ~/scripts/paperless-ingest/paperless_ingest.log
```

The plist has the paths of the Mini hardcoded (`~/scripts/paperless-ingest/`). `RunAtLoad` is on, so loading
the job also processes anything already waiting.

**Why a timer and not `QueueDirectories`:** launchd's folder trigger never fires for a folder in iCloud Drive —
neither for a new file nor for a touched one (tested 2026-11-03), while the same job on a plain folder fired
within a second. A run every 60 seconds is simpler and does not depend on how macOS reports changes inside
iCloud Drive. An idle run (nothing waiting, nothing pending) lists the ingest and failed folders and exits: no
API call, no log line, and no write — `state.json` is only written when it changes, and the lock file is
opened without writing to it. Everything it reads is on the Mini's internal SSD.

---

## Testing by hand

Point a copy of the conf at test folders (`INGEST_DIR`, `FAILED_DIR`, `STATE_DIR` under `~/some-test-dir`), put
files in the ingest folder and run `/bin/bash paperless_ingest.sh` twice — once to upload, once (after a few
seconds) to follow up. A port nobody listens on in `PAPERLESS_URL` gives the unreachable path; a token file with
40 zeros gives the 401 path. Delete the test documents afterwards and empty the trash
(`POST /api/trash/ {"action": "empty"}`).

---

## Known behaviour

- **Duplicates are refused** because `docker-compose.env` sets `PAPERLESS_CONSUMER_DELETE_DUPLICATES=true`: the
  task fails with `result_data` `{"duplicate_of": <id>, "duplicate_in_trash": …}`, and the file goes to
  `FAILED_DIR` with the alert "Duplicate of document #<id>". A document in the trash counts too. Without the
  setting, Paperless 3 consumes the copy and only links the two (`duplicate_documents` on each).
- The original filename in Paperless keeps Hazel's ` (1)`; only the title drops it.
- A document with no date in its name or content gets the upload date.
- Nothing is logged on a run with nothing to do, so the 60-second timer does not fill the log.
- A new document is uploaded within a minute of iCloud syncing it to the Mini, and the file is deleted a minute
  later, once the follow-up sees it consumed (step 6 of the plan, 2026-11-04: about 1.5 minutes from `Införda` to
  Paperless).
- **iCloud Drive now and then interrupts a file system call** (`InterruptedError`, `EINTR` — seen once,
  2026-11-03 21:07, listing the ingest folder), which Python does not retry there. The run stops and logs
  `Interrupted system call, will retry`; the next run a minute later carries on. Stopping is safe: each file is
  either still in the ingest folder or already in `pending/`. An alert only if it goes on for 30 minutes. The
  folder check uses `os.stat`, not `os.path.isdir`, which would answer an interrupted call with False and so
  report the folder missing.
