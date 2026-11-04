"""
paperless_api.py

The small part of Paperless-ngx's REST API (version 3, API version 10) that the ingest and the backfill use.

Errors are sorted by what the caller should do:
  - Unreachable          — no answer, or a server error: try again later
  - AuthenticationFailed — the token was refused (HTTP 401/403)
  - Refused              — this request was refused (any other HTTP 4xx): trying again will not help

Python 3.9 (macOS's /usr/bin/python3), standard library only.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Dict, Iterable, List, Optional

HTTP_TIMEOUT_SECONDS = 120


class Unreachable(Exception):
    """Paperless did not answer, or answered with a server error. Try again later."""


class AuthenticationFailed(Exception):
    """Paperless refused the token (HTTP 401/403)."""


class Refused(Exception):
    """Paperless refused this particular request (HTTP 4xx). Trying again will not help."""


class Paperless:

    def __init__(self, base_url: str, token_file: str) -> None:
        self.base_url = base_url.rstrip("/")
        with open(os.path.expanduser(token_file), encoding="utf-8") as file:
            self.token = file.read().strip()

    def upload(
        self,
        path: str,
        upload_name: str,
        title: str,
        created: Optional[str],
        correspondent: Optional[int] = None,
        document_type: Optional[int] = None,
        tags: Iterable[int] = (),
    ) -> str:
        """Uploads one document. Returns the consumption task's ID."""
        fields = [("title", title)]
        if created is not None:
            fields.append(("created", created))
        if correspondent is not None:
            fields.append(("correspondent", str(correspondent)))
        if document_type is not None:
            fields.append(("document_type", str(document_type)))
        for tag in tags:
            fields.append(("tags", str(tag)))
        with open(path, "rb") as file:
            content = file.read()
        boundary, body = _multipart(fields, "document", upload_name, content)
        answer = self._request(
            "/api/documents/post_document/",
            data=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        return json.loads(answer)

    def task(self, task_id: str) -> Optional[dict]:
        """The consumption task, or None if Paperless does not know it."""
        answer = json.loads(self._request("/api/tasks/?" + urllib.parse.urlencode({"task_id": task_id})))
        # A plain list in older API versions, a page with "results" in version 10
        tasks = answer["results"] if isinstance(answer, dict) else answer
        return tasks[0] if tasks else None

    def get_all(self, endpoint: str, params: Optional[Dict[str, str]] = None) -> List[dict]:
        """Every item of a list endpoint ("documents", "tasks", "tags" …), across all pages."""
        query = dict(params or {})
        query.setdefault("page_size", "100")
        path: Optional[str] = f"/api/{endpoint}/?" + urllib.parse.urlencode(query)
        results: List[dict] = []
        while path:
            page = json.loads(self._request(path))
            results.extend(page["results"])
            # "next" is a full URL; keep its path and query, so the request still goes to base_url
            next_url = page.get("next")
            path = _path_and_query(next_url) if next_url else None
        return results

    def update_document(self, document_id: int, fields: dict) -> None:
        """Changes fields of one document, e.g. {"created": "2013-01-01"}."""
        self._request(
            f"/api/documents/{document_id}/",
            data=json.dumps(fields).encode(),
            content_type="application/json",
            method="PATCH",
        )

    def ids_by_name(self, endpoint: str) -> Dict[str, int]:
        """name -> id for "correspondents", "document_types" or "tags"."""
        return {item["name"]: item["id"] for item in self.get_all(endpoint)}

    def _request(
        self,
        path: str,
        data: Optional[bytes] = None,
        content_type: Optional[str] = None,
        method: Optional[str] = None,
    ) -> bytes:
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Authorization", f"Token {self.token}")
        request.add_header("Accept", "application/json")
        if content_type is not None:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            if error.code in (401, 403):
                raise AuthenticationFailed(f"HTTP {error.code}: {detail}")
            if error.code >= 500:
                raise Unreachable(f"HTTP {error.code}: {detail}")
            raise Refused(f"HTTP {error.code}: {detail}")
        except (urllib.error.URLError, OSError) as error:
            raise Unreachable(str(error))


def task_reason(task: dict) -> str:
    """A readable reason from a failed task. API version 10 keeps it in result_data, as "duplicate_of" (with
    PAPERLESS_CONSUMER_DELETE_DUPLICATES), "reason" or "error_message"."""
    result = task.get("result_data")
    if isinstance(result, dict):
        if result.get("duplicate_of") is not None:
            where = " (in the trash)" if result.get("duplicate_in_trash") else ""
            return f"Duplicate of document #{result['duplicate_of']}{where}"
        for field in ("reason", "error_message"):
            if result.get(field):
                return str(result[field])[:500]
    if result:
        return json.dumps(result, ensure_ascii=False)[:500]
    return f"status {task.get('status')}"


def _path_and_query(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.path}?{parts.query}"


def _multipart(fields: list, file_field: str, file_name: str, content: bytes):
    boundary = uuid.uuid4().hex
    quoted_name = file_name.replace('"', "%22")
    parts = []
    for name, value in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{quoted_name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(parts)
