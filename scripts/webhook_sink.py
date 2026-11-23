"""Development-only bounded in-memory receiver. Never logs request bodies."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock


class Sink:
    def __init__(self):
        self.lock = Lock()
        self.failures = 0
        self.received: list[dict] = []

    def receive(self, raw: bytes, headers: dict) -> int:
        with self.lock:
            failed = self.failures > 0
            self.failures = max(0, self.failures - 1)
            self.received.append(
                {"body": raw.decode("utf-8", errors="replace"), "headers": headers, "status": 503 if failed else 204}
            )
            self.received = self.received[-1000:]
            return 503 if failed else 204


state = Sink()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def answer(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path != "/received":
            return self.answer(404, {})
        with state.lock:
            rows = list(state.received)
        self.answer(200, {"received": rows})

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 65536:
                return self.answer(413, {})
            raw = self.rfile.read(size)
            if self.path == "/config":
                payload = json.loads(raw)
                failures = payload.get("failures", 0)
                if type(failures) is not int or not 0 <= failures <= 10000:
                    raise ValueError
                with state.lock:
                    state.failures = failures
                    if payload.get("clear", False):
                        state.received.clear()
                return self.answer(200, {"failures": failures})
            self.answer(state.receive(raw, dict(self.headers)), {})
        except (ValueError, TypeError, AttributeError):
            self.answer(400, {})


if __name__ == "__main__":
    # Container-only bind; Compose publishes this dev service on loopback only.
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()  # noqa: S104
