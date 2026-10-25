"""Thin HTTP wrapper (stdlib only) exposing the OpenAI-compatible surface:
  GET  /v1/models
  POST /v1/chat/completions   (streaming not supported; stream=true is ignored)
  POST /v1/embeddings
  (answers charging-cost questions from the ledger; read-only)
  GET  /health
Optional bearer auth: set TCL_SERVER_API_KEY and clients must send
Authorization: Bearer <key> — same shape as a real OpenAI client config.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .service import ChargeLedgerService


def _error(status: int, message: str, err_type: str = "invalid_request_error") -> tuple[int, dict]:
    return status, {"error": {"message": message, "type": err_type, "code": None}}


def make_handler(service: ChargeLedgerService):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet by default
            pass

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            key = service.settings.server_api_key
            if not key:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {key}"

        def _read_json(self) -> dict | None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                return json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return None

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"status": "ok"})
            if self.path.startswith("/v1/models"):
                if not self._authorized():
                    return self._send(*_error(401, "Invalid API key", "authentication_error"))
                return self._send(200, service.models_list())
            return self._send(*_error(404, f"Unknown path {self.path}"))

        def do_POST(self):
            if not self.path.startswith("/v1/"):
                return self._send(*_error(404, f"Unknown path {self.path}"))
            if not self._authorized():
                return self._send(*_error(401, "Invalid API key", "authentication_error"))
            body = self._read_json()
            if body is None:
                return self._send(*_error(400, "Request body must be JSON"))
            try:
                if self.path.startswith("/v1/chat/completions"):
                    return self._send(200, service.chat_completions(body))
                if self.path.startswith("/v1/embeddings"):
                    return self._send(200, service.embeddings(body))
            except Exception as exc:  # never leak a stack trace to clients
                return self._send(*_error(500, str(exc)[:200], "server_error"))
            return self._send(*_error(404, f"Unknown path {self.path}"))

    return Handler


def serve(service: ChargeLedgerService) -> None:
    handler = make_handler(service)
    httpd = ThreadingHTTPServer((service.settings.server_host,
                                 service.settings.server_port), handler)
    print(f"tesla-charge-ledger listening on http://{service.settings.server_host}:"
          f"{service.settings.server_port}  (model id: {service.settings.model_id})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
