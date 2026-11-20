"""Run a real HTTP smoke test with an isolated SQLite DB and in-memory broker.

No worker is started; this checks API acceptance/compensation, not broker delivery.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="orders-smoke-") as directory:
        env = {
            **os.environ,
            "DATABASE_URL": f"sqlite:///{Path(directory) / 'smoke.db'}",
            "ENVIRONMENT": "test",
            "SECRET_KEY": "smoke-test-secret",
            "CELERY_BROKER_URL": "memory://",
            "CELERY_RESULT_BACKEND": "cache+memory://",
        }
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)  # noqa: S603
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        os.environ.update(env)
        server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get("/api/v1/system/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Local app did not start")

                def call(path, payload=None, headers=None):
                    print(f"POST {path} {json.dumps(payload)}")
                    response = client.post(path, json=payload, headers=headers or {})
                    body = response.json()
                    displayed = {**body, "access_token": "<redacted>"} if "access_token" in body else body
                    print(f"{response.status_code} {json.dumps(displayed, sort_keys=True)}")
                    return response

                credentials = {"email": "smoke@example.com", "password": "smoke-password"}
                signup = call("/api/v1/auth/signup", {**credentials, "full_name": "Smoke User", "role": "admin"})
                assert signup.status_code == 201 and signup.json()["role"] == "customer"
                login = call("/api/v1/auth/login", credentials)
                assert login.status_code == 200
                headers = {
                    "Authorization": f"Bearer {login.json()['access_token']}",
                    "Idempotency-Key": "smoke-key",
                }
                payload = {"currency": "USD", "items": [{"sku": "SKU", "quantity": 1}]}
                first = call("/api/v1/orders", payload, headers)
                replay = call("/api/v1/orders", payload, headers)
                assert first.status_code == replay.status_code == 202
                assert first.json()["id"] == replay.json()["id"]
                conflict = call("/api/v1/orders", {**payload, "items": [{"sku": "SKU", "quantity": 2}]}, headers)
                assert conflict.status_code == 409
                path = f"/api/v1/orders/{first.json()['id']}/cancel"
                for _ in range(2):
                    cancelled = call(path, headers=headers)
                    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
                print("SMOKE PASSED (in-memory broker, no worker; JWT redacted)")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Smoke server did not stop")
            from app.db.session import engine

            engine.dispose()


if __name__ == "__main__":
    main()
