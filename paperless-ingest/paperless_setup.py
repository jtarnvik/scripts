"""
paperless_setup.py

Creates the classification in paperless_classification.py in Paperless: correspondents, document types, tags
and workflows — step 7 of the Paperless plan in paperless-plan.md.

Only creates what is missing, matched by name; it never changes or deletes anything. So it is safe to run again,
for example after adding a correspondent or a workflow to paperless_classification.py — but a change to an
existing workflow must be made in the web UI (or the workflow deleted there, and this run again).

    python3 paperless_setup.py --dry-run     # show what would be created
    python3 paperless_setup.py               # create it

    python3 paperless_setup.py --set-matching none   # before the backfill: no automatic guesses
    python3 paperless_setup.py --set-matching auto   # after it, then train the classifier

--set-matching changes only the matching of the correspondents, document types and tags listed in
paperless_classification.py (never Backfill or Inkorg), and creates nothing. Why: Paperless's automatic matching only fills
empty fields, from a classifier it retrains every hour. Trained on a handful of documents it guesses badly — in the
test batch (step 9) it gave four documents the rules had left empty the correspondent Ellevio and the type
Faktura, the only labelled document at the time. So it is off while the archive is imported, and trained on the
whole of it afterwards.

Defaults fit the production Mini; --url and --token-file change them.

Correspondents, document types and tags (except Backfill and Inkorg) get matching "Automatic", so Paperless learns
them from the documents. Inkorg is created as Paperless's inbox tag, which it gives every newly consumed document. Each workflow triggers on "Consumption started" from the consume folder, API uploads and the web
UI, with one trigger per filename pattern (a workflow runs if any trigger matches).

Python 3.9 (macOS's /usr/bin/python3), standard library only.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional

from paperless_classification import (
    BACKFILL_TAG, CORRESPONDENTS, DOCUMENT_TYPES, INBOX_TAG, TAGS, WORKFLOWS, Workflow,
)

MATCHING_NONE = 0
MATCHING_AUTOMATIC = 6

TRIGGER_CONSUMPTION_STARTED = 1
SOURCE_CONSUME_FOLDER = 1
SOURCE_API_UPLOAD = 2
SOURCE_WEB_UI = 4
ACTION_ASSIGNMENT = 1


class Api:

    def __init__(self, url: str, token_file: str) -> None:
        self.url = url.rstrip("/")
        with open(os.path.expanduser(token_file), encoding="utf-8") as file:
            self.token = file.read().strip()

    def get_all(self, endpoint: str) -> list:
        results = []
        path = f"/api/{endpoint}/?page_size=100"
        while path:
            page = self._request(path)
            results.extend(page["results"])
            # "next" is a full URL; keep its path and query, so the request still goes to self.url
            path = _path_and_query(page["next"]) if page["next"] else None
        return results

    def create(self, endpoint: str, body: dict) -> dict:
        return self._request(f"/api/{endpoint}/", body)

    def patch(self, endpoint: str, item_id: int, body: dict) -> dict:
        return self._request(f"/api/{endpoint}/{item_id}/", body, method="PATCH")

    def _request(self, path: str, body: Optional[dict] = None, method: Optional[str] = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method)
        request.add_header("Authorization", f"Token {self.token}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1000]
            raise SystemExit(f"ERROR: {request.get_method()} {path}: HTTP {error.code}: {detail}")


def _path_and_query(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.path}?{parts.query}"


def ensure_named(
    api: Api,
    endpoint: str,
    names,
    dry_run: bool,
    matching: Dict[str, int],
    extra_fields: Optional[Dict[str, dict]] = None,
) -> Dict[str, int]:
    """Creates the names that do not exist yet. Returns name -> id for all of them. extra_fields adds fields to
    the body of a name when it is created, e.g. {"Inkorg": {"is_inbox_tag": True}}."""
    existing = {item["name"]: item["id"] for item in api.get_all(endpoint)}
    for name in names:
        if name in existing:
            print(f"  exists   {endpoint}: {name}")
            continue
        algorithm = matching.get(name, MATCHING_AUTOMATIC)
        if dry_run:
            print(f"  would create {endpoint}: {name}")
            existing[name] = -1
            continue
        body = {"name": name, "matching_algorithm": algorithm, "is_insensitive": True}
        body.update((extra_fields or {}).get(name, {}))
        created = api.create(endpoint, body)
        existing[name] = created["id"]
        print(f"  created  {endpoint}: {name} (id {created['id']})")
    return existing


def workflow_body(workflow: Workflow, order: int, correspondents: dict, types: dict, tags: dict) -> dict:
    assign = workflow.assign
    action = {"type": ACTION_ASSIGNMENT, "assign_tags": [tags[tag] for tag in assign.tags]}
    if assign.correspondent:
        action["assign_correspondent"] = correspondents[assign.correspondent]
    if assign.document_type:
        action["assign_document_type"] = types[assign.document_type]
    triggers = [
        {
            "type": TRIGGER_CONSUMPTION_STARTED,
            "sources": [SOURCE_CONSUME_FOLDER, SOURCE_API_UPLOAD, SOURCE_WEB_UI],
            "filter_filename": pattern,
        }
        for pattern in workflow.patterns
    ]
    return {"name": workflow.name, "order": order, "enabled": True, "triggers": triggers, "actions": [action]}


def set_matching(api: Api, algorithm: int, dry_run: bool) -> None:
    """Sets the matching of every correspondent, document type and tag in paperless_classification.py."""
    for endpoint, names in (("correspondents", CORRESPONDENTS), ("document_types", DOCUMENT_TYPES), ("tags", TAGS)):
        changed = 0
        for item in api.get_all(endpoint):
            if item["name"] not in names or item["matching_algorithm"] == algorithm:
                continue
            if not dry_run:
                api.patch(endpoint, item["id"], {"matching_algorithm": algorithm})
            changed += 1
        verb = "would change" if dry_run else "changed"
        print(f"  {endpoint}: {verb} {changed} of {len(names)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--url", default="http://127.0.0.1:9400")
    parser.add_argument("--token-file", default="~/.config/paperless/token")
    parser.add_argument("--dry-run", action="store_true", help="show what would be done, change nothing")
    parser.add_argument("--set-matching", choices=("none", "auto"),
                        help="only switch matching of the correspondents, types and tags to none or automatic")
    args = parser.parse_args()

    api = Api(args.url, args.token_file)

    if args.set_matching:
        algorithm = MATCHING_NONE if args.set_matching == "none" else MATCHING_AUTOMATIC
        print(f"Matching -> {args.set_matching}")
        set_matching(api, algorithm, args.dry_run)
        return 0

    print("Correspondents")
    correspondents = ensure_named(api, "correspondents", CORRESPONDENTS, args.dry_run, {})
    print("Document types")
    types = ensure_named(api, "document_types", DOCUMENT_TYPES, args.dry_run, {})
    print("Tags")
    tags = ensure_named(
        api, "tags", TAGS + (BACKFILL_TAG, INBOX_TAG), args.dry_run,
        matching={BACKFILL_TAG: MATCHING_NONE, INBOX_TAG: MATCHING_NONE},
        extra_fields={INBOX_TAG: {"is_inbox_tag": True}},
    )

    print("Workflows")
    existing = {item["name"] for item in api.get_all("workflows")}
    for order, workflow in enumerate(WORKFLOWS, start=1):
        if workflow.name in existing:
            print(f"  exists   workflow: {workflow.name}")
            continue
        body = workflow_body(workflow, order * 10, correspondents, types, tags)
        if args.dry_run:
            print(f"  would create workflow: {workflow.name} (order {order * 10}, {len(body['triggers'])} pattern(s))")
            continue
        created = api.create("workflows", body)
        print(f"  created  workflow: {workflow.name} (id {created['id']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
