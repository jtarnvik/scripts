"""
paperless_backfill.py

Imports the document archive (iCloud Drive, PrivateData/Dokument Arkiv) into Paperless-ngx — decision 13 and
steps 8–10 of the Paperless plan in home-infra-manual.md. Runs on the MacBook Pro, which has the whole archive
downloaded, and uploads over the LAN. Nothing in the archive is changed, copied or moved.

    python3 paperless_backfill.py --dry-run                 # report what would be uploaded, upload nothing
    python3 paperless_backfill.py --path 2023 --path Kvitton  # upload part of the archive (the test batch)
    python3 paperless_backfill.py                           # upload everything not uploaded yet
    python3 paperless_backfill.py --check                   # once Paperless has worked through its queue
    python3 paperless_backfill.py --check --fix-dates       # … and date the documents Paperless could not date

Each file is uploaded with:
  - title and date from its name (paperless_names.py), with the file's reference year: its year folder, or a year
    at the start of its name when it sits in no year folder;
  - correspondent, document type and tags from its folders and its name (paperless_classification.py, layers 1
    and 2) — Paperless then applies its workflows (layer 3) on top;
  - the tag Backfill.

Skipped: the folder _Paperless-ignored, hidden files and folders, and types Paperless cannot take (psd, zip, pxm).

Every upload is recorded in STATE_DIR/done.tsv, so a second run skips what the first one uploaded. --check reads
it, asks Paperless how each upload went, and writes a report: failed and refused files, uploads not consumed yet,
and documents whose date is more than a year from their reference year (the review list).

With --fix-dates, a document on the review list that Paperless gave the day it was added — because neither its
name nor its content held a date — is set to 1 January of its reference year instead, and reported as "fixed".
The rest of the review list keeps the date Paperless read from the content, for a person to judge: an old paper
can have been filed late.

Python 3.9 (macOS's /usr/bin/python3), standard library only.
"""

import argparse
import csv
import fnmatch
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from paperless_api import AuthenticationFailed, Paperless, Refused, Unreachable, task_reason
from paperless_classification import (
    BACKFILL_NAME_RULES, BACKFILL_TAG, CORRESPONDENTS, DOCUMENT_TYPES, FOLDER_RULES, TAGS, WORKFLOWS, Assign,
)
from paperless_names import leading_year, parse_name

DEFAULT_ARCHIVE = "~/Library/Mobile Documents/com~apple~CloudDocs/PrivateData/Dokument Arkiv"
DEFAULT_URL = "http://192.168.1.204:9400"
DEFAULT_TOKEN_FILE = "~/.config/paperless/token"
DEFAULT_STATE_DIR = "~/.local/state/paperless-backfill"

IGNORED_FOLDER = "_Paperless-ignored"
SKIPPED_EXTENSIONS = {".psd", ".zip", ".pxm"}

# A folder whose name starts with a year is a year folder — "2014", "1991 Årsbesked", "2010 - Årsbesked och Kök" —
# except the collection folder "2019 och tidigare"
YEAR_FOLDER = re.compile(r"^((?:19|20)\d\d)(?!\d)")
NOT_A_YEAR_FOLDER = "2019 och tidigare"

DONE_COLUMNS = ["path", "status", "task_id", "uploaded_at", "reference_year", "detail"]

# Documents dated further than this from their reference year go on the review list
REVIEW_YEARS = 1


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


# ── Classification ────────────────────────────────────────────────────────────

@dataclass
class Classification:
    correspondent: Optional[str] = None
    document_type: Optional[str] = None
    tags: Set[str] = field(default_factory=set)
    # Which rules fired, in order, for the dry-run report
    trace: List[str] = field(default_factory=list)

    def apply(self, assign: Assign, source: str) -> None:
        if assign.correspondent:
            self.correspondent = assign.correspondent
        if assign.document_type:
            self.document_type = assign.document_type
        self.tags.update(assign.tags)
        self.trace.append(source)

    def copy(self) -> "Classification":
        return Classification(self.correspondent, self.document_type, set(self.tags), list(self.trace))


def classify(folders: List[str], name: str) -> Tuple[Classification, Classification]:
    """Returns what the backfill sends (layers 1 and 2), and what Paperless should end with (plus layer 3)."""
    result = Classification()
    for folder in folders:
        for pattern, assign in FOLDER_RULES:
            if re.fullmatch(pattern, folder):
                result.apply(assign, f"folder {folder}")
    for pattern, assign in BACKFILL_NAME_RULES:
        if re.search(pattern, name, re.IGNORECASE):
            result.apply(assign, f"name /{pattern}/")
    result.tags.add(BACKFILL_TAG)
    sent = result.copy()

    # Paperless's workflow filter: fnmatch on the lower-cased filename and pattern
    for workflow in WORKFLOWS:
        if any(fnmatch.fnmatchcase(name.lower(), pattern.lower()) for pattern in workflow.patterns):
            result.apply(workflow.assign, f"workflow {workflow.name}")
    return sent, result


# ── The archive ───────────────────────────────────────────────────────────────

@dataclass
class ArchiveFile:
    path: str               # absolute, as on disk
    relative: str           # relative to the archive, NFC — the key in done.tsv
    folders: List[str]      # NFC
    name: str               # NFC
    skip_reason: Optional[str]

    @property
    def reference_year(self) -> Optional[int]:
        for folder in reversed(self.folders):
            if folder == NOT_A_YEAR_FOLDER:
                continue
            match = YEAR_FOLDER.match(folder)
            if match:
                return int(match.group(1))
        return leading_year(self.name)


def walk_archive(archive: str, only_paths: List[str]) -> List[ArchiveFile]:
    files = []
    for root, dirs, names in os.walk(archive):
        relative_root = nfc(os.path.relpath(root, archive))
        folders = [] if relative_root == "." else relative_root.split("/")
        dirs[:] = sorted(
            d for d in dirs
            if not d.startswith(".") and not (not folders and nfc(d) == IGNORED_FOLDER)
        )
        for name in sorted(names):
            if name.startswith("."):
                continue
            relative = "/".join(folders + [nfc(name)])
            if only_paths and not any(relative == p or relative.startswith(p.rstrip("/") + "/") for p in only_paths):
                continue
            extension = os.path.splitext(name)[1].lower()
            skip_reason = f"type {extension}" if extension in SKIPPED_EXTENSIONS else None
            files.append(ArchiveFile(os.path.join(root, name), relative, folders, nfc(name), skip_reason))
    return files


# ── done.tsv ──────────────────────────────────────────────────────────────────

def read_done(path: str) -> Dict[str, dict]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8", newline="") as file:
        return {row["path"]: row for row in csv.DictReader(file, delimiter="\t")}


def append_done(path: str, row: dict) -> None:
    new_file = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=DONE_COLUMNS, delimiter="\t")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def write_tsv(path: str, columns: List[str], rows: List[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


# ── Modes ─────────────────────────────────────────────────────────────────────

REPORT_COLUMNS = [
    "path", "status", "title", "date", "date_rule", "reference_year",
    "correspondent", "document_type", "tags", "rules",
]


def dry_run(files: List[ArchiveFile], done: Dict[str, dict], report_path: str) -> None:
    rows = []
    statuses: Counter = Counter()
    date_rules: Counter = Counter()
    correspondents: Counter = Counter()
    types: Counter = Counter()
    tags: Counter = Counter()

    for archive_file in files:
        if archive_file.skip_reason:
            status = f"skip: {archive_file.skip_reason}"
        elif archive_file.relative in done:
            status = f"done: {done[archive_file.relative]['status']}"
        else:
            status = "upload"
        statuses[status] += 1

        parsed = parse_name(archive_file.name, archive_file.reference_year)
        _, final = classify(archive_file.folders, archive_file.name)
        if status == "upload":
            date_rules[parsed.rule] += 1
            correspondents[final.correspondent] += 1
            types[final.document_type] += 1
            tags.update(final.tags)

        rows.append({
            "path": archive_file.relative,
            "status": status,
            "title": parsed.title,
            "date": parsed.created.isoformat() if parsed.created else "",
            "date_rule": parsed.rule,
            "reference_year": archive_file.reference_year or "",
            "correspondent": final.correspondent or "",
            "document_type": final.document_type or "",
            "tags": ", ".join(sorted(final.tags)),
            "rules": " | ".join(final.trace),
        })

    write_tsv(report_path, REPORT_COLUMNS, rows)
    to_upload = statuses["upload"]
    print(f"Files found: {len(files)}")
    for status, count in sorted(statuses.items()):
        print(f"  {status}: {count}")
    if to_upload:
        print(f"Of the {to_upload} to upload:")
        print("  date rule: " + ", ".join(f"{k} {v}" for k, v in date_rules.most_common()))
        print(f"  with correspondent: {to_upload - correspondents[None]}, with document type: {to_upload - types[None]}")
        print("  tags: " + ", ".join(f"{k} {v}" for k, v in tags.most_common()))
    print(f"Report: {report_path}")


def upload(paperless: Paperless, files: List[ArchiveFile], done: Dict[str, dict], done_path: str) -> int:
    ids = {
        "correspondents": paperless.ids_by_name("correspondents"),
        "document_types": paperless.ids_by_name("document_types"),
        "tags": paperless.ids_by_name("tags"),
    }
    missing = (
        [c for c in CORRESPONDENTS if c not in ids["correspondents"]]
        + [t for t in DOCUMENT_TYPES if t not in ids["document_types"]]
        + [t for t in TAGS + (BACKFILL_TAG,) if t not in ids["tags"]]
    )
    if missing:
        print(f"ERROR: not in Paperless: {', '.join(missing)}. Run paperless_setup.py first.")
        return 1

    pending = [f for f in files if not f.skip_reason and f.relative not in done]
    print(f"Uploading {len(pending)} file(s); {len(files) - len(pending)} skipped or already done.")
    counts: Counter = Counter()
    started = time.time()

    for number, archive_file in enumerate(pending, start=1):
        reference_year = archive_file.reference_year
        parsed = parse_name(archive_file.name, reference_year)
        sent, _ = classify(archive_file.folders, archive_file.name)
        row = {
            "path": archive_file.relative,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
            "reference_year": reference_year or "",
            "task_id": "",
            "detail": "",
        }
        try:
            row["task_id"] = paperless.upload(
                archive_file.path,
                archive_file.name,
                parsed.title,
                parsed.created.isoformat() if parsed.created else None,
                correspondent=ids["correspondents"][sent.correspondent] if sent.correspondent else None,
                document_type=ids["document_types"][sent.document_type] if sent.document_type else None,
                tags=sorted(ids["tags"][tag] for tag in sent.tags),
            )
            row["status"] = "uploaded"
        except Refused as error:
            row["status"] = "refused"
            row["detail"] = str(error)
            print(f"  refused: {archive_file.relative}: {error}")
        except (Unreachable, AuthenticationFailed) as error:
            print(f"STOPPED after {number - 1} upload(s): {error}")
            print("Run again to continue; uploaded files are skipped.")
            return 1
        append_done(done_path, row)
        counts[row["status"]] += 1
        if number % 50 == 0 or number == len(pending):
            print(f"  {number}/{len(pending)} ({int(time.time() - started)} s)")

    print(f"Done: {counts['uploaded']} uploaded, {counts['refused']} refused. "
          f"Paperless now works through its queue; run --check when it is done.")
    return 0


CHECK_COLUMNS = ["category", "path", "document", "date", "reference_year", "detail"]


def review_row(paperless: Paperless, path: str, document: dict, reference_year: int, fix_dates: bool) -> dict:
    """A document dated more than a year from its reference year: fixed, or left for review."""
    row = {"category": "review", "path": path, "document": document["id"], "date": document["created"],
           "reference_year": reference_year, "detail": "date more than a year from reference"}

    # Paperless falls back to the day a document was added when neither its name nor its content holds a date.
    # A date from the name is never the fallback: the name rules only accept dates near the reference year.
    name_date = parse_name(os.path.basename(path), reference_year).created
    fell_back = name_date is None and document["created"] == document["added"][:10]
    if fell_back:
        if fix_dates:
            new_date = f"{reference_year}-01-01"
            paperless.update_document(document["id"], {"created": new_date})
            row.update(category="fixed", date=new_date, detail=f"no date found; was {document['created']}")
        else:
            row["detail"] = "no date found, Paperless used the day it was added (--fix-dates sets 1 January)"
    return row


def check(paperless: Paperless, done: Dict[str, dict], report_path: str, fix_dates: bool) -> int:
    tasks = {t["task_id"]: t for t in paperless.get_all("tasks", {"task_type": "consume_file"})}
    documents = {d["id"]: d for d in paperless.get_all("documents", {"fields": "id,created,added"})}

    rows = []
    counts: Counter = Counter()
    for path, entry in done.items():
        if entry["status"] == "refused":
            counts["refused at upload"] += 1
            rows.append({"category": "refused", "path": path, "detail": entry["detail"]})
            continue
        task = tasks.get(entry["task_id"])
        status = task["status"] if task else "unknown"
        if status == "success":
            counts["consumed"] += 1
            document_ids = task.get("related_document_ids") or []
            document = documents.get(document_ids[0]) if document_ids else None
            reference_year = int(entry["reference_year"]) if entry["reference_year"] else None
            if document and reference_year and abs(int(document["created"][:4]) - reference_year) > REVIEW_YEARS:
                row = review_row(paperless, path, document, reference_year, fix_dates)
                counts[row["category"]] += 1
                rows.append(row)
        elif status in ("failure", "revoked"):
            counts["failed"] += 1
            rows.append({"category": "failed", "path": path, "detail": task_reason(task)})
        else:
            counts[f"not finished ({status})"] += 1
            rows.append({"category": "pending", "path": path, "detail": status})

    write_tsv(report_path, CHECK_COLUMNS, rows)
    print(f"Recorded in done.tsv: {len(done)}")
    for category, count in sorted(counts.items()):
        print(f"  {category}: {count}")
    print(f"Report: {report_path}")
    return 0


# =============================================================================
# Main
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Import the document archive into Paperless.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="write a report of what would be uploaded")
    mode.add_argument("--check", action="store_true", help="report how the uploads went")
    parser.add_argument("--fix-dates", action="store_true",
                        help="with --check: date documents Paperless could not date as 1 January of the reference year")
    parser.add_argument("--path", action="append", default=[],
                        help="only this file or folder, relative to the archive (repeatable)")
    parser.add_argument("--archive", default=DEFAULT_ARCHIVE)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--token-file", default=DEFAULT_TOKEN_FILE)
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    args = parser.parse_args()

    archive = os.path.expanduser(args.archive)
    state_dir = os.path.expanduser(args.state_dir)
    os.makedirs(state_dir, exist_ok=True)
    done_path = os.path.join(state_dir, "done.tsv")
    done = read_done(done_path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    only_paths = [nfc(p) for p in args.path]

    if args.check:
        paperless = Paperless(args.url, args.token_file)
        return check(paperless, done, os.path.join(state_dir, f"check-{stamp}.tsv"), args.fix_dates)

    if not os.path.isdir(archive):
        print(f"ERROR: archive not found: {archive}")
        return 1
    files = walk_archive(archive, only_paths)
    if only_paths and not files:
        print(f"ERROR: nothing in the archive matches {only_paths}")
        return 1

    if args.dry_run:
        dry_run(files, done, os.path.join(state_dir, f"dry-run-{stamp}.tsv"))
        return 0

    paperless = Paperless(args.url, args.token_file)
    return upload(paperless, files, done, done_path)


if __name__ == "__main__":
    sys.exit(main())
