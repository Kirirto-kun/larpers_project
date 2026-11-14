"""Synchronous, standard-library client for the provisional Campus v1 API.

Wire DTOs remain runner-owned until foundation publishes generated contracts.
See the accompanying README for the exact payloads and integration gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ctypes
import errno
import hashlib
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import random
import re
import shutil
import socket
import ssl
import stat
import sys
import tempfile
import threading
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

VERSION = "0.1.0"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}
_FENCE_CODES = {"STALE_ATTEMPT", "LEASE_EXPIRED", "SESSION_REPLACED"}
_PREVIEW_INTERIM = {"pending", "queued", "analyzing", "planning", "preparing"}
_PREVIEW_TERMINAL = {"failed", "cancelled", "expired", "rejected"}


class CampusError(Exception):
    """Base class for SDK errors; credentials are never included in messages."""


class ProtocolError(CampusError):
    """The coordinator's protocol or response does not match the v1 contract."""


class TransportError(CampusError):
    """A bounded network operation failed."""

    def __init__(self, *, retryable: bool = False):
        self.retryable = retryable
        super().__init__("coordinator transport failed")


class APIError(CampusError):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False, request_id: str = ""):
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.request_id = request_id
        super().__init__(f"API {code} (HTTP {status}): {message}")


class AuthenticationError(APIError):
    """HTTP 401/403; refresh credentials explicitly before making another call."""


class StaleAttemptError(APIError):
    """The attempt, lease, or node session has been fenced by the server."""


class PreviewError(CampusError):
    def __init__(self, preview_id: str, plan_revision: int | str | None, state: str):
        self.preview_id = preview_id
        self.plan_revision = plan_revision
        self.state = state
        super().__init__(f"preview {preview_id} is {state}")


class NeedsInputError(PreviewError):
    def __init__(self, preview_id: str, plan_revision: int | str, questions: list):
        self.questions = questions
        super().__init__(preview_id, plan_revision, "needs_input")


class SubmissionUnknownError(CampusError):
    """A run may have committed; resume using these coordinates and the same key."""

    def __init__(self, preview_id: str, plan_revision: int | str, idempotency_key: str):
        self.preview_id = preview_id
        self.plan_revision = plan_revision
        self.idempotency_key = idempotency_key
        super().__init__("submission acknowledgement is unknown; resume the same preview and idempotency key")


class JobFailedError(CampusError):
    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"job {job_id} failed")


class JobCancelledError(CampusError):
    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"job {job_id} was cancelled")


class DownloadError(CampusError):
    """An artifact manifest or transfer violates the requested output policy."""


class IntegrityError(DownloadError):
    """The complete artifact does not match its immutable SHA-256 digest."""


class UnsafePathError(DownloadError):
    """An artifact or destination has an unsafe, aliased, or linked path."""


@dataclass(frozen=True)
class JobHandle:
    job_id: str
    preview_id: str
    plan_revision: int | str
    idempotency_key: str
    _client: Client = field(repr=False, compare=False)

    def wait(self, **options) -> dict:
        return self._client.wait(self.job_id, **options)

    def logs(self, **options) -> dict:
        return self._client.logs(self.job_id, **options)

    def cancel(self) -> dict:
        return self._client.cancel(self.job_id)

    def download(self, destination) -> Path:
        return self._client.download(self.job_id, destination)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _identity(value, *, server: bool = False) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        error = ProtocolError if server else ValueError
        raise error("invalid API identity")
    return value


def _revision(value) -> int | str:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and 0 < len(value) <= 256 and all(ord(c) >= 32 for c in value):
        return value
    raise ProtocolError("invalid preview plan_revision")


def _key(value: str | None) -> str:
    value = uuid.uuid4().hex if value is None else value
    if not isinstance(value, str) or not re.fullmatch(r"[!-~]{1,200}", value):
        raise ValueError("idempotency_key must contain 1 to 200 visible ASCII characters")
    return value


def _positive(value, name: str, *, zero: bool = False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (not zero and value == 0):
        raise ValueError(f"invalid {name}")
    return value


def _deadline(timeout) -> float | None:
    return None if timeout is None else time.monotonic() + _positive(timeout, "timeout", zero=True)


def _check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("Campus observation timed out; the job continues until explicitly cancelled")


def _remaining_timeout(deadline):
    _check_deadline(deadline)
    return None if deadline is None else max(0.001, deadline - time.monotonic())


def _transient_network(error) -> bool:
    if isinstance(error, URLError):
        return _transient_network(error.reason)
    if isinstance(error, ssl.SSLError):
        return False
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    if isinstance(error, (TimeoutError, ConnectionError, http.client.IncompleteRead, http.client.RemoteDisconnected)):
        return True
    return isinstance(error, OSError) and error.errno in {
        errno.ECONNREFUSED, errno.ECONNRESET, errno.ECONNABORTED, errno.ETIMEDOUT,
        errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EPIPE,
    }


def _decode_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def finite(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non-finite JSON number")
        return result

    def constant(_):
        raise ValueError("nonstandard JSON constant")

    try:
        result = json.loads(data, object_pairs_hook=pairs, parse_float=finite, parse_constant=constant)
        pending = [(result, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 64:
                raise ValueError("JSON nesting exceeds limit")
            if isinstance(item, dict):
                pending.extend((value, depth + 1) for value in item.values())
            elif isinstance(item, list):
                pending.extend((value, depth + 1) for value in item)
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError("malformed or ambiguous API JSON response") from None


class Client:
    """Connect with HTTPS, or HTTP on loopback for local development.

    A Client is synchronous. Polling only observes jobs; it never resubmits them.
    Each individual HTTP exchange is bounded by ``timeout`` and ``max_attempts``.
    """

    def __init__(self, endpoint: str, credential: str = "", *, timeout: float = 40,
                 max_attempts: int = 3, backoff: float = 0.2, poll_interval: float = 0.5,
                 max_json_bytes: int = 8 << 20, max_download_bytes: int = 1 << 30,
                 max_download_files: int = 1000, max_artifact_bytes: int | None = None):
        if not isinstance(endpoint, str) or any(ord(c) <= 32 for c in endpoint):
            raise ValueError("invalid coordinator endpoint")
        try:
            url = urlsplit(endpoint)
            port = url.port
            host = url.hostname
        except ValueError:
            raise ValueError("invalid coordinator endpoint") from None
        if (not host or url.username is not None or url.password is not None or url.query or url.fragment
                or url.path not in {"", "/", "/api/v1", "/api/v1/"} or port == 0):
            raise ValueError("invalid coordinator endpoint")
        loopback = host.lower() == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if url.scheme != "https" and not (url.scheme == "http" and loopback):
            raise ValueError("coordinator requires HTTPS; HTTP is available only on loopback")
        if not isinstance(credential, str) or any(ord(c) < 32 or ord(c) > 126 for c in credential):
            raise ValueError("invalid credential")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        for name, value in {"max_json_bytes": max_json_bytes, "max_download_bytes": max_download_bytes,
                            "max_download_files": max_download_files,
                            "max_artifact_bytes": max_download_bytes if max_artifact_bytes is None else max_artifact_bytes}.items():
            if type(value) is not int or value < 1:
                raise ValueError(f"invalid {name}")
        self.endpoint = f"{url.scheme}://{url.netloc}/api/v1"
        self._credential = credential
        self.timeout = _positive(timeout, "timeout")
        self.max_attempts = max_attempts
        self.backoff = _positive(backoff, "backoff", zero=True)
        self.poll_interval = _positive(poll_interval, "poll_interval", zero=True)
        self.max_json_bytes = max_json_bytes
        self.max_download_bytes = max_download_bytes
        self.max_download_files = max_download_files
        self.max_artifact_bytes = max_download_bytes if max_artifact_bytes is None else max_artifact_bytes
        self._opener = build_opener(_NoRedirects())
        self._protocol = None
        self._protocol_lock = threading.Lock()

    def _redact(self, value, limit: int = 1024) -> str:
        value = value if isinstance(value, str) else "request rejected"
        if self._credential:
            value = value.replace(self._credential, "[REDACTED]")
        return "".join(c if ord(c) >= 32 else " " for c in value)[:limit]

    def _pause(self, attempt: int, deadline=None):
        delay = self.backoff * (2 ** min(attempt, 6))
        if delay:
            delay += random.SystemRandom().uniform(0, delay / 2)
            remaining = _remaining_timeout(deadline)
            time.sleep(delay if remaining is None else min(delay, remaining))
        _check_deadline(deadline)

    def _poll_pause(self, deadline):
        _check_deadline(deadline)
        delay = self.poll_interval if deadline is None else min(self.poll_interval, max(0, deadline - time.monotonic()))
        if delay:
            time.sleep(delay)
        _check_deadline(deadline)

    def _api_error(self, response) -> APIError:
        try:
            raw = response.read(min(self.max_json_bytes, 65536) + 1)
            payload = _decode_json(raw) if len(raw) <= min(self.max_json_bytes, 65536) else {}
            if not isinstance(payload, dict):
                payload = {}
        except (ProtocolError, OSError, http.client.HTTPException):
            payload = {}
        finally:
            response.close()
        status = response.code
        code = payload.get("code", "HTTP_ERROR")
        code = code if isinstance(code, str) else "HTTP_ERROR"
        terminal = status in {401, 403} or code in _FENCE_CODES
        error_type = AuthenticationError if status in {401, 403} else StaleAttemptError if code in _FENCE_CODES else APIError
        return error_type(status, self._redact(code, 128), self._redact(payload.get("message", "request rejected")),
                          retryable=not terminal and status in _TRANSIENT_STATUS,
                          request_id=self._redact(payload.get("request_id", ""), 128))

    def _open(self, method: str, route: str, body: bytes | None = None, key: str | None = None, headers: dict | None = None, *, deadline=None):
        request_headers = {"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "campus/" + VERSION}
        if self._credential:
            request_headers["Authorization"] = "Bearer " + self._credential
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if key:
            request_headers["Idempotency-Key"] = key
        request_headers.update(headers or {})
        request = Request(self.endpoint + route, data=body, headers=request_headers, method=method)
        remaining = _remaining_timeout(deadline)
        try:
            return self._opener.open(request, timeout=self.timeout if remaining is None else min(self.timeout, remaining))
        except HTTPError as error:
            raise self._api_error(error) from None
        except (URLError, OSError, http.client.HTTPException) as error:
            raise TransportError(retryable=_transient_network(error)) from None

    def _json_once(self, method, route, raw, key, deadline=None):
        with self._open(method, route, raw, key, deadline=deadline) as response:
            try:
                data = response.read(self.max_json_bytes + 1)
            except (OSError, http.client.HTTPException) as error:
                _check_deadline(deadline)
                raise TransportError(retryable=_transient_network(error)) from None
            _check_deadline(deadline)
            if len(data) > self.max_json_bytes:
                raise ProtocolError("API response exceeds JSON byte limit")
            if response.status == 204:
                return {}
            result = _decode_json(data)
            if not isinstance(result, dict):
                raise ProtocolError("API response must be a JSON object")
            return result

    def _json(self, method, route, payload=None, key=None, *, attempts=None, deadline=None):
        raw = None
        if payload is not None:
            try:
                raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            except (ValueError, TypeError, UnicodeError):
                raise ValueError("request must contain JSON values") from None
            if len(raw) > self.max_json_bytes:
                raise ValueError("request exceeds JSON byte limit")
        attempts = self.max_attempts if attempts is None else attempts
        for attempt in range(attempts):
            _check_deadline(deadline)
            try:
                return self._json_once(method, route, raw, key, deadline)
            except (APIError, TransportError) as error:
                _check_deadline(deadline)
                if not error.retryable or attempt + 1 == attempts:
                    raise
                self._pause(attempt, deadline)
        raise AssertionError("unreachable")

    def handshake(self, *, timeout: float | None = None) -> dict:
        deadline = _deadline(timeout)
        with self._protocol_lock:
            _check_deadline(deadline)
            if self._protocol is None:
                protocol = self._json("GET", "/protocol", deadline=deadline)

                def version(value):
                    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
                        raise ProtocolError("incompatible coordinator protocol version")
                    return tuple(map(int, value.split(".")))

                low = version(protocol.get("min_runner_version"))
                high = version(protocol.get("max_runner_version"))
                if protocol.get("api_version") != "1" or not low <= version(VERSION) <= high:
                    raise ProtocolError("incompatible coordinator protocol version")
                self._protocol = protocol
            return dict(self._protocol)

    def submit(self, request: dict, *, idempotency_key: str | None = None, timeout: float | None = None) -> JobHandle:
        """Plan a request, then run its exact ready preview once.

        Missing answers raise NeedsInputError. The SDK never invents answers.
        ``timeout`` limits preview observation; it does not cancel a preview/job.
        """
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        key = _key(idempotency_key)
        deadline = _deadline(timeout)
        self.handshake(timeout=_remaining_timeout(deadline))
        preview = self._json("POST", "/job-previews", request, key, deadline=deadline)
        preview_id = _identity(preview.get("preview_id"), server=True)
        return self._submit_preview(preview_id, key, deadline)

    def answer_preview(self, preview_id: str, plan_revision: int | str, answers: list[dict], *, idempotency_key: str | None = None) -> dict:
        """Apply only the owner's explicit answers to one fenced preview.

        Does not submit a job. Call submit_preview afterward to observe the
        same preview's new ready revision. Replaying the exact same batch uses
        the same derived key unless the caller supplies a persisted key.
        """
        preview_id = _identity(preview_id)
        revision = _revision(plan_revision)
        if not isinstance(answers, list) or not 1 <= len(answers) <= 100:
            raise ValueError("answers must contain 1 to 100 explicit answers")
        seen = set()
        for answer in answers:
            if not isinstance(answer, dict) or set(answer) != {"question_id", "value"}:
                raise ValueError("each answer requires only question_id and value")
            question_id = _identity(answer["question_id"])
            if question_id in seen:
                raise ValueError("duplicate answer question_id")
            seen.add(question_id)
        payload = {"plan_revision": revision, "answers": answers}
        pending = [(payload, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 16:
                raise ValueError("answer JSON nesting exceeds limit")
            if type(value) is dict:
                if not all(isinstance(key, str) for key in value):
                    raise ValueError("answer JSON object keys must be strings")
                pending.extend((item, depth + 1) for item in value.values())
            elif type(value) is list:
                pending.extend((item, depth + 1) for item in value)
            elif type(value) is float:
                if not math.isfinite(value):
                    raise ValueError("answer JSON numbers must be finite")
            elif value is not None and type(value) not in {str, bool, int}:
                raise ValueError("answers must contain only JSON values")
        try:
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, UnicodeError):
            raise ValueError("answers must contain valid JSON values") from None
        if len(raw) > min(65536, self.max_json_bytes):
            raise ValueError("answer request exceeds 64 KiB or JSON byte limit")
        # Freeze caller-owned mutable values before deriving a recovery key or
        # doing network I/O. The same canonical body is used on every retry.
        payload = json.loads(raw)
        if idempotency_key is None:
            identity = json.dumps({"preview_id": preview_id, **payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            idempotency_key = "answers:" + hashlib.sha256(identity).hexdigest()
        key = _key(idempotency_key)
        self.handshake()
        receipt = self._json("POST", "/job-previews/" + preview_id + "/answers", payload, key)
        if receipt.get("preview_id", preview_id) != preview_id:
            raise ProtocolError("answer receipt preview identity mismatch")
        return receipt

    def submit_preview(self, preview_id: str, *, idempotency_key: str | None = None, timeout: float | None = None) -> JobHandle:
        """Observe an existing preview and run its current ready revision.

        Creates no new preview and invents no answers. For an unknown job POST
        acknowledgement, use resume_submission with its frozen revision/key.
        """
        preview_id = _identity(preview_id)
        key = _key(idempotency_key)
        deadline = _deadline(timeout)
        self.handshake(timeout=_remaining_timeout(deadline))
        return self._submit_preview(preview_id, key, deadline)

    def _submit_preview(self, preview_id, key, deadline):
        while True:
            _check_deadline(deadline)
            preview = self._json("GET", "/job-previews/" + preview_id, deadline=deadline)
            if preview.get("preview_id") != preview_id:
                raise ProtocolError("preview identity mismatch")
            state = preview.get("state")
            if not isinstance(state, str) or state not in _PREVIEW_INTERIM | _PREVIEW_TERMINAL | {"ready", "needs_input"}:
                raise ProtocolError("unsupported preview state")
            if state in _PREVIEW_TERMINAL:
                revision = preview.get("plan_revision")
                if revision is not None:
                    revision = _revision(revision)
                raise PreviewError(preview_id, revision, state)
            questions = preview.get("questions", [])
            if not isinstance(questions, list):
                raise ProtocolError("invalid preview questions")
            if state in {"ready", "needs_input"} or questions:
                revision = _revision(preview.get("plan_revision"))
                if state == "needs_input" or questions:
                    raise NeedsInputError(preview_id, revision, questions)
                return self._submit_ready(preview_id, revision, key)
            self._poll_pause(deadline)

    def _handle(self, job, preview_id, revision, key) -> JobHandle:
        job_id = _identity(job.get("job_id"), server=True)
        return JobHandle(job_id, preview_id, revision, key, self)

    def _lookup_submission(self, preview_id, revision, key) -> JobHandle | None:
        # This optional filtered lookup is provisional. An unsupported lookup
        # falls back to replaying only the exact same POST/body/key.
        try:
            result = self._json("GET", "/jobs?" + urlencode({"idempotency_key": key}), attempts=1)
        except ProtocolError:
            return None
        except (APIError, TransportError) as error:
            if isinstance(error, (AuthenticationError, StaleAttemptError)):
                raise
            if error.retryable or isinstance(error, APIError) and error.status in {400, 404, 405, 501}:
                return None
            raise
        jobs = result.get("jobs")
        if not isinstance(jobs, list):
            return None
        # An unfiltered list or an old server must never attach an unrelated job.
        matches = [job for job in jobs if isinstance(job, dict) and job.get("idempotency_key") == key]
        if len(matches) > 1:
            raise ProtocolError("ambiguous submission receipt")
        return self._handle(matches[0], preview_id, revision, key) if matches else None

    def _submit_ready(self, preview_id, revision, key) -> JobHandle:
        for attempt in range(self.max_attempts):
            try:
                job = self._json("POST", "/jobs", {"preview_id": preview_id, "plan_revision": revision}, key, attempts=1)
                return self._handle(job, preview_id, revision, key)
            except (APIError, TransportError, ProtocolError) as error:
                if isinstance(error, (APIError, TransportError)) and not error.retryable:
                    raise
                receipt = self._lookup_submission(preview_id, revision, key)
                if receipt is not None:
                    return receipt
                if isinstance(error, ProtocolError) or attempt + 1 == self.max_attempts:
                    raise SubmissionUnknownError(preview_id, revision, key) from None
                self._pause(attempt)
        raise AssertionError("unreachable")

    def resume_submission(self, preview_id: str, plan_revision: int | str, idempotency_key: str) -> JobHandle:
        """Reconcile an unknown run ACK; never create another preview or key."""
        preview_id = _identity(preview_id)
        revision = _revision(plan_revision)
        key = _key(idempotency_key)
        self.handshake()
        receipt = self._lookup_submission(preview_id, revision, key)
        return receipt if receipt is not None else self._submit_ready(preview_id, revision, key)

    def get_job(self, job_id: str, *, timeout: float | None = None) -> dict:
        job_id = _identity(job_id)
        deadline = _deadline(timeout)
        self.handshake(timeout=_remaining_timeout(deadline))
        job = self._json("GET", "/jobs/" + job_id, deadline=deadline)
        if job.get("job_id") != job_id or not isinstance(job.get("state"), str) or not job["state"]:
            raise ProtocolError("invalid job identity or state")
        return job

    def wait(self, job_id: str, *, timeout: float | None = None) -> dict:
        """Observe a terminal job; timeout/disconnection does not cancel or run it."""
        job_id = _identity(job_id)
        deadline = _deadline(timeout)
        while True:
            _check_deadline(deadline)
            job = self.get_job(job_id, timeout=_remaining_timeout(deadline))
            _check_deadline(deadline)
            if job["state"] == "succeeded":
                return job
            if job["state"] == "failed":
                raise JobFailedError(job_id)
            if job["state"] == "cancelled":
                raise JobCancelledError(job_id)
            self._poll_pause(deadline)

    def logs(self, job_id: str, *, cursor: str | None = None, limit: int = 1000) -> dict:
        job_id = _identity(job_id)
        if type(limit) is not int or not 1 <= limit <= 10000 or cursor is not None and (not isinstance(cursor, str) or len(cursor) > 4096):
            raise ValueError("invalid log cursor or limit")
        query = {"limit": limit}
        if cursor is not None:
            query["cursor"] = cursor
        self.handshake()
        page = self._json("GET", "/jobs/" + job_id + "/logs?" + urlencode(query))
        if not isinstance(page.get("records"), list) or page.get("next_cursor") is not None and not isinstance(page["next_cursor"], str):
            raise ProtocolError("invalid log page")
        records = []
        for record in page["records"]:
            if isinstance(record, str):
                records.append({"text": record})
            elif isinstance(record, dict):
                text = record.get("text", record.get("message"))
                if not isinstance(text, str):
                    raise ProtocolError("invalid log record text")
                normalized = {key: value for key, value in record.items() if key != "message"}
                normalized["text"] = text
                records.append(normalized)
            else:
                raise ProtocolError("invalid log record")
        return {**page, "records": records}

    def cancel(self, job_id: str) -> dict:
        job_id = _identity(job_id)
        self.handshake()
        receipt = self._json("POST", "/jobs/" + job_id + "/cancel", {}, "cancel:" + job_id)
        if receipt.get("job_id", job_id) != job_id:
            raise ProtocolError("cancel receipt identity mismatch")
        return receipt

    def download(self, job_id: str, destination) -> Path:
        """Verify all outputs in private staging, then publish a new directory.

        Existing destinations and symlink/reparse ancestors are rejected. A
        transport interruption resumes with Range inside this call. Failure
        removes staging and never exposes a partially verified destination.
        """
        job = self.get_job(job_id)
        if job["state"] == "failed":
            raise JobFailedError(job_id)
        if job["state"] == "cancelled":
            raise JobCancelledError(job_id)
        if job["state"] != "succeeded":
            raise DownloadError("job outputs are not finalized")
        artifacts = self._artifacts(job.get("artifacts"))
        destination = Path(os.path.abspath(os.fspath(destination)))
        _check_location(destination)
        if os.path.lexists(destination):
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _check_location(destination)
        staging = Path(tempfile.mkdtemp(prefix=".campus-download-", dir=destination.parent))
        try:
            for artifact in artifacts:
                target = staging.joinpath(*PurePosixPath(artifact["path"]).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                _check_location(target)
                self._download_artifact(artifact, target)
            _verify_tree(staging, artifacts)
            _check_location(destination)
            _publish_directory(staging, destination)
            _sync_dir(destination.parent)
            return destination
        finally:
            if os.path.lexists(staging):
                if _is_link(staging.lstat()):
                    staging.unlink()
                else:
                    shutil.rmtree(staging)

    def _artifacts(self, entries) -> list[dict]:
        if not isinstance(entries, list) or len(entries) > self.max_download_files:
            raise DownloadError("artifact file count exceeds limit or is malformed")
        artifacts = []
        total = 0
        names = {}
        files = set()
        directories = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise DownloadError("malformed artifact manifest")
            path = _safe_path(entry.get("path"))
            parts = PurePosixPath(path).parts
            parent = ""
            for index, part in enumerate(parts):
                folded = unicodedata.normalize("NFC", part).casefold()
                key = parent + "/" + folded
                if key in names and names[key] != part:
                    raise UnsafePathError("artifact paths collide across case or Unicode normalization")
                names[key] = part
                if index + 1 < len(parts):
                    if key in files:
                        raise UnsafePathError("artifact path is both file and directory")
                    directories.add(key)
                elif key in files or key in directories:
                    raise UnsafePathError("duplicate artifact path or file/directory collision")
                else:
                    files.add(key)
                parent = key
            artifact_id, digest, size = entry.get("artifact_id"), entry.get("digest"), entry.get("size_bytes")
            if (not isinstance(artifact_id, str) or not _ID.fullmatch(artifact_id)
                    or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
                    or type(size) is not int or size < 0 or size > self.max_artifact_bytes):
                raise DownloadError("invalid artifact identity, digest, or byte count")
            total += size
            if total > self.max_download_bytes:
                raise DownloadError("artifact total exceeds download byte limit")
            artifacts.append({"artifact_id": artifact_id, "digest": digest, "size_bytes": size, "path": path})
        return artifacts

    def _download_artifact(self, artifact, target):
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags, 0o600)
        with os.fdopen(descriptor, "w+b") as stream:
            complete = artifact["size_bytes"] == 0
            for attempt in range(self.max_attempts):
                if complete:
                    break
                offset = stream.tell()
                headers = {"Accept": "application/octet-stream"}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                try:
                    with self._open("GET", "/artifacts/" + artifact["artifact_id"] + "/content", headers=headers) as response:
                        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                            raise DownloadError("encoded artifact responses are forbidden")
                        if response.status == 200:
                            if offset:
                                stream.seek(0)
                                stream.truncate()
                                offset = 0
                        elif response.status == 206:
                            value = response.headers.get("Content-Range", "")
                            match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)", value)
                            if not match or tuple(map(int, match.groups())) != (offset, artifact["size_bytes"] - 1, artifact["size_bytes"]):
                                raise DownloadError("artifact resume Content-Range does not match manifest")
                        else:
                            raise DownloadError("unexpected artifact HTTP status")
                        remaining = artifact["size_bytes"] - offset
                        length = response.headers.get("Content-Length")
                        if length is not None and (not re.fullmatch(r"[0-9]+", length) or int(length) != remaining):
                            raise DownloadError("artifact Content-Length does not match manifest")
                        while True:
                            try:
                                chunk = response.read(min(65536, remaining + 1))
                            except (OSError, http.client.HTTPException) as error:
                                partial = getattr(error, "partial", b"")
                                if isinstance(partial, bytes) and partial:
                                    if len(partial) > remaining:
                                        raise DownloadError("artifact response exceeds byte limit") from None
                                    stream.write(partial)
                                raise TransportError(retryable=_transient_network(error)) from None
                            if not chunk:
                                break
                            if len(chunk) > remaining:
                                raise DownloadError("artifact response exceeds byte limit")
                            stream.write(chunk)
                            remaining -= len(chunk)
                        if remaining:
                            raise TransportError(retryable=True)
                        complete = True
                        break
                except (APIError, TransportError) as error:
                    if not error.retryable:
                        raise
                    if stream.tell() == artifact["size_bytes"]:
                        # Framing/EOF can be lost after the complete immutable
                        # payload arrived. Its digest remains the authority.
                        complete = True
                        break
                    if attempt + 1 == self.max_attempts:
                        raise DownloadError("artifact transfer retries exhausted") from None
                    self._pause(attempt)
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if not complete or os.fstat(stream.fileno()).st_size != artifact["size_bytes"] or digest != artifact["digest"]:
                raise IntegrityError("artifact SHA-256 or size does not match manifest")


def _safe_path(name) -> str:
    if not isinstance(name, str) or not name:
        raise UnsafePathError("invalid artifact relative path")
    try:
        if len(name.encode("utf-8")) > 4096:
            raise UnsafePathError("artifact relative path exceeds byte limit")
    except UnicodeError:
        raise UnsafePathError("artifact path is not valid Unicode") from None
    path = PurePosixPath(name)
    if (path.is_absolute() or str(path) != name or not 1 <= len(path.parts) <= 64
            or any(c in '\\:<>"|?*' or ord(c) < 32 or ord(c) == 127 for c in name)):
        raise UnsafePathError("unsafe artifact relative path")
    for part in path.parts:
        if (part in {".", ".."} or part.rstrip(" .") != part or len(part.encode("utf-8")) > 255
                or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])", part.split(".")[0].rstrip(" "))):
            raise UnsafePathError("unsafe or reserved artifact path component")
    return name


def _is_link(metadata) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _check_location(path: Path):
    for candidate in [path, *path.parents]:
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        if _is_link(metadata):
            raise UnsafePathError("destination or artifact path contains a symlink or reparse point")
        if candidate != path and not stat.S_ISDIR(metadata.st_mode):
            raise UnsafePathError("destination ancestor is not a directory")


def _sync_dir(path: Path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _verify_tree(staging: Path, artifacts: list[dict]):
    expected = {artifact["path"]: artifact for artifact in artifacts}
    found = set()
    for directory, subdirectories, filenames in os.walk(staging, followlinks=False):
        for name in [*subdirectories, *filenames]:
            path = Path(directory) / name
            if _is_link(path.lstat()):
                raise UnsafePathError("staged output contains a symlink or reparse point")
        for name in filenames:
            path = Path(directory) / name
            relative = path.relative_to(staging).as_posix()
            artifact = expected.get(relative)
            if artifact is None:
                raise UnsafePathError("unexpected staged output")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise UnsafePathError("staged output must be a regular, unlinked file")
                if metadata.st_size != artifact["size_bytes"] or hashlib.file_digest(stream, "sha256").hexdigest() != artifact["digest"]:
                    raise IntegrityError("staged output changed before publication")
            found.add(relative)
        _sync_dir(Path(directory))
    if found != set(expected):
        raise IntegrityError("staged output is incomplete")


def _publish_directory(staging: Path, destination: Path):
    """Atomic publication without replacing a concurrently-created directory."""
    if os.name == "nt":
        os.rename(staging, destination)  # Windows rename never replaces a destination.
        return
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        operation = getattr(library, "renameat2", None)
        if operation is None:
            raise DownloadError("atomic no-replace publication is unavailable")
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(-100, os.fsencode(staging), -100, os.fsencode(destination), 1)
    elif sys.platform == "darwin":
        operation = library.renamex_np
        operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(os.fsencode(staging), os.fsencode(destination), 4)
    else:
        raise DownloadError("atomic no-replace publication is unavailable on this platform")
    if result:
        error = ctypes.get_errno()
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(error, os.strerror(error), destination)
        raise OSError(error, os.strerror(error), destination)
