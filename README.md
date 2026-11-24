# Event-Driven Order Orchestration System

[![CI](https://github.com/noohkhan7232/event-driven-order-orchestration-system/actions/workflows/ci.yml/badge.svg)](https://github.com/noohkhan7232/event-driven-order-orchestration-system/actions/workflows/ci.yml)

A Python 3.12/FastAPI backend for independent sellers. Each seller owns a tenant,
catalog, inventory, payment configuration and fulfillment workflow. PostgreSQL is
the source of truth; a transactional outbox feeds RabbitMQ/Celery. Consumers use
durable deduplication and state guards, and compensation releases stock and journals
asynchronous refunds. Provider HTTP, seller webhook HTTP and SMTP run outside
database transactions holding row locks.

Phase 3B implements per-tenant Razorpay integration, mock payments,
COD, expiry/reconciliation, manual shipping, signed seller webhooks, notifications,
revocable public tracking, SSE and owner/platform failed-job recovery APIs.
The Docker acceptance runner below covers all specified Phase 3B experiments.
Live provider money flows, external SMTP/TLS, hosted CI execution and production
deployment remain unverified. See observed results in the
[current architecture and verification report](docs/reports/phase-3b-report.md).

```mermaid
flowchart LR
    Client --> API
    API --> DB[(PostgreSQL: orders + outbox)]
    DB --> Relay
    Relay --> RabbitMQ
    RabbitMQ --> Workers
    Workers --> DB
    Workers --> Providers[Payment / seller HTTP / SMTP]
    DB --> Tracking[Safe tracking + short-poll SSE]
```

## Local quickstart (PowerShell)

Run in the repository root with Docker Desktop and Docker Compose installed.
The default stack reads **`.env.example`** and uses local mock payments,
mock shipping and console notifications. These public demo defaults are for local
development. The migration service upgrades the schema before application services
start; do not run migrations concurrently with writers on an existing installation.

```powershell
# If Windows reserves 8000, select another available host port.
$env:API_PORT='18000'
docker compose up -d --build
$port = if ($env:API_PORT) { $env:API_PORT } else { '8000' }
$base = "http://localhost:$port/api/v1"
Invoke-RestMethod "$base/system/health"
```

Swagger is at `http://localhost:18000/docs` with the override above; otherwise use
port 8000. The live schema is `/api/v1/openapi.json`; the checked-in contract is
[docs/openapi.json](docs/openapi.json). PostgreSQL publishes 5432, Redis 6379,
RabbitMQ 5672 and its local management UI 15672. Choose local port overrides if
those are reserved. The stack includes `api`, `worker`, `integrations-worker`, `scheduler`, `outbox-relay`,
`migrate`, `postgres`, `redis` and `rabbitmq`.

Register a seller, create priced stock, then place an order:

```powershell
$store = Invoke-RestMethod -Method Post "$base/tenants/register" -ContentType 'application/json' -Body '{"business_name":"Example Store","slug":"example-store","owner_email":"owner@example.com","owner_password":"replace-with-local-password","owner_name":"Owner"}'
$login = Invoke-RestMethod -Method Post "$base/auth/login" -ContentType 'application/json' -Body '{"email":"owner@example.com","password":"replace-with-local-password"}'
$headers = @{Authorization="Bearer $($login.access_token)"}
$product = Invoke-RestMethod -Method Post "$base/products" -Headers $headers -ContentType 'application/json' -Body '{"sku":"DEMO","name":"Demo product","price":"19.99","currency":"INR"}'
$warehouse = Invoke-RestMethod -Method Post "$base/inventory/warehouses" -Headers $headers -ContentType 'application/json' -Body '{"code":"MAIN","name":"Main warehouse","region":"local"}'
$stock = @{sku='DEMO'; warehouse_id=$warehouse.id; available_quantity=20} | ConvertTo-Json
$inventory = Invoke-RestMethod -Method Post "$base/inventory" -Headers $headers -ContentType 'application/json' -Body $stock
$integration = Invoke-RestMethod -Method Post "$base/api-keys" -Headers $headers -ContentType 'application/json' -Body '{"name":"Order integration","scopes":["orders:read","orders:write"]}'
$orderHeaders = @{'X-API-Key'=$integration.key; 'Idempotency-Key'='demo-1'}
$order = Invoke-RestMethod -Method Post "$base/orders" -Headers $orderHeaders -ContentType 'application/json' -Body '{"items":[{"sku":"DEMO","quantity":3}],"external_reference":"shop-order-1"}'
```

The response is HTTP 202; prices come from the catalog and total 59.97 INR here.
Retrying the same principal/key/payload returns the same order. Changed intent
conflicts. Read `$base/orders/$($order.id)` with JWT or `orders:read` key credentials
to observe asynchronous progress. Keep the creation-only integration key private.
Buyer fields stay on orders and are excluded from emitted business events.

Owners are tenant ADMIN JWT users. Vendors are staff; customers see their own
orders; integration keys have explicit scopes. Customer signup requires
`tenant_slug`. Foreign owned IDs return 404. Platform administrators have no tenant
and cannot use tenant-owner routes. Tenant suspension blocks new authenticated
requests; public tracking returns 404 and active streams stop on a subsequent poll.
Demo data is optional: `docker compose exec -T api python -m scripts.seed_demo`.
That script contains public local fixture passwords and may print a newly created
integration key once; do not use those accounts outside local development.

## Private configuration

Copy `.env.example` to the ignored `.env` only if `.env` does not already exist,
then edit it privately. **Copying alone does not replace Compose's `env_file`.**
For private settings, save this as ignored `.env.compose.yml`:

```yaml
x-private: &private
  env_file: [.env]
services:
  api: *private
  worker: *private
  integrations-worker: *private
  scheduler: *private
  outbox-relay: *private
  migrate: *private
```

Start/recreate with `docker compose -f docker-compose.yml -f .env.compose.yml up -d --build`.
Subsequent commands must retain both files. Later env files override earlier ones;
explicit Compose `environment` entries still take precedence. This follows the
[Compose environment rules](https://docs.docker.com/compose/how-tos/environment-variables/envvars-precedence/).
Base Compose explicitly interpolates notification backend and failure injection
from the shell/`.env`; it does not forward arbitrary shell variables automatically.

Set a private strong `SECRET_KEY`, `TRACKING_TOKEN_SECRET` and Fernet
`CREDENTIALS_ENCRYPTION_KEY`; never commit generated values. Set both
`PUBLIC_BASE_URL` and `PUBLIC_TRACKING_BASE_URL` to your reachable origin, including
the chosen API host port. Returned webhook/tracking URLs otherwise use port 8000.
Keep all application processes on the same key ring and tracking secret.
Outside local/dev/test, validators require strong keys, encryption material,
manual shipping, no mock auto-completion/failure injection, and SMTP or an explicit
console opt-out. The legacy provider selector also requires explicit
`ALLOW_MOCK_PROVIDERS=true`; real tenant configuration still takes precedence for
prepaid payments. Non-local prepaid intake still requires an active tenant Razorpay
configuration. This development Compose stack is not a production deployment.

`RATE_LIMIT_REDIS_URL` uses Redis DB2 in Compose. Request quotas use atomic fixed
windows; login/register/signup/payment callbacks/public tracking fail closed if
protection is unavailable. Authenticated request quotas default to fail open.
SSE connection caps always fail closed; non-local streams require a working shared
Redis backend. Local memory quotas/caps are per process. Default public tracking
and stream opens share 60 requests/min/IP; active streams additionally have
10/tenant and 5/IP caps. `TRUSTED_PROXY_COUNT=0` ignores XFF; only change it behind
a proxy that sanitizes forwarding headers and prevents direct access.

## Connect Razorpay (test mode)

1. Obtain test-mode API keys from your Razorpay account. Configure private Fernet
   material and a reachable HTTPS `PUBLIC_BASE_URL` in the application processes.
2. As the seller owner, `PUT /api/v1/settings/payments/razorpay` with
   `{key_id, key_secret, webhook_secret, mode:"test"}`. The API stores ciphertext
   and returns a masked key ID plus its public `webhook_url`. Keys/vendors/customers
   cannot manage these settings. GET is masked; DELETE deactivates.
3. Register that returned URL in Razorpay using the same webhook secret. Subscribe
   to `payment.captured`, `payment.failed`, `refund.processed` and `refund.failed`;
   `order.paid` is also supported, and duplicate charge notices are harmless.
4. Use INR catalog products. A pending prepaid order returns checkout data with
   provider order ID, public key ID, integer paise amount and expiry. There is no
   checkout frontend in this repository. A verified capture drives fulfillment;
   a client success redirect alone does not mark the order paid.
5. The callback verifies raw-body HMAC before parsing and persists a receipt plus
   outbox intent before acknowledging. Expiry uses the payment window plus capture
   grace; late/mismatched/duplicate charges journal asynchronous refunds.
   Reconciliation polls unresolved payments and processing refunds outside locks.

Contracts are checked against [Razorpay webhook validation](https://razorpay.com/docs/webhooks/validate-test/),
[payment events](https://razorpay.com/docs/webhooks/payments/) and
[refund events](https://razorpay.com/docs/webhooks/refunds/). Live keys, account
entitlements, settlement and remote guarantees are not verified by local tests.
`X-Razorpay-Signature` signs the original bytes with the **webhook secret**, not the
API key secret. `x-razorpay-event-id` supports receipt deduplication. Do not parse
and reserialize before verification. Keep signing-secret overlap when provider
retries span a secret change; automatic old-secret history is not implemented.

Python receiver verification:

```python
import hashlib
import hmac
import re


def verify_razorpay(raw: bytes, signature: str, webhook_secret: str) -> bool:
    expected = hmac.new(webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    return bool(re.fullmatch(r"[0-9a-f]{64}", signature)) and hmac.compare_digest(expected, signature)
```

Node receiver verification:

```javascript
import {createHmac, timingSafeEqual} from 'node:crypto';
function verifyRazorpay(raw, signature, webhookSecret) {
  if (!/^[0-9a-f]{64}$/.test(signature)) return false;
  const expected = createHmac('sha256', webhookSecret).update(raw).digest();
  return timingSafeEqual(expected, Buffer.from(signature, 'hex'));
}
```

## Seller webhooks

Owners create `POST /api/v1/webhook-endpoints` with `{url, events, description?}`.
Creation/`POST /{id}/rotate-secret` shows `signing_secret` once; lists never show it.
Subscribe to `order.created`, `order.status_changed`, `order.paid`, `order.shipped`,
`order.delivered`, `order.failed`, `order.cancelled`, `payment.refunded` or `ping`.
`POST /{id}/test` queues ping. List deliveries at `GET /{id}/deliveries`;
`POST /{id}/deliveries/{delivery_id}/replay` requeues a dead delivery with the same
event ID. Re-enable auto-disabled endpoints through PATCH before replay.

Delivery is at least once and ordering is not promised. Persistently deduplicate
envelope `id` before applying effects; return 2xx only after durable acceptance.
Verify the **raw bytes** using `X-OOS-Signature: t=<unix>,v1=<hex>` and
`X-OOS-Timestamp`. The signed message is `t + '.' + body`, using the endpoint's
`whsec_...` secret. Timestamp/signature refresh on each attempt. Neither receiver
example logs secrets or bodies; deserialize only after successful verification.

```python
import time


def verify_oos(raw: bytes, headers, secret: str) -> bool:
    try:
        parts = dict(piece.split("=", 1) for piece in headers["X-OOS-Signature"].split(","))
        stamp, supplied = parts["t"], parts["v1"]
        if not re.fullmatch(r"[0-9]+", stamp) or stamp != headers["X-OOS-Timestamp"]:
            return False
        if abs(time.time() - int(stamp)) > 300 or not re.fullmatch(r"[0-9a-f]{64}", supplied):
            return False
        expected = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, supplied)
    except (KeyError, TypeError, ValueError):
        return False
```

```javascript
function verifyOos(raw, headers, secret) {
  const parts = Object.fromEntries((headers['x-oos-signature'] ?? '').split(',').map(x => x.split('=')));
  const stamp = parts.t;
  if (!/^[0-9]+$/.test(stamp ?? '') || stamp !== headers['x-oos-timestamp'] ||
      !Number.isSafeInteger(Number(stamp)) || Math.abs(Date.now()/1000 - Number(stamp)) > 300 ||
      !/^[0-9a-f]{64}$/.test(parts.v1 ?? '')) return false;
  const expected = createHmac('sha256', secret).update(stamp + '.').update(raw).digest();
  return timingSafeEqual(expected, Buffer.from(parts.v1, 'hex'));
}
```

The Python blocks share imports, as do the Node blocks. Default retries use
exponential backoff with jitter, eight attempts over roughly 24h, then `dead`;
20 consecutive failed attempts auto-disable an endpoint. Redirects are not followed.
Strict SSRF rules require HTTPS, allowed ports and exclusively public DNS answers;
delivery pins a validated IP while retaining original Host/TLS SNI. Private HTTP
targets require an explicit local-only override and port allowlist. Do not put
buyer details in merchant references, item names or tracking fields: those free
text fields are not classified by a DLP system.

## Manual shipping, email and development tools

Owners select `{"shipping_provider":"manual"}` through
`PATCH /api/v1/settings/shipping`. Staff and `orders:write` keys set carrier,
tracking number, HTTPS tracking URL and estimated date through
`PATCH /api/v1/shipments/{id}`. Submit `{"status":"in_transit"}` then
`{"status":"delivered"}` to `POST /api/v1/shipments/{id}/events`.
Forward guards prevent stale observations moving an order backwards. Delivered
COD orders collect the pending payment once. Cancellation atomically cancels a
fulfillment shipment, releases stock and journals an owed prepaid refund.
Courier aggregators, returns and RTO remain deferred.

Notifications journal once per order/channel/template. Recipients are read at send
time, masked in the journal and never placed in events. Owner JWTs use
`GET/PATCH /api/v1/settings/notifications` with `{"notifications_enabled":false}`
to opt out. Templates cover confirmation, shipping, delivery, failure, cancellation
and completed refunds. SMTP calls run after session closure. Known transient
rejections retry; permanent failures do not. An uncertain post-DATA/crash outcome
is failed without automatic resend, avoiding possible duplicates but requiring
operator investigation and potentially leaving a missed email.

```powershell
$env:NOTIFICATIONS_BACKEND='smtp'
docker compose --profile dev-tools up -d
# With private configuration, retain -f docker-compose.yml -f .env.compose.yml.
```

Mailpit receives SMTP on `mailpit:1025`; its loopback UI is
`http://localhost:8025`. The webhook sink listens inside Compose at
`http://webhook-sink:8080`, with loopback host access on `http://localhost:8090`.
Host ports use `MAILPIT_UI_PORT`, `MAILPIT_SMTP_PORT` and `WEBHOOK_SINK_PORT`.
Sink `GET /received` lists bounded in-memory requests; `POST /config` accepts
`{"failures":2,"clear":true}`. To register it as an application webhook target,
privately set `WEBHOOK_ALLOW_INSECURE_TARGETS=true` and
`WEBHOOK_EXTRA_ALLOWED_PORTS=[8080]`, then recreate with the private override.
Validators forbid that insecure flag outside local/dev/test. The sink/profile is
for development; restart discards its records. SMTP defaults have no TLS locally;
configure STARTTLS/auth and private credentials for a real relay.

## Public tracking and SSE

Authenticated order responses include `tracking_token` and `tracking_url`.
`GET /api/v1/public/track/{token}` exposes friendly state, timeline, item names and
quantities and shipment tracking. Buyer data, internal reasons and prices are
excluded; `PUBLIC_TRACKING_SHOW_TOTAL=true` opts into total/currency only.
Staff/`orders:write` keys can `POST /orders/{id}/tracking/rotate`; old links then
return the same generic 404 as invalid/unknown/suspended-store links.
Treat links as bearer credentials. Tracking responses use no-store/no-referrer;
application, Uvicorn and HTTPX access logs redact their paths.

SSE endpoints:

- `GET /api/v1/orders/stream?order_id=<optional UUID>`: staff/read keys see their
  tenant; customers see only their own orders.
- `GET /api/v1/public/track/{token}/stream`: one order, friendly states only.

Frames contain `id: <order_timeline.id>`, `event: order.status_changed` and JSON
`{order_id, from, to, at}`. Free-text reasons are excluded. New connections start
at now; reconnect with `Last-Event-ID` or `?last_event_id=` to resume. The header
takes precedence. Heartbeats default to15s; connections close after300s or the
bounded event budget. Reconnect with the last processed ID and deduplicate IDs.
Short poll sessions close before waits/yields; Redis pub/sub is not required.

The default5s holdback and recent lookback handle bounded serial-ID commit gaps;
buffered events cannot overtake unread replay batches. Transactions delayed beyond
the configured window are not an arbitrary-duration lossless-resume guarantee.
Refresh the authoritative order snapshot after reconnect or a prolonged outage.
Suspension, key revocation/expiry, role changes and token rotation are checked on
polls. Already accepted reads may finish. Slow database calls can delay teardown.
Set proxy idle timeouts above heartbeat and disable proxy response buffering.

Browser `EventSource` cannot set Authorization/X-API-Key headers. Use a fetch-based
client for authenticated streams (the Phase4 dashboard is not implemented):

```javascript
async function readOrders(jwt, after, onEvent, signal) {
  const headers = {Authorization: `Bearer ${jwt}`};
  if (after != null) headers['Last-Event-ID'] = String(after);
  const response = await fetch('/api/v1/orders/stream', {headers, signal});
  if (!response.ok) throw Error(`Stream HTTP ${response.status}`);
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let pending = '';
  try {
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      pending += decoder.decode(value, {stream:true});
      let boundary;
      while ((boundary = pending.indexOf('\n\n')) >= 0) {
        const frame = pending.slice(0, boundary); pending = pending.slice(boundary + 2);
        const lines = frame.split('\n'), id = lines.find(x => x.startsWith('id: '))?.slice(4);
        const data = lines.filter(x => x.startsWith('data: ')).map(x => x.slice(6)).join('\n');
        if (id && data) { await onEvent(id, JSON.parse(data)); after = id; }
      }
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  return after;
}
```

Persist the cursor only after applying an event; reconnect after normal closure
and respect Retry-After on429/503. Public clients use the tracking stream URL and
omit authentication headers. Database timeline rows are the durable event source.

## Failed-job HTTP recovery

| Method and path under `/api/v1` | Credential |
|---|---|
|GET `/failed-jobs` (`status`, `task`, `limit`, `offset`)|Tenant owner JWT|
|GET `/failed-jobs/{id}`|Tenant owner JWT; foreign404|
|POST `/failed-jobs/{id}/replay`|Tenant owner JWT;202 durable requeue|
|POST `/failed-jobs/{id}/discard`|Tenant owner JWT;204|
|GET `/admin/failed-jobs` (same filters plus `tenant_id`)|Platform-admin JWT|
|POST `/admin/failed-jobs/{id}/replay`|Platform-admin JWT|
|POST `/admin/failed-jobs/{id}/discard`|Platform-admin JWT|

List/detail omit event payloads and arbitrary diagnostics. Known safe error codes
are bounded to160 characters; other errors are replaced with a generic message.
Unknown task/queue labels are masked. Keys/vendors/customers cannot recover jobs.
Replay/discard use the existing order-before-job locking and write an actor audit
in the same transaction. Concurrent replay has one winner. A second replay returns
409 `job_already_replayed`; business/terminal/discarded jobs return
`order_not_retryable`, owed refunds `refund_pending`. Only compensated technical
`FAILED/RETRIES_EXHAUSTED` orders reopen with a fresh event; nonterminal recovery
preserves the original identity. Invalid poison payloads are safe conflicts.

## Development and quality checks

Outbound HTTP uses a cancellable total deadline (`WEBHOOK_TOTAL_TIMEOUT_SECONDS=15`)
across connect/TLS/write/response headers/body; the connect/read timeout remains5s.
DNS has its own hard caller deadline (`WEBHOOK_DNS_TIMEOUT_SECONDS=3`), capped by
the remaining total send budget. At most8 daemon lookups can outlive their callers
per process; saturation and timeout fail with the generic unsafe-target error.
Both NAT64 ranges (`64:ff9b::/96`, `64:ff9b:1::/48`), 6to4 (`2002::/16`) and Teredo
(`2001::/32`) are rejected entirely, including public embedded IPv4 and local override.
`WEBHOOK_BATCH_BUDGET_SECONDS=45` stops new claims, allowing the current send to finish.
Both poller tasks have75s soft/90s hard Celery limits. `integrations-worker` alone
consumes `integrations`, with `INTEGRATIONS_WORKER_CONCURRENCY=2`; the saga worker
keeps its order/inventory/payment/shipment/dead-letter queues. Beat routes explicitly.
Per-task overlap guards use nonblocking Redis locks when `REDIS_URL` is nonempty,
with105s TTL beyond the hard limit and owner-checked release. With empty `REDIS_URL`,
PostgreSQL session advisory locks run on an autocommit connection without row locks;
SQLite uses a process lock for local tests. Failed Redis admission claims no work.
The integrations service has100s shutdown grace. For focused real Docker verification:
`python scripts/e2e_integrations.py --section hardening` runs E1/E8/E22–E25/E27 and
restores all four application services' runtime settings.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
.venv\Scripts\python -m mypy app
# Use a clean local test profile; avoid container-only hostname settings from .env.
$env:ENVIRONMENT='test'
$env:SHIPPING_PROVIDER='mock'
$env:PAYMENT_PROVIDER='mock'
$env:NOTIFICATIONS_BACKEND='console'
$env:RATE_LIMIT_REDIS_URL=''
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:TEST_REDIS_URL -ErrorAction SilentlyContinue
$env:COVERAGE_FILE='.coverage.review.sqlite'
.venv\Scripts\python -m pytest -q --cov=app --cov-report=term-missing --cov-report=json:.coverage.review.sqlite.json --cov-fail-under=90
.venv\Scripts\python scripts/check_coverage.py .coverage.review.sqlite.json
```

Use `.venv/bin/python` on Unix. For the real PostgreSQL/Redis layer, start those
services, create a dedicated disposable database named `orders_test` if needed,
set the variables below, then run the same checks with a separate coverage file:

```powershell
$env:TEST_DATABASE_URL='postgresql+psycopg://orders:orders@localhost:5432/orders_test'
$env:TEST_REDIS_URL='redis://localhost:6379/15'
$env:COVERAGE_FILE='.coverage.review.postgres'
.venv\Scripts\python -m pytest -q --cov=app --cov-report=term-missing --cov-report=json:.coverage.review.postgres.json --cov-fail-under=90
.venv\Scripts\python scripts/check_coverage.py .coverage.review.postgres.json
.venv\Scripts\python scripts/export_openapi.py --check
# After an intentional API change:
.venv\Scripts\python scripts/export_openapi.py
```

Tests drop/recreate their test schema: PostgreSQL names must end in `_test`.
Never point at the application database or overlap runs on one test database.
Tests never call real external providers; PostgreSQL/Redis tests use local test
services. Coverage omits tests and enforces≥90% overall and per required new module.
CI has SQLite and PostgreSQL+Redis jobs; this is configuration, not a claim that
hosted CI ran. The OpenAPI freshness test compares the artifact with current routes.

The complete Docker acceptance entrypoint runs E1–E20 and E22–E30, plus retained
reliability controls. E21 is not specified in the Phase 3B acceptance list. It uses
the real API, PostgreSQL, RabbitMQ, Redis, worker, scheduler, relay, Mailpit and sink;
remote payment-provider sessions are synthetic. It recreates services with proof
settings, uses isolated quota counters, then verifies restored application defaults.
Unique order/financial/audit fixtures and existing volumes remain. Temporary
encrypted webhook endpoint/delivery fixtures are removed before retiring their key.
The temporary platform principal uses a random credential and is disabled on cleanup;
its audit identity remains intact.

```powershell
docker compose build
# If Windows blocks the default host SMTP port1025, select an available host port:
$env:MAILPIT_SMTP_PORT='11025'
.venv\Scripts\python scripts/e2e_integrations.py
# Optional diagnostic rerun; use the complete entrypoint for acceptance:
.venv\Scripts\python scripts/e2e_integrations.py --section sellers --seller-case E27
```

Only one acceptance runner should reconfigure the stack at a time. Container SMTP
stays `mailpit:1025`. If changing dev-tools UI ports, set `E2E_MAILPIT_URL` and
`E2E_SINK_URL` to the matching host URLs; API requests use `API_PORT` or
`E2E_BASE_URL`. See the report for the legacy-data roundtrip command and raw output.

## Limits and references

Mock lookup maps and local memory quotas are process-local. Webhook delivery is
at least once; SMTP has uncertain-outcome limits. No live payment/settlement,
production HA/soak, PostgreSQL RLS, frontend, real courier aggregation, returns/RTO,
WhatsApp/SMS, GST invoicing or hosting is claimed. See
[Phase3B report](docs/reports/phase-3b-report.md) for configuration, migrations,
security boundaries, proof outputs and the current NOT VERIFIED list.

MIT licensed: [LICENSE](LICENSE). Author: **NOOH KHAN** —
[GitHub](https://github.com/noohkhan7232).
