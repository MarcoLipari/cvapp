"""Authenticated loopback handoff for a future Safari Web Extension.

The bridge deliberately binds only to 127.0.0.1 and requires an unpredictable
token for every request. It accepts a small JSON job-application payload and
passes the validated values to the desktop app callback.
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from database import STATUSES


class CaptureBridge:
    def __init__(self, on_capture: Callable[[dict], None]):
        self._on_capture = on_capture
        self.token = secrets.token_urlsafe(32)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def endpoint(self) -> str | None:
        return f"http://127.0.0.1:{self._server.server_port}/v1/applications" if self._server else None

    def start(self) -> str:
        if self._server:
            return self.endpoint or ""
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def _send(self, status: int, body: dict | None = None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-CV-Manager-Token")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.end_headers()
                if body is not None:
                    self.wfile.write(json.dumps(body).encode("utf-8"))

            def do_OPTIONS(self) -> None:
                self._send(204)

            def do_POST(self) -> None:
                if self.path != "/v1/applications":
                    self._send(404, {"error": "not found"}); return
                if not secrets.compare_digest(self.headers.get("X-CV-Manager-Token", ""), bridge.token):
                    self._send(401, {"error": "unauthorized"}); return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 100_000:
                        raise ValueError("invalid request size")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    values = bridge._validate(payload)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                    self._send(400, {"error": str(error)}); return
                bridge._on_capture(values)
                self._send(201, {"status": "captured"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="cv-manager-capture", daemon=True)
        self._thread.start()
        return self.endpoint or ""

    def stop(self) -> None:
        if not self._server:
            return
        self._server.shutdown(); self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)
        self._server = None; self._thread = None

    @staticmethod
    def _validate(payload: object) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        company = str(payload.get("company", "")).strip()
        role = str(payload.get("role", "")).strip()
        if not company or not role:
            raise ValueError("company and role are required")
        status = str(payload.get("status", "Applied"))
        if status not in STATUSES:
            raise ValueError("unknown status")
        return {
            "company": company,
            "role": role,
            "location": str(payload.get("location", "")).strip(),
            "posting_url": str(payload.get("posting_url", "")).strip(),
            "application_date": str(payload.get("application_date", date.today().isoformat())).strip() or date.today().isoformat(),
            "status": status,
            "cv_id": None,
            "notes": str(payload.get("notes", "")).strip(),
        }
