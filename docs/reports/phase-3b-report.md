# Phase 3B — complete implementation and local verification

24 November 2026, Asia/Calcutta. Branch: `phase-3b-integrations`.
Tasks 3B.1–3B.13 and 3B.P1 are DONE. This session completes only 3B.13;
previously reviewed implementations are retained. Live-service limits are in B13.
This document describes the current system and final evidence only.

# PART A — Architecture and operations

## A1. Async payment saga

Intake snapshots catalog prices and commits an owned order with an outbox intent.
Prepaid keeps the legacy fingerprint; explicitly non-default COD participates in
the hash. Replays return the existing order without another reservation/session.
Every transition commits audit, owned timeline and status intent with business state.

```mermaid
stateDiagram-v2
    CREATED --> INVENTORY_RESERVED: reserve
    INVENTORY_RESERVED --> PAYMENT_PENDING: prepaid session
    INVENTORY_RESERVED --> FULFILLMENT_REQUESTED: COD
    PAYMENT_PENDING --> PAID: matching capture before deadline
    PAID --> FULFILLMENT_REQUESTED: shipment
    FULFILLMENT_REQUESTED --> SHIPPED: in transit
    SHIPPED --> DELIVERED: delivery
    PAYMENT_PENDING --> FAILED: decline or expiry
    PAYMENT_PENDING --> CANCELLED: cancel and release
    PAID --> CANCELLED: release and refund
    FULFILLMENT_REQUESTED --> CANCELLED: shipment cancel and refund
    PAYMENT_PENDING --> PAYMENT_PENDING: mismatch refund
    FAILED --> FAILED: late capture refund
    CANCELLED --> CANCELLED: late capture refund
```

Session creation commits a numbered attempt, pending state and expiry; calls the
detached provider; then locks/rechecks before storing checkout and its marker.
Retries keep the UUID/key/expiry. Technical recovery creates the next numbered
attempt. Cancel/expiry during I/O prevents storing an ineligible session result.

```mermaid
sequenceDiagram
    participant W as Worker
    participant DB as Database
    participant P as Provider
    participant API as Callback API
    W->>DB: Lock order, create attempt/pending/expiry; COMMIT
    W->>P: Create session from immutable snapshot
    P-->>W: Reference and safe checkout
    W->>DB: Lock/recheck, store session/marker; COMMIT
    P->>API: Raw signed capture with event ID
    API->>DB: Verify/cap/normalize; receipt + outbox; COMMIT
    API-->>P: 200
    W->>DB: Consume receipt; capture + fulfillment intent; COMMIT
```

Deadline = payment_expires_at + PAYMENT_CAPTURE_GRACE_SECONDS. Only a pending
order can use grace: with the default 120, 119 seconds late is accepted; exactly
120 and beyond are late. Amount/currency must match. Normal captures retain the
native AUTHORIZED label with provider_status=captured; extra/mismatched actual
charges use CAPTURED rows and durable refunds. Repeated payment IDs have one
effect. payment_failed is nonterminal. The numbered primary attempt drives the
summary, so an extra refunded charge cannot replace checkout/payment status.

```mermaid
sequenceDiagram
    participant S as Expiry scheduler
    participant DB as Database
    participant API as Signed callback
    participant W as Refund worker
    S->>DB: Lock eligible pending order SKIP LOCKED
    S->>DB: Release stock, abandon uncharged attempt, FAILED/PAYMENT_EXPIRED; COMMIT
    API->>DB: Verified late capture + receipt/outbox; COMMIT
    W->>DB: Journal actual charge and LATE_PAYMENT refund; COMMIT
    W->>DB: Claim refund; COMMIT
    Note over W,DB: Provider I/O runs outside transactions/locks
    W->>DB: Refund success, REFUNDED, audit/outbox; COMMIT
```

Expiry commits one order per bounded UUID-keyset scan and shares capture's cutoff.
Order-first locks decide the race. Paid/fulfillment cancellation journals refund,
inventory release and shipment cancellation/ShipmentEvent/audit atomically. COD
creates no session/refund; delivered-state notification collects it once.

## A2. Provider architecture and credentials

PaymentProviderV2 accepts immutable SessionOrder/PaymentAttempt snapshots, never
live ORM objects. It creates/verifies/refunds/fetches/finds unknown refunds.
ProviderDeclined is business failure; ProviderUnavailable is transient/unknown.
Session, refund and polling I/O follows commit and session closure; real PG
NOWAIT probes and transaction assertions prove the absence of row locks.

| Situation | Provider |
|---|---|
| Active owned Razorpay config | Seller's decrypted Razorpay credentials |
| No active config, local/development/test + ALLOW_MOCK_PROVIDERS | Mock v2 |
| No config outside fallback | Prepaid422 payment_not_configured |
| COD | No session provider |
| Existing refund/poll | Original payment provider; inactive real config usable |

Fernet stores only ciphertext. MultiFernet takes newest-first comma-separated
keys; material is required non-locally and for local credential writes. Failures
return generic credentials_unavailable. Only tenant ADMIN owner JWTs manage
settings; keys, other roles and tenant-less platform admins cannot. Responses
contain masked key ID, public routing URL/ID, mode and active flag. DELETE deactivates.

Razorpay fixes HTTPS/Basic auth and 10s/3s timeouts. INR uses Decimal minor units,
short UUID receipts and UUID notes. Lookup checks receipt/amount/currency/ownership;
no universal Orders idempotency header is invented. Refunds use stable receipt
lookup and the documented header/body. Errors exclude provider bodies/credentials.
B8 records official contracts; B13 records live limits.

Mock v2 has deterministic references, configured-secret signed notices and the
same inbound consumer. Refund injection uses durable attempt numbers across
prefork workers. Lookup maps are process-local. Non-local settings reject auto
completion/failure injection. Legacy direct helpers remain compatible; financial
compensation uses the durable worker. Rotation/runbook: A13.

## A3. Refunds and reconciliation

Compensation commits one refund per actual payment, stable paymentUUID:refund key
and refund.requested with stock/state. Reasons include ORDER_CANCELLED,
SHIPMENT_FAILED, RETRIES_EXHAUSTED, AMOUNT_MISMATCH, LATE_PAYMENT and
DUPLICATE_PAYMENT. Actual charge amount/currency governs the obligation. Recovery
refuses refund_pending until owed refunds succeed.

The worker locks ORDER then REFUND, increments durable attempts and commits its
token/lease. External submission/lookup/fetch runs without a transaction. Unknown
outcomes are found before same-key retry; known references are fetched. Final
updates check claim/reference. Confirmed success alone sets REFUNDED and one
audit/success intent; pending stays processing. Rejection/exhaustion creates one
owned failed job and safe error. Verified success beats stale failure. Lease waits
do not consume TASK_MAX_RETRIES+1 attempts. Zero value completes locally without
a fake reference/call.

```mermaid
sequenceDiagram
    participant W as Refund worker
    participant S as Reconciler
    participant DB as Database
    participant P as Provider
    W->>DB: Claim refund, attempts++, lease; COMMIT
    W->>P: Stable key/receipt refund
    P-->>W: Unknown outcome or response before crash
    S->>DB: Claim stale processing refund; COMMIT
    S->>P: Fetch known ID, otherwise find receipt
    P-->>S: Pending, terminal, missing or unavailable
    S->>DB: Journal terminal outcome or same-key retry; COMMIT
    W->>DB: Inbound consumer settles verified success once
```

Reconciliation locks ORDER SKIP LOCKED, commits durable polling leases and closes
sessions before fetch. Separate payment checkpoints order never/oldest-checked
sessions; refunds order by oldest update. Bounded seen-ID sets avoid repeated work
and starvation. Only current tokens edit metadata, but valid fetched truth is
journaled even after expiry/lease replacement. Expired leases recover crashes.

Terminal observations feed stage_receipt and the same financial consumer. Synthetic
IDs hash tenant/provider/type/remote identities; polls reuse one receipt. Real
webhooks have their own IDs, with charge/refund guards preventing double money.
Pending refunds stay processing; confirmed missing unknown refunds requeue the
same obligation/key within budget. Poll errors postpone safely without financial
compensation; polling never increments submission attempts. Live guarantees: B13.

## A4. Shipping lifecycle and COD

The existing native ShipmentStatus labels are retained; no native enum is extended.

| Shipment observation | Order effect |
|---|---|
|PENDING / CREATED|Recorded; no transition|
|IN_TRANSIT|FULFILLMENT_REQUESTED → SHIPPED|
|DELAYED|Recorded; no transition, including delay before pickup|
|DELIVERED|SHIPPED → DELIVERED; from fulfillment, records SHIPPED then DELIVERED atomically|
|CANCELLED|Recorded; order cancellation remains the order service's responsibility|

record_event persists every observation, audit and shipment.status_changed intent
in one transaction. Its payload contains only shipment/event UUIDs. The consumer
locks the order, verifies tenant and immutable observation ownership, and applies
guarded forward transitions. Duplicate delivery IDs are marked once; different
observations cannot move terminal shipments/orders backwards. Cancelled/failed
orders ignore notices. A permitted order cancellation wins over an unconsumed
delivery observation and atomically cancels its shipment, releases stock and
journals any refund. DELIVERED emits order.status_changed; the existing COD
consumer marks the pending collection CAPTURED/collected with one audit.

ManualShippingProvider performs no external I/O. It creates CREATED/awaiting-pickup
shipments with an internal deterministic reference, no courier tracking number and
no invented estimate. The seller uses PATCH /shipments/{id} to set carrier≤60,
tracking_number≤80, HTTPS-only tracking_url≤300 and optional estimated_delivery
(ISO date), then POST /shipments/{id}/events. Staff JWTs/orders:write keys may edit;
customers cannot; foreign IDs return404. Owner-only GET/PATCH /settings/shipping
selects the tenant provider. New tenants snapshot SHIPPING_PROVIDER; raw inserts
default manual. Migration preserves existing tenants' mock selection; configure
them to manual before production use. Both settings and runtime resolution reject
mock shipping outside local/development/test, regardless of payment mock opt-in.

Existing carrier80/tracking120 storage and estimated_delivery_at are retained to
avoid truncating legacy data; validation bounds apply to new writes. Tracking URL
and date columns are additive. Order shipment responses expose the new fields.
Returns/RTO, courier aggregators and shipping-provider callbacks remain deferred.

## A5. Seller-facing outbound webhooks

Owner JWTs manage /api/v1/webhook-endpoints. POST accepts url, events[] and optional
description; GET lists masked metadata; PATCH changes configuration and re-enables;
DELETE deactivates/tombstones while retaining delivery history. POST /{id}/rotate-secret
returns a new signing_secret once. POST /{id}/test queues ping. GET /{id}/deliveries
supports status/event_type/limit/offset; POST /{id}/deliveries/{delivery_id}/replay
requeues the same event and frozen payload, increments replay_count and resets its
attempt budget. Active claims cannot be replayed. Keys/non-owner JWTs cannot manage
these routes; foreign IDs return404 and suspended credentials return403.

Signing secrets have a whsec_ prefix and 256-bit random suffix. Only creation and
rotation return plaintext; Fernet ciphertext is stored, lists use a static mask,
and neither secrets nor ciphertext enter logs/events/delivery responses. Encryption
key configuration is required for credential writes and delivery. DELETE preserves
encrypted journals for audit, so archival is an explicit operator action.

| Public type | Source |
|---|---|
|order.created|Owned order.created|
|order.status_changed|Every owned order.status_changed|
|order.paid / shipped / delivered / failed / cancelled|Corresponding status-change alias|
|payment.refunded|refund.succeeded|
|ping|Owner test action|

Fan-out runs in the workflow transaction, selects active endpoints of the order's
tenant and inserts UNIQUE(endpoint_id,event_id) rows. Alias IDs are deterministic
UUIDv5(base_event_id,type), so subscribing to generic and semantic events produces
two distinct logical events without collisions. The generic/source event keeps
its base ID. Duplicate fan-out preserves the original payload. Each status snapshot
uses the source event's from/to, even if the order has since advanced.

Envelope: {id,type,created_at,tenant_id,data}. Data includes order_id,
external_reference, status/from/to, currency/total, item sku/name/quantity,
carrier/tracking_number and timestamps. Buyer profile, email, phone, private reasons,
notes and credentials are excluded by an allowlist. Merchant reference/catalog/
tracking fields must themselves be supplied without personal data; there is no DLP.

```json
{"id":"00000000-0000-4000-8000-000000000001","type":"order.shipped","created_at":"2026-11-22T08:00:00+00:00","tenant_id":"00000000-0000-0000-0000-000000000001","data":{"order_id":"00000000-0000-4000-8000-000000000002","external_reference":"shop-1001","status":"shipped","from":"fulfillment_requested","to":"shipped","currency":"INR","total":"59.97","items":[{"sku":"TEA","name":"Tea","quantity":3}],"shipments":[{"carrier":"Seller courier","tracking_number":"TRACK123"}],"created_at":"2026-11-22T07:00:00+00:00","updated_at":"2026-11-22T08:00:00+00:00"}}
```

The orders-queue poller locks ENDPOINT then DELIVERY with SKIP LOCKED, commits a
durable attempt/token/lease and closes its session. DNS, decryption, signing and
all HTTP run on detached values. Completion locks the same rows and checks the
token before updating. Expired leases recover crashes; exhausted crash budgets
become dead without another send. Delivery is at least once: a crash after remote
acceptance can repeat an event. Receivers must durably deduplicate envelope id;
acknowledge only after durable acceptance. Ordering across deliveries is not promised.

POST sends Content-Type: application/json, User-Agent: oos-webhooks/1 and
X-OOS-Event-Id / X-OOS-Event-Type / X-OOS-Delivery-Id / X-OOS-Timestamp.
X-OOS-Signature is t=<unix>,v1=<hex HMAC-SHA256(secret, t + '.' + raw_body)>.
JSON is deterministic UTF-8; timestamp/signature refresh each attempt. HTTP2 is
not enabled. 2xx delivers; other statuses/transport failures retry. Redirects are
never followed; response wire bytes are capped and never stored. Errors are fixed
safe codes, not exception/response text. Default HTTP connect/read/write/pool timeout
is5s; OS DNS resolution follows host resolver configuration.

Retries use min(43200,600×2^(attempt-1)) seconds plus0–10% jitter. Eight attempts
span roughly22.5–24.75h. Each failed attempt increments consecutive_failures;
success resets it. At20 failures the endpoint auto-disables with a safe reason.
Owner re-enabling clears failures/disable metadata; dead deliveries need replay.
Replay preserves event/delivery IDs and payload. URL/secret changes affect future
claims; already in-flight calls may use the previous configuration/key. Retain
receiver verification-key overlap during rotation.

SSRF validation occurs at create/update and every attempt: strict HTTPS, no
userinfo/fragments/control characters,443 plus explicit extra ports. It rejects
localhost, private/link-local/CGNAT/multicast/unspecified/reserved addresses,
IPv6 ULA/loopback/mapped forms and ambiguous decimal/hex/octal/short IPv4 encodings.
Every DNS answer must be public. Delivery connects to a validated numeric IP with
original Host and TLS SNI/certificate hostname; it does not resolve the hostname
again or use ambient proxies. This closes the validation-to-connect DNS-rebinding
race. The installed HTTPX transport forwards the documented
[httpcore sni_hostname extension](https://github.com/encode/httpcore/blob/master/docs/extensions.md).
Tests inspect the pinned request and PostgreSQL locks inside MockTransport; live
external TLS/egress is not exercised. Production still needs appropriate egress
controls for its routing/NAT environment. Private/plain-HTTP targets require an
explicit local/dev/test-only override plus port allowlist; validators forbid it
in production, staging and custom non-local environments.

Independent receiver verification examples (raw bytes; reject stale timestamps
and deduplicate the verified envelope id before applying effects):

```python
import hashlib, hmac, json, time


def verify(raw, headers, secret):
    parts = dict(x.split("=", 1) for x in headers["X-OOS-Signature"].split(","))
    stamp = parts["t"]
    if stamp != headers["X-OOS-Timestamp"] or abs(time.time() - int(stamp)) > 300:
        raise ValueError("Invalid webhook")
    expected = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parts.get("v1", "")):
        raise ValueError("Invalid webhook")
    return json.loads(raw)
```
```javascript
import {createHmac, timingSafeEqual} from 'node:crypto';
function verify(raw, headers, secret) {
  const p = Object.fromEntries(headers['x-oos-signature'].split(',').map(x => x.split('=')));
  if (p.t !== headers['x-oos-timestamp'] || !/^\d+$/.test(p.t) ||
      !Number.isSafeInteger(Number(p.t)) || Math.abs(Date.now()/1000 - Number(p.t)) > 300)
    throw Error('Invalid webhook');
  const expected = createHmac('sha256', secret).update(p.t + '.').update(raw).digest();
  const actual = /^[0-9a-f]{64}$/.test(p.v1 ?? '') ? Buffer.from(p.v1, 'hex') : Buffer.alloc(0);
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected))
    throw Error('Invalid webhook');
  return JSON.parse(raw.toString('utf8'));
}
```

## A6. Notifications and development tools

The workflow journals notifications with the business transaction, never buyer
contact data in domain events. UNIQUE(order_id,channel,template) guards duplicate
triggers. Sending is a separate orders-queue poller with durable attempts, due time,
token/lease, and pending/sent/failed/skipped statuses. It reads buyer_email and the
tenant preference immediately before sending, commits and closes the session,
then calls the channel. Only a masked recipient_hint is stored. Disabled tenants,
opt-out or missing email become skipped; re-enabling does not resend an old unique
template. Backend selection is snapshotted on the journal row.

| Trigger | Template |
|---|---|
|Prepaid PAID / COD FULFILLMENT_REQUESTED|order_confirmed|
|SHIPPED|order_shipped|
|DELIVERED|order_delivered|
|FAILED|order_failed|
|CANCELLED|order_cancelled|
|refund.succeeded|refund_processed|

Plain text and HTML use fixed templates, store name and order UUID; HTML variables
are escaped and never evaluated as templates. Subjects remove line breaks.
ConsoleChannel logs only notification UUID/template. SmtpChannel supports STARTTLS,
authentication and multipart email with stable Message-ID. Passwords are SecretStr;
exceptions become fixed safe codes, never recipient/body/server response text.
Production requires SMTP or explicit NOTIFICATIONS_ALLOW_CONSOLE=true opt-out.
Owner-only GET/PATCH /settings/notifications controls the tenant flag.

Known4xx rejections/connect failures retry exponentially up to the durable budget;
permanent5xx/auth/protocol failures do not retry. A confirmed SMTP acceptance wins
over a later QUIT error. SMTP has no remote idempotency/lookup contract: uncertain
post-DATA disconnects or abandoned leases become failed/send_outcome_unknown and
are NOT automatically resent. This avoids duplicates but can leave an unsent or
already accepted message needing operator investigation. It is not a remote
exactly-once or crash-delivery guarantee. New claims stop on opt-out/suspension;
already claimed work may complete. PostgreSQL NOWAIT proves no row locks during SMTP.

The dev-tools Compose profile adds Mailpit (axllent/mailpit:latest; UI8025, SMTP1025)
and a tiny Python webhook-sink on8080. Host publication is loopback-only and ports
are configurable. Sink GET /received lists bounded in-memory requests; POST /config
accepts {failures:N,clear:true}; subsequent POSTs fail N times then accept. It caps
bodies at64KiB and retains1000 records; request bodies are never logged. It is a
development receiver, not production infrastructure. Defaults keep notifications
on console; SMTP_HOST=mailpit/SMTP_PORT=1025 is ready for opt-in SMTP with the profile.
The image/ports match [official Mailpit documentation](https://mailpit.axllent.org/docs/install/docker/).

```powershell
$env:NOTIFICATIONS_BACKEND='smtp'
docker compose --profile dev-tools up -d
# UI: http://localhost:8025; receiver: http://localhost:8090
```

## A7. Public tracking and SSE

Authenticated order responses expose tracking_token and tracking_url. The token is
<order_uuid_hex>.<unpadded base64url(HMAC-SHA256(TRACKING_TOKEN_SECRET,
f"{order_id}:{tracking_version}"))>. Orders default version1. Staff JWTs and
orders:write integration keys may POST /orders/{id}/tracking/rotate; it locks the
owned order, increments version and audits without logging the token. Customers
cannot rotate; foreign IDs return404. Rotation does not alter request fingerprints.
Changing the configured global secret invalidates every token; coordinate all nodes.
Strong non-default tracking secrets are mandatory outside local/development/test.

GET /api/v1/public/track/{token} requires no account credentials. The bearer token
selects its own order/tenant; a foreign principal cannot mint/rotate another order's
link. Parsing is canonical and comparisons use constant-time43-character HMAC
comparison, including dummy comparisons for malformed/unknown tokens. Invalid,
unknown, rotated and suspended-tenant links share a generic404. Cache-Control:
no-store and Referrer-Policy:no-referrer apply to success/error/rate-limit outage
responses. The existing limiter enforces60 requests/min/IP and fails closed.
Application, Uvicorn and HTTPX access logging redact tracking paths/query strings.

Public JSON contains store name, friendly status, friendly timeline timestamps,
item names/quantities, and shipment carrier/tracking number/HTTPS URL/date/events.
It excludes buyer/customer data, tenant identifiers, prices/payment records,
external references, internal reasons, location/notes, tracking version and tokens.
PUBLIC_TRACKING_SHOW_TOTAL=true adds only total/currency. No internal DTO is reused.
Unknown timeline states have safe labels; shipment events expose only bounded
status/timestamp fields. No raw failure internals leak.

Example public JSON (shipment fields are described above):

```json
{"store":"Example Store","status":"confirmed","timeline":[{"at":"2026-11-23T08:00:00Z","label":"Order received","status":"confirmed"}],"items":[{"name":"Tea","quantity":1}],"shipments":[]}
```

| Internal order state | Public status |
|---|---|
|CREATED / VALIDATED|confirmed|
|INVENTORY_RESERVED / PAYMENT_PENDING / PAID / FULFILLMENT_REQUESTED|processing|
|SHIPPED|shipped|
|DELIVERED|delivered|
|CANCELLED|cancelled|
|FAILED|failed|

Timeline labels distinguish order received, awaiting payment, payment received and
preparing shipment without exposing private reasons. Links are bearer credentials.

GET /orders/stream authenticates JWT or orders:read keys. Staff/read keys see their
tenant; customers see only their own orders. Optional order_id is ownership-checked
before streaming (foreign404). GET /public/track/{token}/stream selects one signed
order and emits friendly from/to values. Both are text/event-stream, no-store,
no-referrer and X-Accel-Buffering:no. Public opens share the tracking60/min/IP quota;
authenticated opens use the principal quota. Active caps are10/tenant and5/IP.

```text
id: 42
event: order.status_changed
data: {"order_id":"00000000-0000-4000-8000-000000000001","from":"processing","to":"shipped","at":"2026-11-23T10:00:00+00:00"}

```

New connections start at now. Last-Event-ID or ?last_event_id= resumes; the header
wins. Cursors are nonnegative signed64-bit integers. Heartbeats default15s; the
connection duration defaults300s and an event budget bounds memory. Polls use
short independent sessions and close before awaits/yields. A response-level deadline
also bounds blocked sends, with at most1s for final transport termination; slow
database calls can delay cancellation until their synchronous read returns.
The ASGI finally path shields lease release, including disconnect/send failure.

Timeline IDs can allocate before commit. A5s holdback, recent2x lookback, seen-ID
deduplication and sorted buffering handle bounded gaps. A batch lookahead prevents
a buffered high ID overtaking unread lower IDs. Oversized initial history fails503;
the10000-entry budget closes with a resumable last ID. Arbitrarily delayed
transactions beyond this window are not guaranteed lossless resume; refresh the
authoritative order snapshot after reconnect/outages and deduplicate IDs client-side.

Every poll checks tenant status and the original user's status/role, key scopes/
revocation/expiry, or public tracking version. Invalidated streams end without
private error frames. Public data is exactly order_id/from/to/at; no reasons,
buyer fields or credentials. Redis pub/sub is not needed for polling. Shared
connection caps use atomic leased Redis sorted sets through the Part1 backend;
local memory is process-local. Missing non-local protection fails closed503.

Browser EventSource cannot send Authorization/X-API-Key. A fetch client processes
complete frames and persists an ID only after applying it (reconnect on EOF/429/503):

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

## A8. Rate limiting and abuse protection

First-hit fixed windows use atomic Redis Lua (increment, TTL and server time in
one operation), or a locked process-local memory backend for local use. Cold factory
initialization is serialized. Keys use principal UUIDs or hashed normalized IP/email
identity; secrets/raw identities are not logged.429 includes Retry-After and standard
X-RateLimit-Limit/Remaining/Reset. Authenticated outage behavior is configurable;
public authentication and callbacks fail closed. Degradation logs/metrics have
bounded policy/mode labels.

| Surface | Default | Identity | Backend outage |
|---|---|---|---|
| Login |10/60s AND5/900s | Socket IP; email+IP |503 closed |
| Tenant registration |5/3600s | IP |503 closed |
| Signup |10/3600s | IP |503 closed |
| Authenticated API |600/60s | API-key UUID/user UUID | Configurable, open default |
| Docs/health/ready |600/60s | IP | Open |
| Payment callback |600/60s each | IP AND webhook public ID |503 closed |
| Public tracking |60/60s | Socket IP |503 closed |
| SSE active connections |tenant10/IP5; leased | Tenant AND socket IP |503 closed |

TRUSTED_PROXY_COUNT=0 uses the socket peer and ignores XFF. A positive count uses
the configured Nth-from-right hop. Registration can be disabled or require an
invite, compared in constant time with generic registration_closed. Validation
responses omit secret input/context. Full Docker quotas use an isolated temporary
Redis instance; normal limiter counters and data volumes are preserved.

## A9. Security and consistency

| Threat | Control | Verification |
|---|---|---|
| Forged/altered callback |64KiB streamed cap; raw HMAC before JSON; generic401; no storage | HTTP body/signature tests +E15 |
| Replayed callback | Unique(provider,tenant_id,event_id); receipt/charge/state guards | Two-tenant/duplicate/PG concurrency tests |
| Unrelated provider notices | Retry at most configured receipt age, then ignored + empty UNMATCHED_PAYMENT audit | Payment/refund boundary and late-reference controls; no failed job |
| Cross-tenant access | Owned predicates and config-derived tenant; foreign404; owner-only credentials |80-case resource matrix, settings roles, checkpoint ownership and E10/E14 |
| Secret/PII disclosure | Ciphertext only, masked settings, allowlisted normalized notices, static errors | Five-route sweep, validation/parser/job tests |
| Double money effects after crash | Stable keys, leases, lookup-before-reissue, outbox and financial dedupe | Crash, retry, callback/reconciliation and PG race proofs |
| Stock loss/race | ORDER before sorted inventory locks; atomic adjustment/audit and compensation | PG conservation, expiry race and E8/E30 |
| Abuse/enumeration | Shared atomic quotas, trusted proxy rules, dummy login hash, generic errors | Real Redis layer and E28 |
| Tracking guessing/enumeration |HMAC-SHA256, version revocation, constant-time comparison, uniform404, quotas |Canonical/invalid/foreign/rotated/suspended and limiter tests |
| Email/link disclosure |Masked recipient journal, safe SMTP codes, fixed escaped templates, public allowlist and access-log redaction |Notification privacy/HTML/exception tests and public/logging forbidden-field assertions |
| Event loops | Informational status consumer never transitions again | Transition-forbidden regression |
| Database content in images/git | Exclude DBs, sidecars, secrets and local proof artifacts | Ignore rules and clean build/diff |

SSE uses bounded buffers, shared leases and finite response deadlines; NOWAIT/session
and blocked-send/disconnect tests cover cleanup. Polls recheck credentials and public
version. Failed-job reads use an explicit allowlist DTO; poison validation becomes
a generic409, not an exception log containing payloads. Owner/platform role tests,
32 added four-principal isolation requests and a nine-route secret sweep cover it.

## A10. Data model and event catalog

```mermaid
erDiagram
    TENANTS ||--o{ API_KEYS : owns
    TENANTS ||--o{ INVENTORY : owns
    TENANTS ||--o{ TENANT_PAYMENT_CONFIGS : configures
    TENANTS ||--o{ INBOUND_WEBHOOK_EVENTS : receives
    TENANTS ||--o{ ORDER_TIMELINE : owns
    ORDERS ||--o{ PAYMENTS : attempts
    ORDERS ||--o{ REFUNDS : compensates
    PAYMENTS ||--o{ REFUNDS : refunds
    PAYMENTS ||--o| PAYMENT_RECONCILIATION : polling
    FAILED_JOBS o|--o{ REFUNDS : failure_link
    INBOUND_WEBHOOK_EVENTS {
        UUID tenant_id FK
        string provider "unique with tenant_id and provider_event_id"
        string provider_event_id
        JSON payload "normalized allowlist"
        timestamp received_at
        string status "received/processed/ignored"
    }
    REFUNDS {
        UUID payment_id FK
        UUID tenant_id FK
        UUID order_id FK
        decimal amount
        string status "requested/processing/succeeded/failed"
        string idempotency_key UK
        UUID claim_token
        timestamp lease_until
    }
    PAYMENT_RECONCILIATION {
        UUID payment_id PK
        UUID tenant_id FK
        UUID order_id FK
        UUID claim_token
        timestamp lease_until
        timestamp last_checked_at
    }
```

Configs store provider/mode/key ID, encrypted API/webhook secrets, random routing
ID and active flag. Orders add prepaid/cod and expiry; payments add safe checkout,
provider status and numbered attempt key. Refunds retain currency/reason/reference,
attempts, safe error and failed-job link. Checkpoints/refunds have polling timestamps.
Native lifecycle enum labels are unchanged. Expiry index:(status,expiry,id).
Timeline indexes:(tenant_id,id)/(order_id,id); legacy backfill is one synthetic
current-state observation per order. New public prefixes use17 characters in24
storage; legacy12-character prefixes and suffix hashes keep working.

| Event | Queue / aggregate | Safe effect |
|---|---|---|
|order.created|orders / order|Owned reservation intent|
|payment.requested|payments / order|Resumable session|
|payment.provider_event|payments / receipt|inbound_event_id; financial consumer|
|payment.authorized|shipments / order|Fulfillment once|
|payment.expired|payments / order|PAYMENT_EXPIRED|
|refund.requested|payments / refund|refund_id/order_id; process_refund(UUID)|
|refund.succeeded|payments / order|Financial success once|
|order.status_changed|payments / order|Owned from/to/safe reason; informational|
|order.cancelled, payment.failed, shipment.created/failed|Existing queues / order|Existing notifications|

Intents carry tenant/correlation metadata and share business commits. Processed
markers and terminal receipt/refund guards make redelivery safe.

Scoped Part 2 adds tenant.shipping_provider and shipment tracking_url/date while
preserving existing native enums, carrier/tracking widths and legacy estimate.

```mermaid
erDiagram
    TENANTS ||--o{ WEBHOOK_ENDPOINTS : owns
    TENANTS ||--o{ WEBHOOK_DELIVERIES : owns
    WEBHOOK_ENDPOINTS ||--o{ WEBHOOK_DELIVERIES : receives
    ORDERS ||--o{ SHIPMENTS : fulfills
    SHIPMENTS ||--o{ SHIPMENT_EVENTS : records
    WEBHOOK_ENDPOINTS {
        UUID tenant_id FK
        string url
        string secret_enc "Fernet only"
        JSON events
        boolean is_active
        int consecutive_failures
        timestamp disabled_at
        string disabled_reason
        timestamp deleted_at "soft deletion"
    }
    WEBHOOK_DELIVERIES {
        UUID tenant_id FK
        UUID endpoint_id FK
        UUID event_id "unique with endpoint_id"
        string event_type
        JSON payload "buyer-PII allowlist"
        string status "pending/delivered/failed/dead"
        int attempts
        timestamp next_attempt_at
        int last_status_code
        string last_error "safe code"
        timestamp delivered_at
        int replay_count
        UUID claim_token
        timestamp lease_until
    }
```

shipment.status_changed: ShippingService → orders workflow on shipments queue;
order aggregate + observation UUIDs only. Status/refund/creation consumers fan out
to durable deliveries in the same business transaction; deliver_webhooks polls
on orders queue. Endpoint/delivery tables use portable strings and due/owner
indexes, with timestamps and description on endpoint metadata.

Notifications add a portable journal and tenant.notifications_enabled; orders add
tracking_version integer (default1). No native enum labels change.

```mermaid
erDiagram
    TENANTS ||--o{ NOTIFICATIONS : owns
    ORDERS ||--o{ NOTIFICATIONS : triggers
    NOTIFICATIONS {
        UUID tenant_id FK
        UUID order_id FK
        string channel "unique with order_id/template"
        string template
        string recipient_hint "masked; no raw recipient"
        string status "pending/sent/failed/skipped"
        int attempts
        string last_error "safe code"
        timestamp next_attempt_at
        UUID claim_token
        timestamp lease_until
    }
```

order.status_changed/refund.succeeded → notification journal in the workflow
transaction; send_notifications polls on orders queue. SMTP/console calls run
after commit/session closure. Tracking tokens are computed, not stored in events.

Tasks3B.10–3B.12 add no SQL tables, columns or native enum labels. Streams read
order_timeline; HTTP recovery reuses failed_jobs, order-first locks, outbox and
actor audits. Redis sorted-set leases expire after maximum duration+10s; no event
data/credentials are stored in lease keys. The SQL migration head remains0021.

## A11. Configuration reference

| Variable | Default / contract |
|---|---|
| CREDENTIALS_ENCRYPTION_KEY | Secret Fernet key ring, newest first; required non-locally and for writes |
| PUBLIC_BASE_URL |http://localhost:8000; dashboard callback URL base |
| ALLOW_MOCK_PROVIDERS | Model false; local Compose explicitly true |
| MOCK_PAYMENT_AUTO_COMPLETE | True locally, false/forbidden true non-locally |
| MOCK_WEBHOOK_SECRET | Local simulator default; supply outside local use |
| MOCK_REFUND_FAIL_ATTEMPTS |0; injected transient attempts |
| PAYMENT_WINDOW_SECONDS |1800; positive |
| PAYMENT_CAPTURE_GRACE_SECONDS |120; nonnegative; exact deadline is late |
| PAYMENT_UNMATCHED_RETRY_SECONDS |600; nonnegative; measured from receipt |
| REFUND_LEASE_SECONDS |60; positive |
| PAYMENT_EXPIRY_INTERVAL_SECONDS |30; positive |
| PAYMENT_MAINTENANCE_BATCH_SIZE |100;1–1000, per scheduler kind |
| PAYMENT_RECONCILE_AFTER_SECONDS |60; nonnegative order age/poll cooldown |
| PAYMENT_RECONCILE_INTERVAL_SECONDS |60; positive |
| PAYMENT_RECONCILE_LEASE_SECONDS |60; positive |
| TASK_MAX_RETRIES / TASK_RETRY_BACKOFF_MAX |3 /60; durable financial budget plus backoff |
| RATE_LIMIT_REDIS_URL | Model unset; Compose Redis logical DB2 |
| RATE_LIMIT_FAIL_OPEN / TRUSTED_PROXY_COUNT |true /0 |
| RATE_LIMIT_LOGIN_IP_LIMIT / _WINDOW_SECONDS |10 /60 |
| RATE_LIMIT_LOGIN_EMAIL_LIMIT / _WINDOW_SECONDS |5 /900 |
| RATE_LIMIT_REGISTER_LIMIT / _WINDOW_SECONDS |5 /3600 |
| RATE_LIMIT_SIGNUP_LIMIT / _WINDOW_SECONDS |10 /3600 |
| RATE_LIMIT_AUTHENTICATED_LIMIT / _WINDOW_SECONDS |600 /60 |
| RATE_LIMIT_PUBLIC_LIMIT / _WINDOW_SECONDS |600 /60 |
| RATE_LIMIT_WEBHOOK_LIMIT / _WINDOW_SECONDS |600 /60 |
| RATE_LIMIT_TRACKING_LIMIT / _WINDOW_SECONDS |60 /60, public tracking limit |
| SSE_TENANT_CONNECTION_LIMIT / SSE_IP_CONNECTION_LIMIT |10 /5, active connection caps |
| REGISTRATION_ENABLED / REGISTRATION_INVITE_CODE |true /optional secret |
| FAILURE_INJECTION_ENABLED / FAILURE_TRANSIENT_ATTEMPTS |false /2; local/test only |

cryptography is a runtime dependency. Redis and PostgreSQL test resources are
separate from the local operational databases; pytest refuses non-_test PG names.

| Scoped Part 2 variable | Default / requirement |
|---|---|
|SHIPPING_PROVIDER|mock locally; manual required non-locally; tenant snapshots on creation|
|WEBHOOK_ALLOW_INSECURE_TARGETS|false; forbidden outside local/development/test|
|WEBHOOK_EXTRA_ALLOWED_PORTS|[]; JSON list of1–65535 ports, additional to443|
|WEBHOOK_MAX_ATTEMPTS|8;1–100, durable attempt budget|
|WEBHOOK_TIMEOUT_SECONDS|5; positive≤60 HTTP timeout|
|WEBHOOK_RESPONSE_MAX_BYTES|1024;1–65536 wire-byte cap|
|WEBHOOK_RETRY_BASE_SECONDS|600; positive|
|WEBHOOK_RETRY_MAX_SECONDS|43200; positive|
|WEBHOOK_DISABLE_AFTER_FAILURES|20; positive|
|WEBHOOK_DELIVERY_INTERVAL_SECONDS|10; positive beat interval|
|WEBHOOK_DELIVERY_BATCH_SIZE|100;1–1000|
|WEBHOOK_LEASE_SECONDS|60; must exceed HTTP timeout|

Outbound secrets use the existing CREDENTIALS_ENCRYPTION_KEY ring; no separate
plaintext secret store or external-provider credentials are introduced.

| Notification/tracking variable | Default / requirement |
|---|---|
|NOTIFICATIONS_BACKEND|console; console or smtp only|
|NOTIFICATIONS_ALLOW_CONSOLE|false; explicit non-local console opt-out|
|SMTP_HOST / SMTP_PORT|mailpit /1025|
|SMTP_USERNAME / SMTP_PASSWORD|optional; password secret, private environment only|
|SMTP_FROM / SMTP_USE_TLS|noreply@example.com /false; STARTTLS when true|
|SMTP_TIMEOUT_SECONDS|10; positive≤60|
|NOTIFICATION_MAX_ATTEMPTS|4;1–20|
|NOTIFICATION_RETRY_SECONDS|60; exponential, capped3600|
|NOTIFICATION_LEASE_SECONDS|120; exceeds SMTP timeout|
|NOTIFICATION_BATCH_SIZE / INTERVAL_SECONDS|100 /10|
|TRACKING_TOKEN_SECRET|local demo default;≥8 locally, strong non-default≥32 non-locally|
|PUBLIC_TRACKING_BASE_URL|http://localhost:8000; link base|
|PUBLIC_TRACKING_SHOW_TOTAL|false|
|MAILPIT_UI_PORT / MAILPIT_SMTP_PORT / WEBHOOK_SINK_PORT|8025 /1025 /8090; Compose dev-tools host ports|

| SSE variable | Default / meaning |
|---|---|
|SSE_MAX_SECONDS|300; positive≤3600; response deadline|
|SSE_POLL_SECONDS|1; positive≤15; DB polling interval|
|SSE_HEARTBEAT_SECONDS|15; positive≤60; comment heartbeat|
|SSE_LOOKBACK_SECONDS|5; positive≤60; holdback and recent gap window|
|SSE_BATCH_SIZE|200;1–1000; bounded reads|
|SSE_MAX_EVENTS|10000;1–10000; seen-ID/buffer budget|

Non-local streams require a working RATE_LIMIT_REDIS_URL; request fail-open does
not bypass connection caps. Final transport close has1s grace; lease TTL has10s.
The example environment has no duplicate assignments. Private Compose overrides
must select .env for all app/migration services; base Compose uses .env.example.

## A12. API reference

| Method/path | Access / result |
|---|---|
| POST /api/v1/inventory/{id}/adjustments | Staff JWT/inventory:write key; owned lock, nonzero delta/reason≤200, audit; negative422 |
| PATCH /api/v1/inventory/{id} | Owned locked mutation; foreign404 |
| PUT /api/v1/settings/payments/razorpay | Owner JWT only; encrypted upsert, masked key/URL/mode/active |
| GET same settings path | Owner JWT; owned masked row or404 |
| DELETE same settings path | Owner JWT; deactivate,204 |
| POST /api/v1/webhooks/payments/{provider}/{webhook_public_id} | PUBLIC_WEBHOOK; raw HMAC;200 durable/duplicate,404 unknown,401 invalid,413 oversized,429 quota |
| POST /api/v1/orders | Existing owner/principal rules; optional prepaid/cod, configuration gate, immutable pricing/hash |
| Order get/list/cancel responses | Existing ownership; method/status/expiry and safe checkout while pending |

Every public route declares quotas; owned routes use database-authoritative
identity. Current registry and new-route details follow.

New authenticated routes use the existing authenticated quota (600/60s by
principal by default), authoritative active-tenant check and exact route registry.

| Method/path under /api/v1 | Access / result |
|---|---|
|PATCH /shipments/{shipment_id}|Staff JWT/orders:write key; owned order-first lock; metadata; foreign404, customer403, invalid422, tracking conflict409|
|GET/PATCH /settings/shipping|Owner JWT; owned provider; invalid422/non-local mock400|
|POST /webhook-endpoints|Owner JWT;201, once-only secret; unsafe target400, invalid422, unavailable encryption503|
|GET /webhook-endpoints|Owner JWT; own paginated masked list|
|PATCH /webhook-endpoints/{endpoint_id}|Owner JWT; owned URL/events/active/description; foreign404, invalid422/unsafe400|
|DELETE /webhook-endpoints/{endpoint_id}|Owner JWT; owned soft delete204|
|POST /webhook-endpoints/{endpoint_id}/rotate-secret|Owner JWT; owned new once-only secret|
|POST /webhook-endpoints/{endpoint_id}/test|Owner JWT; owned ping202; inactive409|
|GET /webhook-endpoints/{endpoint_id}/deliveries|Owner JWT; own status/event_type filters, limit1–100/offset≥0|
|POST /webhook-endpoints/{endpoint_id}/deliveries/{delivery_id}/replay|Owner JWT; own same event ID; inactive/live lease409, foreign/mismatch404|


| Added method/path under /api/v1 | Auth / policy / errors |
|---|---|
|GET/PATCH /settings/notifications|Owner JWT; active owned tenant; authenticated quota; non-owner403, invalid422|
|POST /orders/{id}/tracking/rotate|Staff JWT/orders:write key; owned lock; authenticated quota; foreign404, customer/read-only403|
|GET /public/track/{token}|PUBLIC_TRACKING;60/min/IP,429 Retry-After,503 closed; generic404, no-store/no-referrer|

Current registry67 entries:13 public,1 payment webhook,2 public tracking,42 tenant,
9 platform. Exact classification and every public quota remain enforced.
docs/openapi.json is generated from these current routes; freshness is tested.

| New method/path under /api/v1 | Auth, scope and result |
|---|---|
|GET /orders/stream; optional order_id|JWT/orders:read; owned tenant/customer; SSE200; foreign404, role403, cursor422, cap429/outage503|
|GET /public/track/{token}/stream|PUBLIC_TRACKING; signed one-order/friendly data; invalid/rotated/suspended404; quota/cap429; outage503|
|GET /failed-jobs; GET /failed-jobs/{job_id}|Owner JWT; owned rows; status/task/limit/offset; foreign404; role403; validation422|
|POST /failed-jobs/{job_id}/replay|Owner JWT;202 id/status/event_id; foreign404;409 recovery conflicts|
|POST /failed-jobs/{job_id}/discard|Owner JWT;204; foreign404;409 already finalized|
|GET /admin/failed-jobs|Platform-admin JWT; status/task/tenant_id/limit/offset; cross-tenant; role403|
|POST /admin/failed-jobs/{job_id}/replay or /discard|Platform-admin JWT;202 or204;404 missing;409 recovery conflicts|

All recovery routes use the authenticated quota and database-authoritative roles.
Lists default50/max100 with offset≥0. The safe DTO contains IDs, canonical task/
queue, status, retries, timestamps and≤160-character allowlisted error code or a
generic message. Payloads/raw diagnostics never appear; unknown labels are masked.
Replay uses existing3A services: job_already_replayed/order_not_retryable/
refund_pending/tenant_suspended are409. Invalid poison event shapes also return
order_not_retryable. Owner suspension is403 before the route. Replay/discard audit
the actor atomically; one concurrent replay wins. No raw outbox row is returned.

## A13. Operations runbook

Provision a Fernet ring through a secret store on application/worker nodes. As
owner, PUT the seller's test-mode API/webhook credentials; configure the provider
dashboard with the returned public HTTPS URL and B8 events. Preserve raw bytes
through proxies. GET is masked. Deactivation blocks new real sessions while
callbacks/refunds remain usable; explicit local mock fallback is development-only.

For pending orders inspect owned attempt/reference/expiry/checkpoint. Missing
config/provider errors postpone polling. Expiry releases stock at the shared
deadline; late money creates a refund. Unmatched notices retry until their receipt
window, then are ignored/audited; normalized receipts remain for investigation.

For refunds inspect status/attempts/reference/key/lease/error/failed-job link.
Recovery fetches known IDs or finds unknown outcomes before same-key retry.
Never force REFUNDED or change its key. Restore original merchant credentials;
account switching does not migrate captured funds. Zero money stays local.

Rotate using new,old encryption keys, CredentialVault.rotate in a controlled
transaction, then verify before retiring old keys/backups. Signing-secret retry
overlap requires operator coordination: the app stores one secret, not history.
Keep credentials out of source, commands, logs and reports.

Stop writers before migrations, build/migrate, then restart. Downgrade preserves
reconstructible legacy data and refuses operational journals/checkpoints or old-key
conflicts; never truncate hashes/IDs or delete money journals to force rollback.
B14 gives test/reviewer commands. Acceptance preserves existing data volumes,
uses unique proof rows and temporary Redis/overrides, reads restored defaults,
and removes its own cache/config and temporary encrypted webhook fixtures.
Its temporary platform principal uses a random credential and is revoked on cleanup;
audit identity and order/financial history remain. Its Razorpay HTTP case is a local session
fixture with dummy credentials and no remote order/charge.

Scoped Part 2 operations: select manual through owner PATCH /settings/shipping,
attach courier metadata, then record transit/delivery observations. Existing
migrated tenants preserve mock; switch them before production shipping.

For webhook failures inspect the owned delivery's status/attempts/safe error and
next_attempt_at. Fix URL/DNS/TLS/key configuration; re-enable an auto-disabled
endpoint, then replay dead deliveries. Receiver idempotency must use the unchanged
event ID. Rotate signing secrets through the owner action and retain receiver-key
overlap for in-flight calls. Rotate encrypted ciphertext with the existing Fernet
ring before retiring old keys. Never edit a delivery payload/key to force success.
Notifications, public tracking, SSE and HTTP recovery are implemented and
covered by the full local Docker acceptance runner documented in B6/B14.

Notification operations: enable the dev-tools profile and opt into SMTP, or configure
the real relay through private environment values. Inspect journal safe status/error;
do not blindly resend send_outcome_unknown rows. Confirm remote acceptance manually.
Disable notifications through the owner setting before changing delivery policy.

Tracking operations: share only the generated public link, rotate an order link
through its staff/key endpoint, or coordinate TRACKING_TOKEN_SECRET changes across
nodes to invalidate all links. Suspended stores return404 publicly. Preserve the
version column on rollback: rotated versions deliberately refuse downgrade.

For recovery, owner JWTs list/detail their /failed-jobs and POST replay/discard;
platform admins use /admin/failed-jobs with optional tenant_id. Inspect private
diagnostics through protected operations tooling, never public DTOs. Resolve
refund_pending before replay; business/terminal failures do not reopen. A202 means
durable requeue, not completed fulfillment. Keep workers and relay running.

For streams, persist the applied Last-Event-ID, reconnect on finite closure and
refresh the snapshot after outages. Respect Retry-After and proxy idle/buffering
settings. Rotate links or revoke credentials to stop future polls. Crash leases
expire; Redis outages fail closed for new connections. Run export_openapi.py after
intentional API edits; --check/freshness tests catch stale artifacts. Coverage gates
omit tests and require90% overall and for each required new Part2 module.

## A14. Limitations

Frontend and production deployment/HA/soak remain deferred.
Application ownership controls do not add PostgreSQL RLS. Global email uniqueness
and the existing HTTP low-stock pagination cap remain unchanged. Local memory
limiting and simulator lookup maps are process-local; use shared Redis for real
multi-worker quotas. Remote order creation/refund visibility and exactly-once
settlement depend on the provider and are not live-verified. Known processing
refunds are polled; failed obligations stay visible for investigation, and a later
verified success may still settle them. A sample migration is not arbitrary data
reconciliation. See the single current NOT VERIFIED list in B13.

SSE gap recovery is bounded by its configured window; arbitrary long transactions
need snapshot reconciliation. Synchronous slow DB reads can delay teardown. No
live provider or production guarantee follows from local Docker verification.

# PART B — Current verification

## B1. Summary

All Phase3B tasks3B.1–3B.13 and3B.P1 are DONE; none are skipped.
Session starts at clean bf02c7c on phase-3b-integrations; earlier tasks are retained.
Baseline before changes:559 passed,43 skipped in271.91s.
Task3B.13 extends the real Docker runner; no product feature is added.
All29 specified E-cases pass, plus retained S5/S6/S10 and payment controls.
Real Mailpit/sink/HTTP streams and broker-backed replay run against PostgreSQL.
Legacy0007→0021→0007→0021 preserves fixture data and native enum labels.
Ruff/format/mypy/OpenAPI and both full coverage suites pass; B4 has actual results.
Runtime defaults are verified on api/worker/scheduler; application volumes remain.
Source/evidence commit:a9abc48; report is finalized in its own commit.
Live Razorpay, external SMTP/TLS, hosted CI and production remain NOT VERIFIED.
No push, merge or deployment is performed; B13 records precise residual limits.

## B2. Task status

| ID | Task | Status | Commits | Notes |
|---|---|---|---|---|
|3B.1| Carry-over fixes a–e |DONE |6eb6b04,4ef2ff7 |Reviewed; not reimplemented |
|3B.2| Shared quotas/abuse controls |DONE |b721647 |Real Redis layer/E28 |
|3B.3| Transactional timeline/status |DONE |1bb5fc1 |Current full gates |
|3B.4| Encryption/tenant configuration |DONE |f43531b,72033a7,9ef0307 |Reviewed |
|3B.5| Provider v2, session/COD, inbound consumer/refunds |DONE |7585220,78ada7d,72033a7,9ef0307,09413d8,f726b58,6cb581c |Reviewed changes 1–6; 7–9 below |
|Review0a| Bounded unmatched notices |DONE |5bdbea8 |Payment and refund notices |
|Review0b| Shared grace deadline |DONE |153b0e2 |Exact boundary covered |
|Review0c| Tenant receipt uniqueness/safe migration |DONE |90d9c47 |Downgrade conflict refusal |
|3B.5(7)| Expiry scheduler |DONE |e00a961 |Shared grace, SKIP LOCKED |
|3B.5(8)| Payment/refund reconciliation |DONE |122cb3b |Durable detached polling |
|3B.5(9)| Real PG races/NOWAIT proofs |DONE |d33935c |Prior session/refund proofs retained |
|3B.P1| Full local quality/acceptance/migration/report |DONE |db98ac4 |Additional report-only commit |
|3B.6| Manual shipping lifecycle and COD |DONE |722e6f4 |API/consumer/PG migration and cancellation race |
|3B.7| Seller webhooks, SSRF and detached delivery |DONE |5570b95 |Pinned HTTP/PG NOWAIT, owner/privacy/retry proofs |
|3B.8| Notifications and dev-tools |DONE |4f3b5aa |Durable journals/SMTP proof/profile smoke |
|3B.9| Revocable public tracking |DONE |581470b |Privacy/rotation/quota/headers/logging |
|3B.10| Scoped SSE |DONE |79de3db |Resume/gaps/caps/disconnect; blocked-send guard in12 |
|3B.11| Failed-jobs HTTP |DONE |e5ee852 |Owner/platform privacy, PG replay race |
|3B.12| Quality/OpenAPI/README |DONE |ce87002 |Fresh export; coverage gates; current docs |
|3B.13| Full-phase Docker and legacy proof |DONE |a9abc48 |All specified29 E-cases; full suites; restored defaults; report-only commit follows |

## B3. TDD evidence

Observed failures are distinguished from positive controls. Detailed original
outputs remain in reviewed commits; B4 is the final current-tree result.

| Task / regression | Observed red | Green / positive controls |
|---|---|---|
|Unmatched window|5 failed10.04s;1 control|SQLite18/2 skipped; PG20|
|Capture grace|119s case failed4.05s;3 controls|SQLite23/2 skipped; PG11; exact boundary|
|Tenant receipts|2 failed/1 skipped1.94s|SQLite13/2 skipped; PG15|
|Expiry|8 failed/1 skipped3.86s|PG10; boundary/rollback/batch/refund|
|Reconciliation|Missing feature:1 failed/11 errors4.90s; fairness1 failed1.46s|20 passed/93%; durability23/1 skipped; PG27|
|P1 race/HTTP/legacy proofs|Positive controls|5 passed7.10s; raw-HTTP strengthening5 passed6.98s;3 legacy PG tests|
|3B.6 shipping|1 failed/8 errors4.33s|11 passed; PG14 passed28.07s; shipping commit438/35 skipped235.41s|
|Cancellation race / old schemas|1 failed/1 control4.62s;2 full-run legacy failures|PG14 lifecycle/migration;5 legacy checks15.62s|
|3B.7 SSRF/API/worker|38 failed/3 errors26.92s; worker2 failed4.77s|43 initial passes;48 edge controls34.12s; HTTP/migration PG controls|
|IP pinning / explicit port0|1 failed2.69s;1 failed5.95s|Pinned IP+Host/SNI, redirect/body bounds and port validation|
|3B.8 notifications|9 failed5.00s; SMTP classifier1 failed/16 controls|19 passed/1 skipped13.05s; PG3 passed9.27s;94% target coverage|
|3B.9 tracking/logging|3 failed1.83s; log privacy1 failed1.08s|16 passed/1 skipped19.91s; log controls16 passed8.86s|
|Logging/legacy isolation|Two531/1 failed/40 skipped runs; Alembic disabled loggers|Migration/log order2 passed/1 skipped2.59s; explicit capture/activation; assertions retained|
|3B.10 SSE|6 failed4.28s; history/backlog/header regressions|PG13 passed17.61s; task commit543 passed/42 skipped490.81s|
|3B.11 HTTP recovery|7 failed/1 skipped5.68s|PG9 passed14.41s; task commit551 passed/43 skipped402.01s; new modules100%|
|3B.12 export/env/privacy|4 failed/2 controls7.26s; FastAPI status inference error|24 passed/2 skipped32.83s; fresh export; explicit200|
|3B.13 runner/signature control|1 failed/1 control1.28s: missing seller runner module|2 passed1.09s; tampered signature rejected; real Docker scenarios in B6|
|3B.13 platform fixture cleanup|1 failed2.94s: temporary admin remains active|3 passed2.76s; only owned principal retired; real PG/Docker rerun in B6|
|Blocked client send|1 failed4.43s: deadline exceeded|19 passed/2 skipped32.62s; fresh PG21 passed32.05s; SSE/API100%|

Poison payload409, coverage90/89.99/missing evidence, Windows paths, revocation,
README Python/Node/fetch/private-Compose and historical seed checks are positive
controls. Valid platform fixtures are tenant-less at insertion. Functional stream
tests permit300ms for covered DB reads; blocked-send proof still requires30ms.

New Docker scenarios are executable acceptance assertions; no existing assertion
was relaxed. Initial setup failed on Windows-blocked host SMTP port1025; the
supported MAILPIT_SMTP_PORT=11025 override fixes infrastructure without changing
container SMTP port1025 or acceptance expectations. B12 records any later findings.

## B4. Final quality gates and production coverage

```text
All checks passed!
210 files already formatted
Success: no issues found in 169 source files
OpenAPI export is fresh
SQLite: 561 passed, 43 skipped in 725.72s (0:12:05)
PostgreSQL + Redis: 603 passed, 1 skipped in 1487.34s (0:24:47)
Production coverage gate: PASS overall >=90%; all17 required Part2 modules >=90% in both runs
```

The sole PostgreSQL skip is test_razorpay_test_mode_session: its explicit guard
requires RAZORPAY_TEST_KEY_ID and RAZORPAY_TEST_KEY_SECRET, which are absent.
SQLite also skips42 PostgreSQL/Redis-only checks; those run in the PG layer.
The focused runner controls are3 passed in2.76s after the cleanup regression.
Tests are omitted by pyproject coverage configuration. Every nonempty new
Phase3B production module plus shipping service/API also exceeds90% below. CI has
SQLite and PostgreSQL+Redis jobs with these gates; hosted execution is unverified.

| Module | SQLite | PG+Redis |
|---|---:|---:|
|api/v1/endpoints/failed_jobs.py|100.00%|100.00%|
|api/v1/endpoints/notification_settings.py|96.30%|96.30%|
|api/v1/endpoints/payment_config.py|100.00%|100.00%|
|api/v1/endpoints/payment_webhooks.py|100.00%|100.00%|
|api/v1/endpoints/public_tracking.py|100.00%|100.00%|
|api/v1/endpoints/shipments.py|93.85%|93.85%|
|api/v1/endpoints/streams.py|100.00%|100.00%|
|api/v1/endpoints/webhook_endpoints.py|100.00%|100.00%|
|core/crypto.py|100.00%|100.00%|
|core/ratelimit.py|97.74%|98.87%|
|core/ssrf.py|100.00%|100.00%|
|domain/timeline.py|100.00%|100.00%|
|middleware/rate_limit.py|100.00%|100.00%|
|notifications/channels.py|93.51%|93.51%|
|notifications/service.py|94.50%|91.74%|
|providers/manual.py|100.00%|100.00%|
|providers/mock_v2.py|92.65%|92.65%|
|providers/razorpay.py|92.68%|92.68%|
|providers/resolution.py|100.00%|100.00%|
|schemas/failed_jobs.py|100.00%|100.00%|
|schemas/payment_config.py|100.00%|100.00%|
|schemas/webhooks.py|100.00%|100.00%|
|services/inbound_payments.py|98.00%|98.00%|
|services/outbound_webhooks.py|98.04%|92.16%|
|services/payment_config.py|91.89%|91.89%|
|services/payment_events.py|100.00%|100.00%|
|services/payment_expiry.py|100.00%|100.00%|
|services/payment_reconciliation.py|97.53%|97.53%|
|services/payment_sessions.py|96.74%|98.91%|
|services/payment_timing.py|100.00%|100.00%|
|services/provider_events.py|95.50%|97.30%|
|services/refund_intents.py|100.00%|100.00%|
|services/refunds.py|92.17%|95.18%|
|services/shipping.py|100.00%|100.00%|
|services/sse.py|100.00%|100.00%|
|services/tracking.py|97.73%|97.73%|
|services/webhook_delivery.py|98.35%|98.35%|
|TOTAL production|97.15%|97.21%|

## B5. Migrations and legacy-data proof

Head is0021_public_tracking. Part1 adds widened key prefixes, synthetic timeline,
encrypted tenant credentials, payment sessions, receipt/refund/claim journals,
tenant-scoped receipt uniqueness, expiry index and polling checkpoints.0018 adds
shipping metadata without narrowing80/120-character legacy fields;0019 adds
seller webhook journals;0020 notifications;0021 tracking version. Native enum
labels remain unchanged. Tasks3B.10–3B.13 add no schema changes.

The0007 fixture preserves a paid order, authorized payment,3×19.99 item/59.97 total,
stock7/3, warehouse/product/user, legacy prefix/hash and IDs through head→0007→head.
Operational polling, shipping, endpoint/delivery, notification and rotated tracking
state refuse destructive downgrade. Receipt downgrade refuses old-key conflicts;
ciphertext/history guards remain. These sample roundtrips are not arbitrary
production repair/rollback. Reflected historical seeds keep every original check.

Fresh standalone PostgreSQL0007→head→0007→head check:1 passed in7.21s.
The complete raw Alembic output is [legacy migration output](phase-3b-legacy-migration.txt).
The current full suites also retain the shipping/webhook/notification/tracking
roundtrips and all downgrade refusal checks. Key raw proof outputs:

```text
0007->HEAD->0007->HEAD: LEGACY ORDER/PAYMENT/ITEM/INVENTORY/PREFIX/HASH AND NATIVE ENUM LABELS PRESERVED
OPERATIONAL CHECKPOINT DOWNGRADE REFUSAL: BEFORE DATA LOSS; FINANCIAL SNAPSHOT UNCHANGED
SHIPPING LEGACY ROUNDTRIP: IDS/80-CHAR CARRIER/120-CHAR TRACKING/MONEY PRESERVED; OPERATIONAL DOWNGRADE REFUSED
OUTBOUND WEBHOOK EMPTY ROUNDTRIP PASS; OPERATIONAL CONFIG/DELIVERY/CLAIM DOWNGRADE REFUSED WITHOUT LOSS
NOTIFICATION LEGACY ROUNDTRIP PASS; OPERATIONAL JOURNAL/SETTING DOWNGRADE REFUSED
TRACKING LEGACY ROUNDTRIP PASS; ROTATED VERSION DOWNGRADE REFUSED; MONEY/REVOCATION PRESERVED
```

The reviewed Part1 operational upgrade was0014→0017 with writers stopped and
volumes preserved. The real application stack is already at0021_public_tracking before experiments;
Compose's migration service exits0 on recreation. The standalone legacy roundtrip
uses disposable orders_test/schema fixtures, never downgrades application data.

## B6. Full Docker end-to-end evidence

The real Compose stack below runs all29 specified cases: E1–E20 and E22–E30.
E21 is absent from the prompt. Complete output is preserved in
[Docker acceptance transcript](phase-3b-docker-e2e.txt); repeated inventories and
identical runtime JSONs are condensed here. All three services' defaults are
asserted; the inline runtime example is api. E15 uses a synthetic provider session.

```text
ISOLATED Redis HTTP quotas; normal quota counters and all data volumes preserved
PASS E1 happy path: fulfillment_requested; payment=1 shipment=1 stock=(9,1)
PASS E2 insufficient inventory: failed/INSUFFICIENT_INVENTORY; stock=(0,0); retry jobs=0
PASS E3 payment decline: failed/PAYMENT_DECLINED; stock=(10,0); retry jobs=0
PASS E4 shipment failure: failed/SHIPMENT_FAILED; stock=(10,0); retry jobs=0
PASS E5 worker crash and redelivery: SIGKILL before consumption; resumed; broker redelivery; one reservation/payment/shipment
PASS E6 broker outage: five HTTP 202; pending backlog=5; recovered all five; no duplicate effects
PASS E7 request idempotency: one order; one order.created intent; one payment/shipment
PASS E8 concurrent orders: 30-vs-10: fulfilled=10 failed=20 available=0 reserved=10 conserved=10
PASS S5 transient retry: transient retry succeeds; exactly one reservation/payment/shipment
PASS S6 exhausted retry and replay: retries exhausted=3; stock compensated; replay completed; one abandoned + one captured attempt, one shipment
PASS S10 cancellation at every requested state: CREATED/INVENTORY_RESERVED/PAID/FULFILLMENT_REQUESTED; repeated cancel; stock/refund/shipment correct
RESULT: 11/11 experiments passed
PASS E9 two tenant workflows: two registered stores; same SKU; key orders completed independently; stock=(17,3) each
PASS E10 isolation: B JWT/key foreign order/product/inventory=404; list totals=1; A IDs absent; shared idempotency independent
PASS E11 pricing: 19.99 x 3=59.97; product changed to25; snapshot unchanged; unknown SKU=422
PASS E12 key lifecycle and suspension: revoked/expired=401; suspended JWT/key=403; activation restored both
PASS E13 exhausted recovery: retries exhausted=3; stock compensated; replay completed; one abandoned + one captured attempt, one shipment
PASS E14 cross-tenant concurrency: 20 concurrent orders per tenant, shared SKU: fulfilled=10 failed=10 each; stock independent
RESULT: 6/6 tenancy experiments passed
PASS E20 PAID: real shipment consumer paused; attempts=3; one refund/audit/intent; stock restored
PASS E20: broker/prefork worker; two transient failures, attempts=3, one refund success/audit; shipment cancelled, stock restored
PASS E17 pipeline: signed mock mismatch -> receipt -> broker consumer -> async refund; pending, no shipment
PASS late-cancel pipeline: signed late payment refunded, cancellation and stock preserved (beat expiry is checked separately by E16)
PASS E15 HTTP fixture: raw HMAC accepted once, duplicate=200, bad/missing=401, oversize=413; broker fulfillment one payment/shipment; NO remote Razorpay session/money
PASS E16: actual beat expiry after window+grace, stock restored; signed late capture twice, one succeeded refund, no fulfillment
PASS E28: login email/IP and registration quotas, forged XFF ignored, independent API keys, strict429/Retry-After headers
PASS E30:30 orders+15 adjustments, accepted_delta=5, fulfilled=25, stock conserved and audits exact
PASS E19: manual metadata/events; stale/duplicate harmless; one DELIVERED and one COD collection/audit
PASS E22: four logical lifecycle events; every received signature independently verified; delivered logs, no buyer PII
PASS E23: 503/503/204; exhaustion dead after3; replay same event ID/replay_count1; one success each
PASS E24: 14 real API rejections: localhost, metadata/private/CGNAT, IPv6/mapped, encoded IPs and non-HTTPS
PASS E25: Mailpit exactly1 per trigger across6 templates; graceful worker restart mid-order and repeat polls no resend; opt-out0
PASS E26: safe public JSON/headers; rotate invalidates old link; configured3-request quota returns429/Retry-After
PASS E27: real HTTP live sequence; disconnect/reconnect Last-Event-ID exact timeline; friendly public stream; foreign0
PASS E29: real exhausted retries; owner/foreign/platform HTTP; replay once, exact attempts/shipment/stock; discard+actor audits
PASS session subset: pending checkout, durable attempt, cancellation and stock release
PASS E18: COD fulfillment without session; cancellation releases stock and shipment
PASS selected Phase 3B acceptance section: all
RESTORED api {"auto_complete": true, "refund_failures": 0, "window": 1800, "grace": 120, "expiry_interval": 30.0, "reconcile_after": 60, "reconcile_interval": 60.0, "failure_injection": false, "shared_rate_backend": true, "rate_backend_is_default": true, "encryption_configured": false, "notifications_backend": "console", "webhook_insecure": false, "webhook_attempts": 8, "webhook_interval": 10.0, "notification_interval": 10.0, "stream_max": 300.0, "stream_poll": 1.0, "stream_lookback": 5.0, "tracking_limit": 60}
```

The complete run precedes the runner's platform-fixture cleanup fix. Business
assertions are unchanged; the [focused E29 rerun](phase-3b-cleanup-e29.txt) verifies
random credentials, committed retirement and restored defaults. Both refreshed
full suites include its new regression. Additional raw proof:

```text
PASS fixture cleanup: both earlier owned platform principals disabled; all unrelated principals unchanged
PASS E29: real exhausted retries; owner/foreign/platform HTTP; replay once, exact attempts/shipment/stock; discard+actor audits
PASS platform fixture cleanup: temporary principal disabled
PASS selected Phase 3B acceptance section: sellers
owned_platform_fixtures | active
-------------------------+--------
                       4 |      0
(1 row)
```

Unique order, money and audit fixtures plus existing volumes remain. Only owned
temporary encrypted webhook rows/cache/config are removed; platform proof accounts
are disabled. Normal Redis quota windows are untouched. SMTP host11025 avoids the
Windows-blocked1025 binding; internal mailpit:1025 stays unchanged. Mailpit/sink
remain on loopback8025/8090. E22 verifies source sequence, not transport ordering;
E25 restarts gracefully between stages, not during uncertain SMTP acceptance.

Final raw service/state/status inventory:

```text
SERVICE        STATE     STATUS
api            running   Up About a minute
mailpit        running   Up 58 minutes (healthy)
migrate        exited    Exited (0) About a minute ago
outbox-relay   running   Up 35 minutes
postgres       running   Up 2 hours (healthy)
rabbitmq       running   Up 43 minutes (healthy)
redis          running   Up 2 hours
scheduler      running   Up 2 minutes
webhook-sink   running   Up About an hour
worker         running   Up 2 minutes
```

## B7. Concurrency, lock and isolation proofs

The final PG suite covers both expiry/capture winners, two schedulers, polling vs
raw webhook dedupe, stock conservation, financial exactly-once effects and
cross-tenant ownership. Provider/refund/HTTP/SMTP probes use independent NOWAIT
sessions during blocked calls and peer SKIP LOCKED claims. HTTP transport/SMTP are
mocked deliberately; no live TLS/egress guarantee follows. Retained focused PG
markers below identify assertions passing again in the current full PG suite.
README markers come from separate fresh synthetic checks after this session's
documentation update. Local Docker transport proofs are recorded in B6.

```text
EXPIRY/CAPTURE WINNER=CAPTURE; ONE TRANSITION; STOCK CONSERVED; NO DOUBLE REFUND
EXPIRY/CAPTURE WINNER=EXPIRY; ONE TRANSITION; STOCK CONSERVED; NO DOUBLE REFUND
POLL PAYMENT: PEER SKIP LOCKED; ORDER/INVENTORY/PAYMENT/CLAIM NOWAIT DURING FETCH; REAL WEBHOOK DEDUPED
POLL REFUND: PEER SKIP LOCKED; ORDER/INVENTORY/PAYMENT/CLAIM NOWAIT DURING FETCH; REAL WEBHOOK DEDUPED
EXPIRY DURING PROVIDER POLL: LATE TRUTH JOURNALED; ONE REFUND; STOCK RESTORED
TWO EXPIRY SCHEDULERS: SKIP LOCKED; TWO ORDERS EXPIRED ONCE; STOCK CONSERVED
CONCURRENT VERIFIED RECEIPTS: ONE JOURNAL ROW AND ONE INTENT
CONCURRENT CAPTURES: ONE PAID TRANSITION AND ONE FULFILLMENT INTENT
REFUND PROVIDER IN FLIGHT: ORDER, REFUND AND INVENTORY NOWAIT PASS; ONE CLAIM/EFFECT
OUTBOUND HTTP IN FLIGHT: TENANT/ENDPOINT/DELIVERY NOWAIT AND OWNER ROTATION PASS; PEER SKIP LOCKED; ONE SEND
SMTP IN FLIGHT: ORDER/TENANT/NOTIFICATION NOWAIT PASS; PEER LEASE SKIP; ONE SEND
SSE PG GAP: LOWER ID COMMITS LAST; SORTED ONCE; NO SESSION HELD BETWEEN POLLS
FAILED JOB HTTP PG RACE: TWO REQUESTS, ONE 202/ONE 409, ONE OUTBOX EFFECT AND ACTOR AUDIT
PASS README Python: Razorpay/raw-body and seller/timestamp signatures; tamper, malformed, stale rejected
PASS README Node: both signature schemes and rejection cases; fetch SSE split-frame parser and resume header
PASS README private Compose override: all five app/migration services use the final private env values; no services changed
```

Four principal variants retain earlier resource checks and add8 stream/recovery
actions each (32 additional HTTP isolation requests). Registry67 is exact:
13 PUBLIC,1 PUBLIC_WEBHOOK,2 PUBLIC_TRACKING,42 TENANT_SCOPED,9 PLATFORM_ADMIN;
all public routes declare quotas. A nine-new-route sweep excludes buyer fields,
key/token material, payloads and raw diagnostics. ASGI tests prove closed poll
sessions, actual disconnect, rotation, lease release and blocked-send deadline.
README proofs use synthetic signatures, a mocked fetch and read-only Compose;
they do not send money/email. Separately, B6 exercises real local SMTP, sink HTTP,
stream reconnect/isolation and broker-backed failed-job replay. Docker tests do not
replace independent PostgreSQL NOWAIT/SKIP LOCKED proofs in the full test suite.

## B8. Razorpay verification ledger

Official documentation checked22 November2026. VERIFIED means the primary source
documents the contract and local MockTransport/parser tests exercise our use; it
does not claim live execution. Application-only fields are labelled INTERNAL.

| Contract | Status / primary source |
|---|---|
|POST /v1/orders; Basic auth; integer amount/currency; receipt≤40; notes; returned id |VERIFIED [Create order](https://razorpay.com/docs/api/orders/create/) |
|GET /v1/orders; receipt filter and items |VERIFIED [Fetch orders](https://razorpay.com/docs/api/orders/fetch-all/); code checks exact receipt/amount/currency/ownership |
|GET /v1/orders/{id}/payments; items id/order_id/status/amount/currency |VERIFIED [Order payments](https://razorpay.com/docs/api/orders/fetch-payments/) |
|POST /v1/payments/{id}/refund; amount/speed/receipt; id/status; X-Refund-Idempotency, same body/key and409 retry |VERIFIED [Normal refund](https://razorpay.com/docs/api/refunds/create-normal/);32-hex key meets documented character/length rules; account/region entitlement remains untested |
|GET /v1/payments/{id}/refunds; count≤100/skip; collection receipt/payment_id/amount/id/status |VERIFIED [Payment refunds](https://razorpay.com/docs/api/refunds/fetch-multiple-refund-payment/); unknown outcomes require exact receipt/payment/amount |
|GET /v1/refunds/{id}; id/status |VERIFIED [Fetch refund](https://razorpay.com/docs/api/refunds/fetch-with-id/) |
|Raw HMAC-SHA256 hex; X-Razorpay-Signature; x-razorpay-event-id; retry signing-secret overlap |VERIFIED [Validate webhooks](https://razorpay.com/docs/webhooks/validate-test/); old-secret history is not an app feature |
|payment.captured/payment.failed payment.entity id/order_id/amount/currency |VERIFIED [Payment events](https://razorpay.com/docs/webhooks/payments/) |
|order.paid captured-equivalent payment entity |VERIFIED [Order events](https://razorpay.com/docs/webhooks/orders/); shared charge guards prevent double effect |
|refund.created/processed/failed and refund entities |VERIFIED [Refund events](https://razorpay.com/docs/webhooks/refunds/); created alone is not success |
|Checkout expiry, normalized types, synthetic hash IDs, claim leases, find_refund() |INTERNAL; no invented remote endpoint/field |
|Universal Orders idempotency header/atomic lookup-create guarantee |UNVERIFIED; not invented or relied on |
|Live entitlement, visibility/idempotency races and settlement |NOT VERIFIED; optional test-mode credentials absent |

On23 November2026 the README webhook contracts are rechecked against the official
[validation](https://razorpay.com/docs/webhooks/validate-test/),
[payment events](https://razorpay.com/docs/webhooks/payments/) and
[refund events](https://razorpay.com/docs/webhooks/refunds/) pages: raw-body
HMAC-SHA256, X-Razorpay-Signature, x-razorpay-event-id, payment.captured/failed,
order.paid and refund.processed/failed. API endpoint rows above retain their
22 November verification date. No new remote behavior or live execution is claimed.

## B9. Phase 3A carry-over

| Item | Current state |
|---|---|
|Inventory PATCH/adjustment race, negative stock and audit |Resolved; PG conservation and E30 |
|Unscoped generic repository/inventory helpers |Removed and guarded |
|Low-stock global scheduler cap/cross-store aggregation |Per-tenant keyset scans/counts; HTTP pagination cap unchanged |
|Short API-key prefix |17-character public prefix,24 storage,12-character legacy compatibility |
|Duplicate warehouse/inventory errors, dummy login hash |Typed409 and uniform hash work retained |
|Payment/refund I/O under locks |Detached sessions/refunds/polling, real NOWAIT proofs |
|Abuse and transactional history |Implemented and verified |
|Shipping lifecycle / seller webhooks |Resolved in3B.6/3B.7; current proofs above |
|Notifications/public tracking |DONE; durable SMTP journal and revocable public links |
|SSE/failed-jobs HTTP |DONE; scoped streams and owner/platform recovery |
|RLS/global email uniqueness/HA/monitoring/deployment |Existing limitations, outside Phase3B |

## B10. Changed files and commits

Current complete Phase3B diff against main (every file; width flags do not limit count):

```text
.dockerignore              |    1 +
 .env.example               |   76 +-
 .github/workflows/ci.yml   |   13 +-
 .gitignore                 |    1 +
 README.md                  |  706 +-
 .../0008_api_key_prefix.py |   25 +
 .../0009_order_timeline.py |   42 +
 .../0010_payment_config.py |   36 +
 ...011_payment_sessions.py |   37 +
 ...012_inbound_webhooks.py |   31 +
 .../0013_refunds.py        |   39 +
 .../0014_refund_claims.py  |   34 +
 ...0015_tenant_receipts.py |   29 +
 ...payment_expiry_index.py |   16 +
 ...yment_reconciliation.py |   31 +
 ...0018_manual_shipping.py |   35 +
 ...19_outbound_webhooks.py |   69 +
 .../0020_notifications.py  |   45 +
 ...0021_public_tracking.py |   21 +
 .../failed_jobs.py         |  108 +
 .../v1/endpoints/health.py |    7 +-
 .../endpoints/inventory.py |   72 +-
 ...otification_settings.py |   45 +
 .../v1/endpoints/orders.py |   21 +
 .../payment_config.py      |   41 +
 .../payment_webhooks.py    |   41 +
 .../public_tracking.py     |   16 +
 .../endpoints/shipments.py |  104 +-
 .../endpoints/streams.py   |   72 +
 .../endpoints/tenancy.py   |    5 +-
 .../webhook_endpoints.py   |  200 +
 app/api/v1/router.py       |   30 +-
 app/auth/principal.py      |   34 +-
 app/core/config.py         |  124 +-
 app/core/crypto.py         |   44 +
 app/core/exceptions.py     |    8 +
 app/core/logging.py        |   23 +
 app/core/ratelimit.py      |  316 +
 app/core/ssrf.py           |   70 +
 app/domain/order_state.py  |   18 +-
 app/domain/timeline.py     |   61 +
 app/events/contracts.py    |    2 +
 app/events/publisher.py    |    5 +-
 app/main.py                |   15 +-
 .../rate_limit.py          |   82 +
 .../request_context.py     |    4 +-
 app/models/__init__.py     |    2 +
 app/models/entities.py     |  207 +-
 .../__init__.py            |    1 +
 .../channels.py            |  102 +
 .../service.py             |  186 +
 app/providers/contracts.py |   77 +
 app/providers/manual.py    |   17 +
 app/providers/mock_v2.py   |  118 +
 app/providers/razorpay.py  |  201 +
 app/providers/registry.py  |   10 +-
 .../resolution.py          |   54 +
 app/repositories/base.py   |   10 -
 .../inventory.py           |   10 -
 app/schemas/failed_jobs.py |   52 +
 app/schemas/inventory.py   |   12 +
 app/schemas/orders.py      |   20 +
 .../payment_config.py      |   19 +
 app/schemas/shipments.py   |   37 +-
 app/schemas/tenancy.py     |    3 +-
 app/schemas/webhooks.py    |   84 +
 app/services/auth.py       |    7 +-
 .../failed_jobs.py         |   12 +-
 .../inbound_payments.py    |  118 +
 app/services/inventory.py  |   14 +-
 app/services/orders.py     |    9 +
 .../outbound_webhooks.py   |  121 +
 .../payment_config.py      |   64 +
 .../payment_events.py      |   42 +
 .../payment_expiry.py      |   59 +
 ...yment_reconciliation.py |  303 +
 .../payment_sessions.py    |  155 +
 .../payment_timing.py      |   21 +
 app/services/payments.py   |   17 +-
 .../provider_events.py     |  181 +
 .../refund_intents.py      |   74 +
 app/services/refunds.py    |  232 +
 app/services/shipping.py   |  101 +-
 app/services/sse.py        |  270 +
 app/services/tenancy.py    |   24 +-
 app/services/tracking.py   |  116 +
 .../webhook_delivery.py    |  208 +
 app/tests/conftest.py      |   24 +-
 app/tests/legacy_orders.py |   23 +
 ...est_cancel_hardening.py |    7 +
 .../test_consumers.py      |   39 +-
 .../test_e2e_runner.py     |   69 +
 ...test_failed_jobs_api.py |  249 +
 ...st_failure_scenarios.py |   20 +-
 ...st_inbound_migration.py |   57 +
 ...est_inbound_payments.py |  195 +
 ...ntegration_carryover.py |   35 +
 ...est_inventory_atomic.py |   21 +-
 app/tests/test_mock_v2.py  |  102 +
 ...otification_channels.py |  100 +
 ...tification_migration.py |   78 +
 ...otification_postgres.py |   62 +
 .../test_notifications.py  |  158 +
 .../test_order_recovery.py |    9 +-
 .../test_order_state.py    |   12 +-
 .../test_order_timeline.py |  136 +
 ...nd_webhook_migration.py |   80 +
 ...st_outbound_webhooks.py |  218 +
 ...t_part1_verification.py |  254 +
 .../test_payment_config.py |  160 +
 ...ent_config_migration.py |   74 +
 .../test_payment_expiry.py |  185 +
 ...yment_reconciliation.py |  343 +
 ...test_payment_reviews.py |  159 +
 ...cheduler_concurrency.py |  287 +
 ...est_payment_sessions.py |  253 +
 ...t_sessions_migration.py |   78 +
 ...test_phase3b_quality.py |  184 +
 ...postgres_reliability.py |   84 +-
 ...est_prefix_migration.py |   81 +
 .../test_principal_auth.py |    2 +-
 ...test_provider_events.py |  307 +
 ...test_provider_inputs.py |   17 +
 .../test_providers.py      |   31 +-
 ...test_public_tracking.py |  200 +
 .../test_ratelimit.py      |  356 +
 app/tests/test_razorpay.py |  189 +
 ...pt_tenant_uniqueness.py |  108 +
 ...nciliation_migration.py |   77 +
 ...fund_claim_migration.py |  112 +
 ...est_refund_migration.py |   91 +
 .../test_refund_worker.py  |  462 +
 ...st_reliability_edges.py |    4 +-
 ..._remaining_carryover.py |  133 +
 ...t_settings_hardening.py |   12 +-
 ...t_shipping_lifecycle.py |  214 +
 ...t_shipping_migration.py |   94 +
 app/tests/test_sse.py      |  406 +
 app/tests/test_ssrf.py     |  129 +
 ...st_tenancy_migration.py |    9 +-
 ...est_tenant_isolation.py |   79 +-
 ...t_timeline_migration.py |  105 +
 ...est_tracking_logging.py |   22 +
 ...t_tracking_migration.py |   68 +
 ...est_webhook_delivery.py |  177 +
 ...ok_delivery_postgres.py |   86 +
 app/workers/celery_app.py  |   21 +
 app/workers/tasks.py       |  158 +-
 docker-compose.yml         |   17 +
 docs/openapi.json          | 6595 +
 ...hase-3b-cleanup-e29.txt |   18 +
 ...phase-3b-docker-e2e.txt |   79 +
 ...3b-legacy-migration.txt |   68 +
 .../phase-3b-report.md     | 1632 +
 pytest.ini                 |    1 +
 requirements.txt           |    1 +
 scripts/check_coverage.py  |   50 +
 .../e2e_abuse_inventory.py |  236 +
 .../e2e_integrations.py    |  253 +
 scripts/e2e_payments.py    |  522 +
 scripts/e2e_reliability.py |  114 +-
 ..._seller_integrations.py |  741 +
 scripts/e2e_tenancy.py     |    4 +-
 scripts/export_openapi.py  |   38 +
 ...verify_rate_timeline.py |  155 +
 scripts/webhook_sink.py    |   70 +
 166 files changed, 23858 insertions(+), 466 deletions(-)
```

Commits before the final report-only commit:

```text
a9abc48 test(e2e): verify complete phase 3b Docker workflows
bf02c7c docs: finalize phase 3b documentation and verification report
ce87002 chore(quality): enforce coverage and refresh API documentation
e5ee852 feat(recovery): expose private owner and platform failed-job APIs
79de3db feat(sse): stream scoped order timelines with bounded leases
c77c2d5 docs: finalize phase 3b documentation and verification report
581470b feat(tracking): add signed revocable public order links
4f3b5aa feat(notifications): add durable private notifications and dev tools
99f6cb6 docs: fix Ruff formatting in Phase 3B report
721d62a docs: finalize phase 3b documentation and verification report
5570b95 feat(webhooks): add safe durable seller deliveries
722e6f4 feat(shipping): add tenant manual lifecycle and COD delivery
3d8e2c1 docs: add phase 3b part 1 report
db98ac4 test(integrations): verify full Part 1 acceptance
d33935c test(payments): prove scheduler races and lock-free provider polling
122cb3b feat(payments): reconcile provider outcomes with durable polling leases
e00a961 feat(payments): expire unpaid orders after the capture grace deadline
90d9c47 fix(webhooks): scope receipt uniqueness to each tenant
153b0e2 fix(payments): share capture grace deadline semantics
5bdbea8 fix(payments): bound unmatched notice retries by receipt age
9bcc9a5 docs: add phase 3b part 1 report
6cb581c feat(refunds): execute durable asynchronous compensation outside transactions
f726b58 feat(payments): consume verified provider events and journal compensation
09413d8 feat(webhooks): persist verified inbound payment events
f07a44e docs: add phase 3b part 1 report
9ef0307 feat(payments): phase session creation and add COD fulfillment
72033a7 feat(providers): add mock v2 and tenant payment resolution
78ada7d feat(providers): add verified Razorpay payment adapter
7585220 feat(providers): define detached payment session and webhook contracts
f43531b feat(payments): encrypt and isolate tenant payment settings
e4deece docs: add phase 3b part 1 report
1bb5fc1 feat(events): persist order timelines and transactional status intents
b721647 feat(security): add shared rate limits and registration controls
fbc43ce docs: add phase 3b part 1 report
4ef2ff7 fix(tenancy): finish inventory isolation and key prefix carry-over
1800ebd docs: add phase 3b integrations documentation and verification report
6eb6b04 fix(security): harden login verification and duplicate inventory errors
```

The final report-only commit changes only this document. Its own hash cannot be
embedded without changing that hash; the log above ends at the source/evidence commit.

## B11. Deviations and every modified existing test

All previously documented test changes are retained below, with duplicate entries
consolidated. This session modifies the exact registry test, all four variants of
the resource-isolation matrix, and the SSE disconnect proof. No existing test is
removed or weakened; new files are additional controls.

| Existing test(s) | Reason / retained strictness |
|---|---|
|test_cancel_hardening::test_cancel_refunds_authorized_payment_once|Assert durable refund request, then worker REFUNDED and one audit after repeats.|
|test_consumers::test_duplicate_redelivery_has_one_effect_per_step|Account for provider receipt/four markers.|
|test_failure_scenarios::test_business_failure_compensates_without_retry|Patch v2 session seam.|
|test_failure_scenarios::test_transient_injection_then_retry_has_once_only_effects|Assert durable pending attempt during retry.|
|test_inventory_atomic::test_partial_inventory_reservation_is_never_left_behind|Separate generic status intent from inventory.failed.|
|test_inventory_atomic::test_fulfillment_cancel_compensates_shipment_payment_inventory_atomically|Await actual refund worker.|
|test_order_recovery::test_recovery_reopens_compensated_order_and_finishes_once|Complete async refund before replay.|
|test_order_recovery::test_recovery_scope_suspension_discard_and_missing_order|Separate generic failure intent from zero business intents.|
|test_order_state::test_all_order_transitions|Use owned UUID; exactly three audit/timeline/outbox writes; rejected states unchanged.|
|test_postgres_reliability::test_postgres_concurrent_duplicate_delivery|Drive committed inbox phase after eight-way delivery.|
|test_postgres_reliability::test_postgres_two_tenants_same_sku_have_independent_stock|Separate status/business intents.|
|test_principal_auth::test_api_key_show_once_hash_uniform_auth_and_throttle|Expected prefix12→17.|
|test_providers::test_provider_registry_rejects_unknown_and_nonlocal_mock|Supply required production encryption/manual shipping/console opt-out/tracking key.|
|test_providers::test_provider_calls_persist_references_and_idempotency_keys|Use detached request/result and durable refund worker/key.|
|test_reliability_edges::test_missing_order_and_scheduled_task_controls|Count tenant low-stock rows.|
|test_settings_hardening::test_production_accepts_strong_secret|Supply required production settings.|
|test_tenant_isolation::test_tenant_resource_isolation_matrix|Add atomic adjustments and32 stream/recovery isolation requests across admin/vendor/customer/key.|
|test_tenant_isolation::test_route_registry_has_no_unclassified_routes|Classify all current routes (including nine SSE/recovery additions); exact set and public quotas stay strict.|
|test_provider_events::test_invalid_or_extra_capture_creates_one_durable_refund_without_fulfillment|Move expired fixture1→121s beyond grace.|
|test_provider_events::test_ignored_notification_and_unmatched_refund_are_safe|Require retry inside unmatched window.|
|test_receipt_tenant_uniqueness::test_sqlite_tenant_receipt_migration|Reflect0014 tenant columns.|
|test_receipt_tenant_uniqueness::test_postgres_tenant_receipt_migration|Same historical-schema seed correction and strict preservation/refusal checks.|
|test_tenancy_migration::test_sqlite_tenancy_legacy_roundtrip|Use reconstructible mock setting.|
|test_tenancy_migration::test_postgres_tenancy_legacy_roundtrip|Same reconstructible legacy seed; no weakened assertions.|
|test_reconciliation_migration::test_sqlite_payment_poll_migration / test_postgres_payment_poll_migration|Reflect historical orders before tracking_version.|
|test_refund_claim_migration::test_sqlite_refund_claim_migration / test_postgres_refund_claim_migration|Reflect historical orders.|
|test_refund_migration::test_sqlite_refund_migration / test_postgres_refund_migration|Reflect historical orders.|
|test_shipping_migration::test_sqlite_shipping_migration / test_postgres_shipping_migration|Reflect historical orders.|
|test_payment_scheduler_concurrency::test_postgres_poll_schedulers_skip_locked_fetch_without_locks_and_dedupe_webhook|Submit duplicate raw signed callbacks during blocked fetch; retain NOWAIT/receipt/financial counts.|
|test_sse::test_stream_disconnect_releases_lease_and_closes_reader_sessions|Require a real disconnect after polling, zero checked-out sessions and one lease release;2s safety budget.|

Shared helpers: conftest.workflow total10 and cancel order_stock total20 match
priced items. consume/drain_financial execute owned committed receipts/refunds;
limiter fixtures isolate windows; PG disables stale prepared enum-OID caching.
legacy_orders reflects actual historical columns while preserving enum names,
currency and UUIDs. The fast stream fixture now permits300ms for covered PG reads;
a separate blocked-send proof still requires a30ms response deadline.

Prior Docker adapters await actual refunds, preserve abandoned/captured attempt
counts and use real broker intents for cancellation. E15 is a local session
fixture, not live money. No synthetic state assignment substitutes for those
financial proofs. Operational shipping/webhook/refund/migration assertions remain.

SMTP uncertain outcomes fail without automatic resend to avoid duplicate email;
this can leave missed delivery. Failed-job HTTP deliberately replaces arbitrary
error text/unknown labels with safe generic values, rather than heuristic PII
redaction. Original diagnostics remain private in the database.

Task3B.13 changes no existing test function. It adds two tests to
app/tests/test_e2e_runner.py:

- test_seller_acceptance_inventory_and_independent_signature_check: complete case
  catalog, independent valid signature and tamper rejection.
- test_seller_cleanup_disables_only_its_temporary_platform_principal: owned admin
  becomes inactive; an unrelated platform principal remains active.

AST comparison against main finds18 modified preexisting Phase3B test functions;
all remain named above. The entrypoint retains every earlier E/S assertion and
adds seller cases/restored defaults. E22 uses source timestamps and the complete
event set; transport arrival order is unspecified. Populated buyer fields and a
second tenant strengthen privacy/platform checks. Host11025 uses supported
configuration; no acceptance assertion is weakened.

## B12. Newly discovered issues

Resolved integration findings retain coverage: stale shipment responses,
cancellation vs queued delivery, historical ORM seeds, alias event collisions,
DNS rebinding, SMTP classification/QUIT handling and tracking-log redaction.
Alembic logger fixtures isolate disabled state. SSE fixes cover backlog overtaking,
oversized initial lookback, missing public422/503 privacy headers and blocked sends;
the response deadline bounds transport finalization and shields lease release.
FastAPI0.115.6 needs explicit200 for the custom response. Reserved SSE docs are
deduplicated; CI rejects missing/below90% modules. Platform fixtures are tenant-less;
PG functional reads allow300ms while the independent deadline remains30ms.
Git statistics now include every file without a count limit.

Task3B.13 first encounters a Windows-forbidden host1025 socket binding. Existing
MAILPIT_SMTP_PORT=11025 resolves it without changing container SMTP or assertions.
Review then finds the new runner leaves its platform-admin fixture active. The
new regression fails as expected (B3); code records the owned UUID and retires it
in finally, guarded by expected email/platform flag/tenant-less identity. Credentials
are random per run and never logged; audit identities remain. Both earlier owned
accounts are identified through their proof tenants/owners and disabled with all
unrelated principals unchanged. The final real E29 rerun and refreshed full suites
pass; the application database has4 owned proof principals and0 active (B6).
No product feature/application change or selected blocker remains.

## B13. NOT VERIFIED

- Live Razorpay session/refund/settlement/entitlements, external SMTP/TLS/egress,
  hosted CI, production deployment/HA/soak and real browser/proxy performance.
- Arbitrarily delayed transactions beyond the SSE gap window: refresh snapshots.
  Synchronous slow DB reads may delay cancellation; production timeout/ingress
  tuning is not asserted. Previously accepted reads/sends may finish on revocation.
- SMTP remote exactly-once/crash delivery; unknown outcomes deliberately do not
  auto-resend. Webhook delivery is at least once; receivers must deduplicate.
- Arbitrary production data repair/rollback, free-text PII classification, durable
  simulator maps or automatic signing-secret history/merchant migration. Global
  encryption/tracking key changes still require coordinated operations.

## B14. Reviewer commands (PowerShell)

Run from the repository root. Use a clean test profile if private .env values
target container-only hostnames. Local test PostgreSQL/Redis use5432/6379; Windows
port exclusions may require a private Compose override and matching URLs. Never
overlap pytest runs against one database; only disposable *_test PG names are allowed.

```powershell
git switch phase-3b-integrations
$py='.venv\Scripts\python'
& $py -m ruff check .
& $py -m ruff format --check .
& $py -m mypy app
& $py scripts/export_openapi.py --check
$env:ENVIRONMENT='test'
$env:SHIPPING_PROVIDER='mock'
$env:PAYMENT_PROVIDER='mock'
$env:NOTIFICATIONS_BACKEND='console'
$env:RATE_LIMIT_REDIS_URL=''
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:TEST_REDIS_URL -ErrorAction SilentlyContinue
$env:COVERAGE_FILE='.coverage.review.sqlite'
& $py -m pytest -q --cov=app --cov-report=json:.coverage.review.sqlite.json --cov-report=term-missing --cov-fail-under=90
& $py scripts/check_coverage.py .coverage.review.sqlite.json
$env:TEST_DATABASE_URL='postgresql+psycopg://orders:orders@localhost:5432/orders_test'
$env:TEST_REDIS_URL='redis://localhost:6379/15'
$env:COVERAGE_FILE='.coverage.review.postgres'
& $py -m pytest -q --cov=app --cov-report=json:.coverage.review.postgres.json --cov-report=term-missing --cov-fail-under=90
& $py scripts/check_coverage.py .coverage.review.postgres.json
& $py -m pytest -q -s app/tests/test_sse.py app/tests/test_failed_jobs_api.py app/tests/test_phase3b_quality.py
docker compose --profile dev-tools config --quiet
docker compose build
# Choose an available host SMTP port if Windows blocks1025; container SMTP stays1025.
$env:MAILPIT_SMTP_PORT='11025'
& $py scripts/e2e_integrations.py
# Optional focused diagnostics; not a substitute for the complete run:
& $py scripts/e2e_integrations.py --section sellers --seller-case E27
& $py -m pytest -q -s app/tests/test_part1_verification.py::test_postgres_full_part1_legacy_migration
git diff --check main...HEAD
git log --oneline main..HEAD
```

After intentional route changes run python scripts/export_openapi.py and review
docs/openapi.json. README contains the private Compose setup, dev-tools profile and
receiver/fetch examples. e2e_integrations.py runs all specified Phase3B experiments,
with --section legacy|payments|abuse|sessions|sellers available for diagnosis.
Run only one Docker acceptance process at a time: it temporarily reconfigures and
restarts services, then restores defaults. Test databases remain separate.
No real provider keys belong in test runs.
