import socket
import socketserver
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID

import pytest
from conftest import TestingSessionLocal
from pydantic import ValidationError
from test_outbound_webhooks import seller_endpoint as seller_endpoint
from test_payment_config import payment_settings as payment_settings
from test_webhook_delivery import ping

from app.auth.principal import aware
from app.core.config import Settings
from app.models.entities import WebhookDelivery, WebhookEndpoint
from app.services import webhook_delivery
from app.services.payment_timing import database_now


@contextmanager
def local_receiver(mode):
    stop = threading.Event()
    connections = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            connections.append(self.request)
            self.request.settimeout(2)
            try:
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = self.request.recv(65536)
                    if not chunk:
                        return
                    request += chunk
                head, body = request.split(b"\r\n\r\n", 1)
                size = next(
                    (
                        int(line.split(b":", 1)[1])
                        for line in head.split(b"\r\n")
                        if line.lower().startswith(b"content-length:")
                    ),
                    0,
                )
                while len(body) < size:
                    chunk = self.request.recv(65536)
                    if not chunk:
                        return
                    body += chunk
                if mode == "ok":
                    self.request.sendall(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                elif mode == "silent":
                    stop.wait(30)
                else:
                    wire = b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n" if mode == "headers" else b"x" * 100
                    if mode == "body":
                        self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n")
                    for byte in wire:
                        self.request.sendall(bytes([byte]))
                        if stop.wait(2):
                            return
            except OSError:
                pass

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/hook", server.server_address[1]
    finally:
        stop.set()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("mode", ["headers", "body", "silent"])
def test_total_deadline_aborts_local_peer_and_records_transport_failure(db, client, seller_endpoint, monkeypatch, mode):
    _, auth, endpoint, settings = seller_endpoint
    identity = ping(client, auth, endpoint)
    durations, results, errors = [], [], []
    complete = threading.Event()
    original = webhook_delivery.send_webhook

    def timed_send(*args):
        started = time.monotonic()
        try:
            return original(*args)
        finally:
            durations.append(time.monotonic() - started)

    monkeypatch.setattr(webhook_delivery, "send_webhook", timed_send)
    with local_receiver(mode) as (url, port):
        settings = settings.model_copy(
            update={
                "webhook_timeout_seconds": 2.5,
                "webhook_total_timeout_seconds": 3.0,
                "webhook_allow_insecure_targets": True,
                "webhook_extra_allowed_ports": [port],
                "webhook_delivery_batch_size": 1,
            }
        )
        db.get(WebhookEndpoint, UUID(endpoint["id"])).url = url
        db.commit()

        def deliver():
            try:
                results.append(webhook_delivery.run_delivery_batch(TestingSessionLocal, settings))
            except BaseException as error:
                errors.append(error)
            finally:
                complete.set()

        sender = threading.Thread(target=deliver, daemon=True)
        sender.start()
        completed = complete.wait(6)
    sender.join(5)
    assert completed, "Slow-drip peer exceeded the total outbound deadline"
    assert not errors and results == [{"failed": 1}]
    assert durations and durations[0] <= 5
    if mode != "silent":
        assert durations[0] >= 2.75
    db.expire_all()
    row = db.get(WebhookDelivery, identity)
    assert row.status == "failed" and row.last_error == "transport_error" and row.last_status_code is None
    assert row.attempts == 1 and row.claim_token is None and row.lease_until is None
    assert aware(row.next_attempt_at) > database_now(db)
    print(f"TOTAL DEADLINE {mode}: {durations[0]:.2f}s; failed/transport_error; attempt1; retry retained")


@pytest.mark.parametrize(
    "values",
    [
        {"webhook_total_timeout_seconds": 5},
        {"webhook_total_timeout_seconds": 60},
        {"webhook_total_timeout_seconds": 0},
        {"webhook_batch_budget_seconds": 0},
        {"webhook_batch_budget_seconds": 60},
    ],
)
def test_webhook_deadline_budget_and_lease_configuration(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_batch_budget_stops_before_claiming_the_next_delivery(db, client, seller_endpoint, monkeypatch):
    _, auth, endpoint, settings = seller_endpoint
    first, second = ping(client, auth, endpoint), ping(client, auth, endpoint)
    now_in_db = database_now(db)
    db.get(WebhookDelivery, first).next_attempt_at = now_in_db - timedelta(seconds=2)
    db.get(WebhookDelivery, second).next_attempt_at = now_in_db - timedelta(seconds=1)
    db.commit()
    now = [100.0]
    monkeypatch.setattr(webhook_delivery.time, "monotonic", lambda: now[0])

    def send(*args):
        now[0] += 46
        return 204

    monkeypatch.setattr(webhook_delivery, "send_webhook", send)
    assert webhook_delivery.run_delivery_batch(TestingSessionLocal, settings) == {"delivered": 1}
    db.expire_all()
    assert db.get(WebhookDelivery, first).status == "delivered"
    assert db.get(WebhookDelivery, second).attempts == 0


def test_stalled_endpoint_does_not_block_another_endpoint_in_next_run(db, client, seller_endpoint):
    _, auth, endpoint, settings = seller_endpoint
    with local_receiver("silent") as (slow, slow_port), local_receiver("ok") as (fast, fast_port):
        settings.webhook_allow_insecure_targets = True
        settings.webhook_extra_allowed_ports = [slow_port, fast_port]
        db.get(WebhookEndpoint, UUID(endpoint["id"])).url = slow
        db.commit()
        first = ping(client, auth, endpoint)
        other = client.post("/api/v1/webhook-endpoints", headers=auth, json={"url": fast, "events": ["ping"]})
        assert other.status_code == 201
        second = ping(client, auth, other.json())
        now_in_db = database_now(db)
        db.get(WebhookDelivery, first).next_attempt_at = now_in_db - timedelta(seconds=2)
        db.get(WebhookDelivery, second).next_attempt_at = now_in_db - timedelta(seconds=1)
        db.commit()
        bounded = settings.model_copy(
            update={
                "webhook_timeout_seconds": 0.2,
                "webhook_total_timeout_seconds": 0.4,
                "webhook_batch_budget_seconds": 0.05,
            }
        )
        assert webhook_delivery.run_delivery_batch(TestingSessionLocal, bounded) == {"failed": 1}
        db.expire_all()
        assert db.get(WebhookDelivery, first).last_error == "transport_error"
        assert db.get(WebhookDelivery, second).attempts == 0
        db.commit()
        assert webhook_delivery.run_delivery_batch(TestingSessionLocal, bounded) == {"delivered": 1}
        db.expire_all()
        assert db.get(WebhookDelivery, second).status == "delivered"
