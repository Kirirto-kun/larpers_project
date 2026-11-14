"""Real HTTP boundary fixtures; the SDK itself uses no test doubles."""
from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@dataclass
class Request:
    method: str
    path: str
    query: dict
    headers: dict
    body: object


@dataclass
class Response:
    status: int = 200
    body: object = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    truncate: int | None = None
    disconnect: bool = False
    omit_length: bool = False


class APIServer:
    def __init__(self):
        self.routes = {}
        self.requests = []
        self._errors = []
        self._closed = False
        owner = self

        class BoundaryHTTPServer(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                error = sys.exc_info()[1]
                if isinstance(error, (BrokenPipeError, ConnectionResetError)):
                    return
                # Unexpected fixture errors, especially handler assertions,
                # must fail teardown rather than looking like retryable EOFs.
                if error is not None:
                    owner._errors.append(error)
                super().handle_error(request, client_address)

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                parts = urlsplit(self.path)
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                request = Request(
                    self.command, parts.path, parse_qs(parts.query), dict(self.headers),
                    json.loads(raw) if raw else None,
                )
                owner.requests.append(request)
                route = owner.routes.get((request.method, request.path))
                response = route(request) if callable(route) else route
                response = response or Response(404, {"code": "NOT_FOUND"})
                if response.disconnect:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                body = response.body if isinstance(response.body, bytes) else json.dumps(response.body).encode()
                self.send_response(response.status)
                if not response.omit_length:
                    self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Type", "application/json" if not isinstance(response.body, bytes) else "application/octet-stream")
                for name, value in response.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                try:
                    self.wfile.write(body if response.truncate is None else body[:response.truncate])
                    self.wfile.flush()
                    if response.truncate is not None:
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.httpd = BoundaryHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.respond("GET", "/protocol", {"api_version": "1", "min_runner_version": "0.1.0", "max_runner_version": "0.1.99"})

    def respond(self, method, path, body=None, *, status=200, headers=None):
        self.routes[(method, "/api/v1" + path)] = Response(status, {} if body is None else body, headers or {})

    def handle(self, method, path, callback):
        self.routes[(method, "/api/v1" + path)] = callback

    def seen(self, method, path):
        return [request for request in self.requests if request.method == method and request.path == "/api/v1" + path]

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        if self._errors:
            raise ExceptionGroup("HTTP fixture handler failed", self._errors)


@pytest.fixture
def api():
    server = APIServer()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def new_client():
    import campus

    def create(api, **options):
        client_type = getattr(campus, "Client", None)
        assert client_type is not None, "campus.Client must expose the submission SDK"
        return client_type(api.url, "test-token", backoff=0, poll_interval=0, **options)

    return create
