"""
paperless_ingest.py

Uploads the documents that arrive in the ingest folder (iCloud Drive, PrivateData/_paperless_uploads) to
Paperless-ngx through its API — decisions 3, 11 and 15 of the Paperless plan in paperless-plan.md.

Started by paperless_ingest.sh, which loads paperless_ingest.conf into the environment. launchd runs it every
60 seconds (StartInterval). Not QueueDirectories: launchd's folder trigger never fires for a folder in iCloud
Drive (tested 2026-11-03). A run with nothing to do only lists two folders — no API call, no log line, no write.

Each run:
  1. Follows up on earlier uploads. An accepted upload is kept in STATE_DIR/pending/<task id>/ until Paperless
     has consumed it: on success the file is deleted, on failure it is moved to the failed folder with an alert.
  2. Uploads every file in the ingest folder, with title and date from its name (paperless_names.py), and
     moves it to pending/, out of the ingest folder.
     - .DS_Store (Finder's folder settings, if the folder is opened on the Mini) is deleted.
     - A file iCloud has not finished downloading is left for a later run.
     - A subfolder or another hidden file is moved to the failed folder.
     - A file Paperless refuses (HTTP 4xx) is moved to the failed folder, with an alert.
     - If Paperless cannot be reached, files stay where they are, for the next run.

Pushover only when something needs attention: a failed file, Paperless unreachable for 30 minutes, a rejected
token, a missing folder, a file still downloading after 30 minutes, an upload not consumed within 24 hours.
Conditions that persist are reported once, and again only after they have cleared.

Python 3.9 (macOS's /usr/bin/python3), standard library only.
"""

import fcntl
import json
import os
import shutil
import stat
import sys
import time
import traceback
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime

from paperless_api import AuthenticationFailed, Paperless, Refused, Unreachable, task_reason
from paperless_names import parse_name

UNREACHABLE_ALERT_SECONDS = 30 * 60
INTERRUPTED_ALERT_SECONDS = 30 * 60
DOWNLOADING_ALERT_SECONDS = 30 * 60
PENDING_ALERT_SECONDS = 24 * 60 * 60

# st_flags bit for an iCloud file whose content is not on disk yet (<sys/stat.h>)
SF_DATALESS = 0x40000000

REQUIRED_SETTINGS = (
    "PAPERLESS_URL", "PAPERLESS_TOKEN_FILE", "INGEST_DIR", "FAILED_DIR", "STATE_DIR",
    "PUSHOVER_USER_KEY", "PUSHOVER_API_TOKEN",
)


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


# ── Settings ──────────────────────────────────────────────────────────────────

class Settings:

    def __init__(self) -> None:
        missing = [name for name in REQUIRED_SETTINGS if not os.environ.get(name)]
        if missing:
            raise SystemExit(f"ERROR: Missing required config variables: {' '.join(missing)}")

        self.paperless_url = os.environ["PAPERLESS_URL"].rstrip("/")
        self.token_file = os.path.expanduser(os.environ["PAPERLESS_TOKEN_FILE"])
        self.ingest_dir = os.path.expanduser(os.environ["INGEST_DIR"])
        self.failed_dir = os.path.expanduser(os.environ["FAILED_DIR"])
        self.state_dir = os.path.expanduser(os.environ["STATE_DIR"])
        self.pending_dir = os.path.join(self.state_dir, "pending")
        self.pushover_user_key = os.environ["PUSHOVER_USER_KEY"]
        self.pushover_api_token = os.environ["PUSHOVER_API_TOKEN"]


# ── State: which alerts have been sent, and since when conditions hold ────────

class State:
    """Kept in STATE_DIR/state.json between runs."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.data = {"alerted": [], "since": {}}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as file:
                self.data = json.load(file)
        self.saved = self._serialised()

    def save(self) -> None:
        """Writes the state if it changed, so that an idle run every minute writes nothing."""
        if self._serialised() == self.saved:
            return
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(self.data, file, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)
        self.saved = self._serialised()

    def _serialised(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, sort_keys=True)

    def seconds_since(self, key: str) -> float:
        """Seconds since the condition `key` was first seen; starts the clock if it is new."""
        since = self.data["since"].setdefault(key, time.time())
        return time.time() - since

    def is_alerted(self, key: str) -> bool:
        return key in self.data["alerted"]

    def mark_alerted(self, key: str) -> None:
        if key not in self.data["alerted"]:
            self.data["alerted"].append(key)

    def clear(self, key: str) -> bool:
        """Forgets the condition `key`. Returns True if an alert had been sent for it."""
        self.data["since"].pop(key, None)
        if key in self.data["alerted"]:
            self.data["alerted"].remove(key)
            return True
        return False

    def keys_with_prefix(self, prefix: str) -> list:
        keys = set(self.data["since"]) | set(self.data["alerted"])
        return [key for key in keys if key.startswith(prefix)]


# ── Pushover ──────────────────────────────────────────────────────────────────

class Notifier:

    def __init__(self, settings: Settings, state: State) -> None:
        self.settings = settings
        self.state = state

    def send(self, title: str, message: str, priority: int) -> None:
        """priority: 0 = normal, 1 = high. A failed send is logged, so that a broken token does not look like silence."""
        log(f"Alert: {title}: {message}".replace("\n", " | "))
        body = urllib.parse.urlencode({
            "token": self.settings.pushover_api_token,
            "user": self.settings.pushover_user_key,
            "title": title,
            "message": message,
            "priority": priority,
        }).encode()
        try:
            urllib.request.urlopen("https://api.pushover.net/1/messages.json", data=body, timeout=30).read()
        except Exception as error:
            log(f"ERROR: Pushover notification failed: {error}")

    def send_once(self, key: str, title: str, message: str, priority: int) -> None:
        """Sends an alert for a lasting condition, unless one was already sent for it."""
        if not self.state.is_alerted(key):
            self.send(title, message, priority)
            self.state.mark_alerted(key)


# ── Files ─────────────────────────────────────────────────────────────────────

def is_downloading(path: str, name: str) -> bool:
    """True while iCloud has only a placeholder for the file."""
    if name.startswith(".") and name.endswith(".icloud"):
        return True
    return bool(os.lstat(path).st_flags & SF_DATALESS)


def move_to_failed(settings: Settings, path: str, name: str) -> str:
    """Moves a file into the failed folder, without overwriting. Returns the name it got there."""
    stem, extension = os.path.splitext(name)
    target_name = name
    counter = 2
    while os.path.exists(os.path.join(settings.failed_dir, target_name)):
        target_name = f"{stem} ({counter}){extension}"
        counter += 1
    shutil.move(path, os.path.join(settings.failed_dir, target_name))
    return target_name


# ── The two halves of a run ───────────────────────────────────────────────────

def follow_up_pending(settings: Settings, state: State, notifier: Notifier, paperless: Paperless) -> None:
    for task_id in sorted(os.listdir(settings.pending_dir)):
        task_dir = os.path.join(settings.pending_dir, task_id)
        if not os.path.isdir(task_dir):
            continue
        names = os.listdir(task_dir)
        if not names:
            os.rmdir(task_dir)
            continue
        name = names[0]
        path = os.path.join(task_dir, name)

        task = paperless.task(task_id)
        status = task["status"] if task else "unknown"
        key = f"pending:{task_id}"

        if status == "success":
            os.remove(path)
            os.rmdir(task_dir)
            state.clear(key)
            log(f"Consumed: {name} -> document {task.get('related_document_ids')}")
        elif status in ("failure", "revoked"):
            reason = task_reason(task)
            failed_name = move_to_failed(settings, path, name)
            os.rmdir(task_dir)
            state.clear(key)
            log(f"Consumption failed: {name} ({reason}) -> _paperless_failed/{failed_name}")
            notifier.send(
                "❌ Paperless ingest - document failed",
                f"{name}\n{reason}\nMoved to _paperless_failed.",
                0,
            )
        elif state.seconds_since(key) > PENDING_ALERT_SECONDS:
            notifier.send_once(
                key,
                "❌ Paperless ingest - upload not consumed",
                f"{name} was uploaded more than 24 hours ago and its task is still '{status}'.",
                0,
            )


def upload_new_files(settings: Settings, state: State, notifier: Notifier, paperless: Paperless) -> None:
    present = set()
    for name in sorted(os.listdir(settings.ingest_dir)):
        path = os.path.join(settings.ingest_dir, name)
        present.add(name)

        if name == ".DS_Store":
            os.remove(path)
            continue

        if is_downloading(path, name):
            key = f"downloading:{name}"
            if state.seconds_since(key) > DOWNLOADING_ALERT_SECONDS:
                notifier.send_once(
                    key,
                    "❌ Paperless ingest - file not downloaded",
                    f"{name} has waited more than 30 minutes for iCloud to download it on the Mini.",
                    0,
                )
            continue

        if name.startswith(".") or stat.S_ISDIR(os.lstat(path).st_mode):
            failed_name = move_to_failed(settings, path, name)
            log(f"Not a document: {name} -> _paperless_failed/{failed_name}")
            notifier.send(
                "❌ Paperless ingest - not a document",
                f"{name} is a folder or a hidden file.\nMoved to _paperless_failed.",
                0,
            )
            continue

        upload_name = unicodedata.normalize("NFC", name)
        parsed = parse_name(name, datetime.now().year)
        created = parsed.created.isoformat() if parsed.created else None
        try:
            task_id = paperless.upload(path, upload_name, parsed.title, created)
        except Refused as error:
            failed_name = move_to_failed(settings, path, name)
            log(f"Refused: {name} ({error}) -> _paperless_failed/{failed_name}")
            notifier.send(
                "❌ Paperless ingest - document refused",
                f"{name}\n{error}\nMoved to _paperless_failed.",
                0,
            )
            continue

        task_dir = os.path.join(settings.pending_dir, task_id)
        os.makedirs(task_dir, exist_ok=True)
        shutil.move(path, os.path.join(task_dir, name))
        log(f"Uploaded: {name} (title '{parsed.title}', date {created or 'from content'} [{parsed.rule}]), task {task_id}")

    # Forget files that are gone, so a later file with the same name gets a fresh clock
    for key in state.keys_with_prefix("downloading:"):
        if key[len("downloading:"):] not in present:
            state.clear(key)


# =============================================================================
# Main
# =============================================================================

def folder_exists(path: str) -> bool:
    """Like os.path.isdir, but an interrupted call raises instead of answering False."""
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except FileNotFoundError:
        return False


def run(settings: Settings, state: State, notifier: Notifier) -> None:
    try:
        for folder, key in ((settings.ingest_dir, "missing:ingest"), (settings.failed_dir, "missing:failed")):
            if not folder_exists(folder):
                notifier.send_once(key, "❌ Paperless ingest - folder missing", f"{folder} does not exist.", 1)
                log(f"ERROR: folder missing: {folder}")
                return
            state.clear(key)

        paperless = Paperless(settings.paperless_url, settings.token_file)
        follow_up_pending(settings, state, notifier, paperless)
        upload_new_files(settings, state, notifier, paperless)
    except InterruptedError as error:
        # iCloud Drive's file system now and then answers a call with EINTR (seen 2026-11-03, listing the ingest
        # folder), which Python does not retry there. Stopping is safe: each file is either still in the ingest
        # folder or already in pending/, and the next run comes a minute later. Alert only if it persists.
        log(f"Interrupted system call, will retry: {error}")
        if state.seconds_since("interrupted") > INTERRUPTED_ALERT_SECONDS:
            notifier.send_once(
                "interrupted",
                "❌ Paperless ingest - folder access interrupted",
                f"File system calls have been interrupted for more than 30 minutes.\n{error}",
                1,
            )
        return
    except Unreachable as error:
        # The folders answered before Paperless was asked, so file system calls are not what failed
        state.clear("interrupted")
        log(f"Paperless unreachable, will retry: {error}")
        if state.seconds_since("unreachable") > UNREACHABLE_ALERT_SECONDS:
            notifier.send_once(
                "unreachable",
                "❌ Paperless ingest - Paperless unreachable",
                f"Paperless at {settings.paperless_url} has not answered for more than 30 minutes.\n{error}\n"
                "Documents wait in the ingest folder.",
                1,
            )
        return
    except AuthenticationFailed as error:
        # Paperless answered, so it is reachable; and the folders answered before it was asked
        state.clear("unreachable")
        state.clear("interrupted")
        log(f"ERROR: Paperless refused the token: {error}")
        notifier.send_once(
            "authentication",
            "❌ Paperless ingest - token refused",
            f"Paperless refused the API token in {settings.token_file}.\n{error}\nDocuments wait in the ingest folder.",
            1,
        )
        return

    for key in ("unreachable", "authentication", "interrupted"):
        if state.clear(key):
            log(f"Cleared: {key}")


def main() -> int:
    settings = Settings()
    os.makedirs(settings.pending_dir, exist_ok=True)

    # launchd never runs two copies of the job, but a run by hand could overlap one
    # "a", not "w": opening an existing file for appending does not write to it
    lock = open(os.path.join(settings.state_dir, "lock"), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Another run is in progress, exiting.")
        return 0

    state = State(os.path.join(settings.state_dir, "state.json"))
    notifier = Notifier(settings, state)
    try:
        run(settings, state, notifier)
        state.clear("crash")
        return 0
    except Exception:
        log("ERROR: unexpected failure:\n" + traceback.format_exc())
        notifier.send_once(
            "crash",
            "❌ Paperless ingest - script error",
            f"{traceback.format_exc(limit=1)[-500:]}\nSee paperless_ingest.log.",
            1,
        )
        return 1
    finally:
        state.save()


if __name__ == "__main__":
    sys.exit(main())
