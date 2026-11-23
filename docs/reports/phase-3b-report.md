# Phase 3B — implemented through notifications and public tracking

23 November 2026, Asia/Calcutta. Branch: `phase-3b-integrations`.
Tasks 3B.1–3B.9 are implemented locally. This session completes 3B.8 and 3B.9.
Tasks 3B.10–3B.13 remain pending and were not selected for this session.
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

## A7. Public tracking (SSE remains pending)

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

Example public shape (no account/order identifiers are required in the response):

```json
{"store":"Example Store","status":"shipped","timeline":[{"at":"2026-11-23T08:00:00Z","label":"Shipped","status":"shipped"}],"items":[{"name":"Tea","quantity":1}],"shipments":[{"carrier":"Courier","tracking_number":"TRACK123","tracking_url":"https://example.com/track","estimated_delivery":"2026-11-25","events":[{"status":"shipped","at":"2026-11-23T08:00:00Z"}]}]}
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
preparing shipment without exposing raw state/reason strings. Token holders should
treat links as bearer credentials. SSE, streaming connection caps and resume
semantics remain task3B.10 and are not implemented in this scope.

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
| SSE connections |tenant10/IP5 | Reserved only | Task3B.10 |

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
| SSE_TENANT_CONNECTION_LIMIT / SSE_IP_CONNECTION_LIMIT |10 /5, reserved Part 2 |
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

The original Part 1 registry has43 entries; scoped additions bring the current
registry to58 (breakdown below). Every public/public-webhook/tracking entry declares quotas.
No polling/refund journal, failed-jobs or test-only public mock endpoint is added. Twenty existing owned routes retain the four-principal matrix; credential
roles/ownership and public signature/tenant tests supplement it.

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

The shipping/webhook additions bring the prior registry to54 entries; current
notification/tracking additions are listed below. Static export remains3B.12.

| Added method/path under /api/v1 | Auth / policy / errors |
|---|---|
|GET/PATCH /settings/notifications|Owner JWT; active owned tenant; authenticated quota; non-owner403, invalid422|
|POST /orders/{id}/tracking/rotate|Staff JWT/orders:write key; owned lock; authenticated quota; foreign404, customer/read-only403|
|GET /public/track/{token}|PUBLIC_TRACKING;60/min/IP,429 Retry-After,503 closed; generic404, no-store/no-referrer|

Current registry58 entries:13 public,1 payment webhook,1 public tracking,37 tenant,
6 platform. Public policies and exact classification remain enforced. OpenAPI
export and SSE are deferred to their selected future tasks.

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
and removes only its own cache/config. Its Razorpay HTTP case is a local session
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
Notifications, public tracking and the Mailpit/sink profile are implemented. Full
Part 2 Docker acceptance and SSE remain outside the selected tasks.

Notification operations: enable the dev-tools profile and opt into SMTP, or configure
the real relay through private environment values. Inspect journal safe status/error;
do not blindly resend send_outcome_unknown rows. Confirm remote acceptance manually.
Disable notifications through the owner setting before changing delivery policy.

Tracking operations: share only the generated public link, rotate an order link
through its staff/key endpoint, or coordinate TRACKING_TOKEN_SECRET changes across
nodes to invalidate all links. Suspended stores return404 publicly. Preserve the
version column on rollback: rotated versions deliberately refuse downgrade.

## A14. Limitations

SSE, failed-jobs HTTP, final OpenAPI export and production deployment/HA/soak remain unimplemented.
Application ownership controls do not add PostgreSQL RLS. Global email uniqueness
and the existing HTTP low-stock pagination cap remain unchanged. Local memory
limiting and simulator lookup maps are process-local; use shared Redis for real
multi-worker quotas. Remote order creation/refund visibility and exactly-once
settlement depend on the provider and are not live-verified. Known processing
refunds are polled; failed obligations stay visible for investigation, and a later
verified success may still settle them. A sample migration is not arbitrary data
reconciliation. See the single current NOT VERIFIED list in B13.

# PART B — Current verification

## B1. Summary

Reviewed report and git history at99f6cb6 before edits; clean branch.
Baseline:496 passed,37 skipped,284.01s. Tasks3B.1–3B.7 remain reviewed.
Selected tasks3B.8 and3B.9 are DONE, separately committed; none skipped.
Notifications journal once, send outside locks, protect opt-out and recipient privacy.
Dev-tools Mailpit/sink profile passes local SMTP/fail-N smoke checks.
Public tracking has signed revocable links, explicit privacy projection and quotas.
Access-log redaction covers application, Uvicorn and HTTPX logs.
Current final suites/coverage follow; all new modules exceed90%.
Full new-scope Docker e2e/hosted CI/production are not asserted.
Tasks3B.10–3B.13 were not selected. No push, PR, merge or deployment is performed.

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
|3B.10| SSE |Part 2 |— |Out of scope |
|3B.11| Failed-jobs API |Part 2 |— |Out of scope |
|3B.12| OpenAPI/final quality/docs |Part 2 |— |Part 1 gates completed separately |
|3B.13| Full-phase Docker proof |Part 2 |— |Part 1 acceptance subset completed |

## B3. TDD evidence

| Item | Observed red | Green / positive controls |
|---|---|---|
|Unmatched window|5 failed/1 positive control,10.04s |SQLite18/2 skipped; PG20; full383/25 skipped |
|Capture grace|119-second case failed;3 boundary/terminal controls passed,4.05s |SQLite23/2 skipped; PG11; full388/25 |
|Tenant receipts|2 failed/1 skipped,1.94s |SQLite13/2 skipped; PG15; full391/26 |
|Expiry|8 failed/1 skipped,3.86s |Boundary/rollback/batch/refund controls; PG10; full400/27 |
|Reconciliation|1 failed/11 errors,4.90s, missing feature |Initial12 pass; fairness red1 failed1.46s, then20 pass with93% module coverage |
|Reconciliation durability|Additional crash/ownership/migration controls |23 pass/1 skipped; PG27; full423/28 |
|PG proof additions|Positive controls of completed implementation |Both race winners, two poll kinds and expiry during fetch:5 pass7.10s; full423/33 |
|P1 HTTP race strengthening|Positive controls |During payment/refund fetches, two raw signed HTTP callbacks each return200; one financial effect and synthetic receipt:5 pass6.98s |
|P1 verification additions|Positive controls |Five-route secrecy, exact registry,0007 legacy roundtrip;3 pass/1 skipped locally and3 real PG tests |

New proof tests are labelled positive controls rather than fabricated red runs.
Final outputs supersede diagnostic runs. No existing test is removed or weakened;
all compatibility changes are enumerated in B11.

Scoped Part 2 TDD evidence:

| Item | Observed red | Green / controls |
|---|---|---|
|3B.6 shipping|1 failed,8 errors,4.33s: missing manual/settings feature|Initial11 passed; service/manual100%, API94%|
|Cancellation vs queued delivery|1 failed,1 positive control,4.62s on PG|14 PG lifecycle/migration tests pass28.07s|
|Historical schema compatibility|Full run2 failed: new ORM column at0014, new default intercepted old refusal|Reflected old tenant seed / explicit reconstructible default; original assertions retained; PG5 pass15.62s|
|3B.6 final gates|See raw results in B4 for final combined tree|Shipping commit:438 passed,35 skipped,235.41s|
|3B.7 SSRF/API|38 failed,3 errors,26.92s: absent modules|43 passed first implementation;48 edge controls passed34.12s|
|Delivery worker|2 failed4.77s: absent worker|Independent HMAC, retry/dead/replay, suspension/DNS/lease controls|
|IP pinning|1 failed2.69s: request still used hostname|Numeric destination, original Host/SNI, redirect/body-cap assertions now pass|
|Explicit port zero|1 failed5.95s:0 fell back to443|Explicit0 is now rejected; omitted port alone gets scheme default|
|PG HTTP proof / migrations|Positive controls, not fabricated reds|Actual MockTransport HTTP in flight; NOWAIT/peer SKIP LOCKED/rotation; empty roundtrip + operational refusal|

| Selected task / regression | Observed red | Green evidence |
|---|---|---|
|Notifications|9 failed5.00s; absent module|19 passed1 skipped13.05s; module coverage94%|
|SMTP classification|1 failed16 positive controls: SMTPException/OSError inheritance|Handler order corrected; permanent/retry/unknown/QUIT paths checked|
|Public tracking|3 failed1.83s; absent token/route|16 passed1 skipped19.91s; tracking98%, API100%|
|Server/client log privacy|1 failed1.08s; token/query visible|16 targeted tests pass8.86s with Uvicorn/HTTPX filters|
|Full-suite log-capture isolation|Two runs:531 passed/1 failed/40 skipped; Alembic disables existing loggers; INFO-level-only fix insufficient|Migration-then-logging reproduction failed, then2 passed/1 skipped2.59s; fixture activates both loggers, asserts both redacted; fresh full suites below|
|Historical order seeds|Compatibility adaptations for a new column|Reflect the old schema; retain every original money/refusal assertion|
|SMTP NOWAIT/migrations|Positive controls|3 PG tests pass9.27s; no locks during SMTP, peer lease skipped|

## B4. Final quality gates and production coverage

```text
All checks passed!
200 files already formatted
Success: no issues found in 162 source files
SQLite: 532 passed, 40 skipped in 415.73s (0:06:55)
PostgreSQL + Redis: 571 passed, 1 skipped in 613.58s (0:10:13)
```

Production statements only (--cov=app; tests omitted). All new nonempty modules
and changed shipping service/API exceed90%.

| Module | SQLite | PG+Redis |
|---|---:|---:|
|api/v1/endpoints/notification_settings.py|96.30%|96.30%|
|api/v1/endpoints/payment_config.py|100.00%|100.00%|
|api/v1/endpoints/payment_webhooks.py|100.00%|100.00%|
|api/v1/endpoints/public_tracking.py|100.00%|100.00%|
|api/v1/endpoints/shipments.py|93.85%|93.85%|
|api/v1/endpoints/webhook_endpoints.py|100.00%|100.00%|
|core/crypto.py|100.00%|100.00%|
|core/ratelimit.py|99.29%|99.29%|
|core/ssrf.py|100.00%|100.00%|
|domain/timeline.py|100.00%|100.00%|
|middleware/rate_limit.py|100.00%|100.00%|
|notifications/channels.py|93.51%|93.51%|
|notifications/service.py|94.50%|91.74%|
|providers/manual.py|100.00%|100.00%|
|providers/mock_v2.py|92.65%|92.65%|
|providers/razorpay.py|92.68%|92.68%|
|providers/resolution.py|100.00%|100.00%|
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
|services/tracking.py|97.73%|97.73%|
|services/webhook_delivery.py|98.35%|98.35%|
|TOTAL production|97.00%|97.02%|

## B5. Migrations and legacy-data proof

Current head:0021_public_tracking.0008 widens prefixes;0009 backfills synthetic
timeline;0010 stores encrypted configs;0011 adds portable sessions;0012/13/14
add receipt/refund/claim journals;0015 scopes receipt uniqueness;0016 indexes
expiry;0017 adds separate polling checkpoints. No native enum labels change.

The0007 fixture contains a paid order, authorized payment,3×19.99 line,59.97 total,
stock7 available/3 reserved, warehouse/product/user and legacy prefix/hash.
It upgrades, downgrades and upgrades while preserving identity/money/stock/hash
and native enum labels. A separate operational checkpoint then refuses rollback
without data loss. Receipt downgrade also succeeds where reconstructible and
refuses cross-tenant old-key conflicts; receipt/outbox rows remain intact.

```text
0007->HEAD->0007->HEAD: LEGACY ORDER/PAYMENT/ITEM/INVENTORY/PREFIX/HASH AND NATIVE ENUM LABELS PRESERVED
OPERATIONAL CHECKPOINT DOWNGRADE REFUSAL: BEFORE DATA LOSS; FINANCIAL SNAPSHOT UNCHANGED
SHIPPING LEGACY ROUNDTRIP: IDS/80-CHAR CARRIER/120-CHAR TRACKING/MONEY PRESERVED; OPERATIONAL DOWNGRADE REFUSED
OUTBOUND WEBHOOK EMPTY ROUNDTRIP PASS; OPERATIONAL CONFIG/DELIVERY/CLAIM DOWNGRADE REFUSED WITHOUT LOSS
```

Operational local upgrade stopped writers, preserved volumes and applied:
```text
0014_refund_claims ->0015_tenant_receipts ->0016_expiry_index ->0017_payment_polling
```
The reviewed Part 1 operational schema is recorded in B6; it is not the current test head. Other migration tests cover encryption,
session/backfill journals and claim metadata refusal; arbitrary populated
production downgrade is not asserted.

0018 adds tenant provider and URL/date metadata without narrowing legacy
carrier/tracking or changing enums.0019 adds endpoint/delivery/claim tables with
portable statuses. Empty/reconstructible downgrade works; operational shipping or
webhook history/configuration refuses before loss. Current full PG suite also
reruns the0007→head→0007→head financial/stock/hash/native-enum preservation test.
The operational Docker database was not upgraded/rerun for this selected scope;
its retained Part 1 schema/runtime below is reviewed evidence, not current-head proof.

0020 adds notification setting/journal/leases;0021 adds tracking versions. Empty
roundtrips succeed. Notification history/disabled preference and rotated tracking
versions refuse rollback without deleting operational state. Historical order seeds
use reflected version-specific columns. Current full PG reruns0007→head→0007→head.

Fresh SQLite + PostgreSQL legacy-data proof (both dialects; repeated markers condensed):

```text
0007->HEAD->0007->HEAD: LEGACY ORDER/PAYMENT/ITEM/INVENTORY/PREFIX/HASH AND NATIVE ENUM LABELS PRESERVED
OPERATIONAL CHECKPOINT DOWNGRADE REFUSAL: BEFORE DATA LOSS; FINANCIAL SNAPSHOT UNCHANGED
NOTIFICATION LEGACY ROUNDTRIP PASS; OPERATIONAL JOURNAL/SETTING DOWNGRADE REFUSED
TRACKING LEGACY ROUNDTRIP PASS; ROTATED VERSION DOWNGRADE REFUSED; MONEY/REVOCATION PRESERVED
7 passed in 31.32s
```

## B6. Reviewed Part 1 Docker proof and runtime

These retained outputs predate3B.6/3B.7. They are not a current-tree Docker or
operational0019 deployment claim; new-scope Docker checks remain unverified.

Real local PostgreSQL, RabbitMQ, Redis, API, prefork worker, scheduler and relay
run the cases below. The runner preserves existing volumes, creates unique proof
rows and uses a temporary isolated Redis quota service. No live Razorpay HTTP or
money is used: E15 explicitly relabels a local session fixture for the public
Razorpay signature endpoint; E16/E17 use configured-secret mock signatures inside
the worker and the same durable journal/relay/consumer/refund pipeline.

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
PASS session subset: pending checkout, durable attempt, cancellation and stock release
PASS E18: COD fulfillment without session; cancellation releases stock and shipment
PASS selected Part 1 acceptance section: all
```

E16 uses actual beat scheduling and a tiny configured window/grace; no manual
state assignment creates expiry. E28 uses real isolated Redis with smaller
configured limits and fresh logical quota state, checks headers/XFF/key isolation,
then restores defaults. E30 mixes30 orders and15 adjustments and checks accepted
deltas, stock/reservations and exact audits. E1–E14 and the existing S5/S6/S10
cases are all rerun, including broker/worker interruption and recovery.
E20 pauses the real shipment queue consumer to cancel at PAID, then restores it
and independently retains the fulfillment-cancellation control. Both require
three durable attempts, exactly one refund success/audit/intent and restored stock.

```text
RESTORED api {"auto_complete": true, "refund_failures": 0, "window": 1800, "grace": 120, "expiry_interval": 30.0, "reconcile_after": 60, "reconcile_interval": 60.0, "failure_injection": false, "shared_rate_backend": true, "rate_backend_is_default": true, "encryption_configured": false}
RESTORED worker {"auto_complete": true, "refund_failures": 0, "window": 1800, "grace": 120, "expiry_interval": 30.0, "reconcile_after": 60, "reconcile_interval": 60.0, "failure_injection": false, "shared_rate_backend": true, "rate_backend_is_default": true, "encryption_configured": false}
RESTORED scheduler {"auto_complete": true, "refund_failures": 0, "window": 1800, "grace": 120, "expiry_interval": 30.0, "reconcile_after": 60, "reconcile_interval": 60.0, "failure_injection": false, "shared_rate_backend": true, "rate_backend_is_default": true, "encryption_configured": false}
0017_payment_polling (head)
INFO [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO [alembic.runtime.migration] Will assume transactional DDL.
-> celery@a1caeaf1cf1a: OK
 pong

1 node online.
NAME IMAGE COMMAND SERVICE CREATED STATUS PORTS
event-driven-order-orchestration-system-api-1 event-driven-order-orchestration-system-api "uvicorn app.main:ap…" api 2 minutes ago Up 2 minutes 0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
event-driven-order-orchestration-system-migrate-1 event-driven-order-orchestration-system-migrate "alembic upgrade head" migrate About an hour ago Exited (0) 2 minutes ago
event-driven-order-orchestration-system-outbox-relay-1 event-driven-order-orchestration-system-outbox-relay "python -m app.worke…" outbox-relay 10 minutes ago Up 9 minutes 8000/tcp
event-driven-order-orchestration-system-postgres-1 postgres:16-alpine "docker-entrypoint.s…" postgres 2 months ago Up 2 hours (healthy) 0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
event-driven-order-orchestration-system-rabbitmq-1 rabbitmq:3.13-management-alpine "docker-entrypoint.s…" rabbitmq 2 months ago Up 17 minutes (healthy) 0.0.0.0:5672->5672/tcp, [::]:5672->5672/tcp, 0.0.0.0:15672->15672/tcp, [::]:15672->15672/tcp
event-driven-order-orchestration-system-redis-1 redis:7-alpine "docker-entrypoint.s…" redis 2 months ago Up 2 hours 0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
event-driven-order-orchestration-system-scheduler-1 event-driven-order-orchestration-system-scheduler "celery -A app.worke…" scheduler 6 minutes ago Up 6 minutes 8000/tcp
event-driven-order-orchestration-system-worker-1 event-driven-order-orchestration-system-worker "celery -A app.worke…" worker 3 minutes ago Up 3 minutes 8000/tcp
```

The temporary quota container is removed; normal shared quota counters are not
cleared. Temporary overrides/keys are unlinked and only the fixture's own dummy
config is removed. Operational proof orders/journals remain for review.

Scoped Part 2 qualification: the outputs above are the reviewed Part 1 Docker
run. They were not rerun after3B.6/3B.7, and the old operational0017 head is not a
claim of deployment at0019. E19/E22/E23/E24 and full current-tree Docker regressions
remain NOT VERIFIED;3B.13 was not selected. The scoped dev-tools smoke is verified
below. This
scope's API, HTTP transport, migration and concurrency proofs use test databases
and mocked external network transports, as required by the test isolation rule.

Scoped3B.8 dev-tools smoke (separate from full task3B.13 e2e):

```text
PASS dev-tools: Mailpit UI and local SMTP accepted one synthetic message
PASS dev-tools: sink recorded three requests, failed twice then accepted, invalid config/body cap rejected
```

Only Mailpit/sink started, using loopback18025/11025/18090. Both stopped afterward;
no application services/volumes changed by that smoke. Full E25/E26 are not run.
For final database tests, PostgreSQL/Redis had stopped across the session pause.
They were restarted without deleting volumes; Windows excluded6379, so an ignored
Compose port override exposed Redis on loopback16379. No default Compose port was
changed. Test database remains orders_test; Redis logical DB15 is used.

After final suites and migration proof, PostgreSQL/Redis were stopped again to
restore their original stopped state; volumes were preserved and the ignored
Redis override file was removed. Mailpit/sink remain stopped. No app deployment
or operational application-database migration was performed.

## B7. Concurrency, lock and isolation proofs

```text
EXPIRY/CAPTURE WINNER=CAPTURE; ONE TRANSITION; STOCK CONSERVED; NO DOUBLE REFUND
EXPIRY/CAPTURE WINNER=EXPIRY; ONE TRANSITION; STOCK CONSERVED; NO DOUBLE REFUND
POLL PAYMENT: PEER SKIP LOCKED; ORDER/INVENTORY/PAYMENT/CLAIM NOWAIT DURING FETCH; REAL WEBHOOK DEDUPED
POLL REFUND: PEER SKIP LOCKED; ORDER/INVENTORY/PAYMENT/CLAIM NOWAIT DURING FETCH; REAL WEBHOOK DEDUPED
EXPIRY DURING PROVIDER POLL: LATE TRUTH JOURNALED; ONE REFUND; STOCK RESTORED
TWO EXPIRY SCHEDULERS: SKIP LOCKED; TWO ORDERS EXPIRED ONCE; STOCK CONSERVED
CONCURRENT VERIFIED RECEIPTS: ONE JOURNAL ROW AND ONE INTENT
CONCURRENT CAPTURES: ONE PAID TRANSITION AND ONE FULFILLMENT INTENT
CONCURRENT CAPTURES: ONE PAID TRANSITION AND ONE FULFILLMENT INTENT
REFUND PROVIDER IN FLIGHT: ORDER, REFUND AND INVENTORY NOWAIT PASS; ONE CLAIM/EFFECT
```

These use separate real PostgreSQL sessions/threads. Expiry/capture transactions
straddle a controlled grace boundary; the order lock admits one winner, preserves
stock and creates at most one refund/provider submission. Twin expiry schedulers
skip locked work. Poll peers skip an order while the first lease is still
uncommitted, then another session NOWAIT-locks order/inventory/payment/claim rows
during the provider call. A real webhook finishes during that fetch; the later
synthetic receipt causes no second financial effect. Expiry during a fetch still
journals the late financial truth. Original session/refund NOWAIT and crash/lease/
callback tests remain green. The reviewed Part 1 route registry and80-case ownership matrix
are supplemented by credential roles, two-tenant receipt and checkpoint controls.

Scoped Part 2 proof:

```text
OUTBOUND HTTP IN FLIGHT: TENANT/ENDPOINT/DELIVERY NOWAIT AND OWNER ROTATION PASS; PEER SKIP LOCKED; ONE SEND
```

PG uses separate sessions/threads: a peer skips an uncommitted endpoint claim,
then tenant/endpoint/delivery NOWAIT and owner rotation succeed during actual
HTTPX MockTransport execution. A peer also skips the committed live lease. One
attempt/send completes. Requests use pinned numeric IP with original Host/SNI.
SSRF tests cover every requested literal class, mixed private DNS answers, port/
scheme/userinfo/encoding validation, OS resolver errors and forbidden production
bypass; redirects are not followed and response streaming is capped. Privacy
checks cover owner-only management, foreign IDs, secret show-once/rotation/list
masking, all semantic aliases and buyer-PII exclusion. The exact54-route registry
retains the base80-case matrix and adds shipping/JWT/key/foreign ownership plus
eight-route owner/privacy controls.

```text
SMTP IN FLIGHT: ORDER/TENANT/NOTIFICATION NOWAIT PASS; PEER LEASE SKIP; ONE SEND
```

SMTP is blocked in a separate PG thread while another session NOWAIT-locks the
order, tenant and notification; a peer skips the live lease, with one send. Tracking
tests independently recompute HMAC, exercise all10 status mappings, malformed/
unknown/rotated/suspended links, constant-time43-char comparisons, foreign rotation,
JWT/key scope gates, price opt-in,60/min quotas, outage headers and forbidden fields.
App/Uvicorn/HTTPX log privacy is tested. Exact route registry now58 entries.

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
|SSE/failed-jobs HTTP |Remaining Part 2 |
|RLS/global email uniqueness/HA/monitoring/deployment |Existing limitations, outside Part 1 |

## B10. Changed files and commits

Delivery tree against main, report included (--stat=74,60,8; spacing condensed):
```text
.dockerignore | 1 +
 .env.example | 70 +++++++++++++-
 .github/workflows/ci.yml | 7 ++
 .gitignore | 1 +
 README.md | 126 +++++++++++++++++++++++++-
 alembic/versions/0008_api_key_prefix.py | 25 +++++
 alembic/versions/0009_order_timeline.py | 42 +++++++++
 alembic/versions/0010_payment_config.py | 36 ++++++++
 ...
 151 files changed, 14204 insertions(+), 158 deletions(-)
```
Implementation log before this report-only commit:
```text
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
No push/PR/merge is performed.

## B11. Deviations and every modified existing test

All pre-existing test functions changed against main are listed below. The final
two consumer rows additionally name tests that already existed at this session's
reviewed9bcc9a5 starting point. Shared helpers are listed separately. Added tests
are new controls, not removed/replaced assertions. Financial service assertions
use durable rows and actual workers; broker publisher/relay boundary tests may
still patch send_task legitimately.

| Existing test | Reason / strictness retained |
|---|---|
|test_cancel_hardening::test_cancel_refunds_authorized_payment_once |Assert requested obligation before actual worker, retain REFUNDED and one audit after repeated cancel |
|test_consumers::test_duplicate_redelivery_has_one_effect_per_step |Exactly4 business intents/markers including one provider receipt instead of3; preserve one payment/shipment, stock,4 status intents and correlation |
|test_failure_scenarios::test_business_failure_compensates_without_retry |Patch v2 session injection seam; preserve exact failure/stock/audit behavior |
|test_failure_scenarios::test_transient_injection_then_retry_has_once_only_effects |Assert one durable initiated attempt/pending state during retry; preserve exact marker and once-only completion |
|test_inventory_atomic::test_partial_inventory_reservation_is_never_left_behind |Separate generic status intent from the exact inventory.failed count; stock checks remain |
|test_inventory_atomic::test_fulfillment_cancel_compensates_shipment_payment_inventory_atomically |Strict refund request/intent before actual worker; retain shipment status/event/audit, final refund, stock and duplicate cancel |
|test_order_recovery::test_recovery_reopens_compensated_order_and_finishes_once |Complete actual async refund before recovery; retain refund/recovery and once-only fulfillment checks |
|test_order_recovery::test_recovery_scope_suspension_discard_and_missing_order |Separate one generic failure status intent from zero business intents; all ownership/recovery assertions retained |
|test_order_state::test_all_order_transitions |Owned mock UUID and exactly3 audit/timeline/outbox writes; invalid transitions still have no effect |
|test_postgres_reliability::test_postgres_concurrent_duplicate_delivery |Drive new committed inbox phase after eight-way delivery; retain exact payment/shipment/stock counts |
|test_postgres_reliability::test_postgres_two_tenants_same_sku_have_independent_stock |Separate4 status intents from4 business intents per tenant; retain ownership/stock strictness |
|test_principal_auth::test_api_key_show_once_hash_uniform_auth_and_throttle |Expected prefix12→17 only; secrecy/hash/uniform/throttle controls retained |
|test_providers::test_provider_registry_rejects_unknown_and_nonlocal_mock |Supply required encryption key and production manual shipping; retain provider/permission rejections |
|test_providers::test_provider_calls_persist_references_and_idempotency_keys |Detached refund result/request/worker; exact references, status and call list with durable key retained |
|test_reliability_edges::test_missing_order_and_scheduled_task_controls |Tenant count replaces cross-store low-stock SKU aggregation; missing-order/scheduler controls retained |
|test_settings_hardening::test_production_accepts_strong_secret |Supply required key and production manual shipping; retain strong-secret acceptance |
|test_tenant_isolation::test_tenant_resource_isolation_matrix |Add owned/foreign adjustment checks, retain all previous role/resource assertions |
|test_tenant_isolation::test_route_registry_has_no_unclassified_routes |Classify payment/shipping/seller-webhook routes; preserve exact set equality and public quotas |
|test_provider_events::test_invalid_or_extra_capture_creates_one_durable_refund_without_fulfillment |Expired fixture changes1→121 seconds to remain beyond default grace; identical late/refund assertions |
|test_provider_events::test_ignored_notification_and_unmatched_refund_are_safe |Missing refund within the new window must retry, not silently process; existing nonmutation/other guards retained |

The newly added scheduler test
`test_payment_scheduler_concurrency::test_postgres_poll_schedulers_skip_locked_fetch_without_locks_and_dedupe_webhook`
is further strengthened in P1 to submit duplicate raw signed HTTP callbacks
during each blocked fetch, retaining every NOWAIT, receipt and financial count.

Shared changes: conftest.workflow total10 and test_cancel_hardening.order_stock
total20 match priced items; consume/drain_financial drive only owned committed
mock receipts/refunds through real workers. Limiter fixtures isolate windows;
the destructive PG test engine disables prepared enum-OID caching. Legacy seeds
use reflected old-schema columns where current ORM fields did not exist.

Docker compatibility changes are equally strict: shipment failure awaits the actual
refund; exhausted session replay requires exactly one abandoned and one captured
numbered attempt plus one shipment; cancellation publishes real committed intents
through the broker while relay is stopped, including refund.requested, then checks
the same stock/refund/shipment results. No synthetic assignment fakes financial
state. The E15 setup is explicitly a local provider-session fixture, not remote
session creation. Isolated temporary Redis preserves normal abuse counters;
DB sidecar ignore rules prevent accidental packaging. No native enum label or live money call is introduced. Scoped Part 2 endpoints
are documented in A12.

Scoped Part 2 additionally modifies these shared migration fixtures; all original
roundtrip/data/refusal assertions remain unchanged:

| Existing test | Reason |
|---|---|
|test_receipt_tenant_uniqueness::test_sqlite_tenant_receipt_migration|Reflect0014 tenant columns rather than insert the new shipping field before it exists|
|test_receipt_tenant_uniqueness::test_postgres_tenant_receipt_migration|Same shared historical seed correction|
|test_tenancy_migration::test_sqlite_tenancy_legacy_roundtrip|Seed second tenant with reconstructible mock setting so the original multi-tenant refusal is still exercised|
|test_tenancy_migration::test_postgres_tenancy_legacy_roundtrip|Same shared seed; no weakened assertion|

The existing provider/strong-secret tests now explicitly use production manual
shipping (required by the selected rule); their rejection/acceptance assertions
remain strict. The existing registry test covers every current authenticated and public route.
New lifecycle, SSRF, delivery and concurrency tests are additional controls.
Reusing carrier/tracking columns preserves legacy widths; no duplicate/narrowing
columns were introduced. DELETE is soft to preserve delivery audit history. Each
failed attempt counts toward auto-disable; replay resets attempts but not event ID.
Only3B.6–3B.9 are implemented from Part2; tests call no real external network.

Current-scope existing-test changes (assertions remain equally strict):

| Existing tests / shared helper | Reason |
|---|---|
|test_providers::test_provider_registry_rejects_unknown_and_nonlocal_mock|Accepted production config explicitly opts out of SMTP and supplies a strong tracking key|
|test_settings_hardening::test_production_accepts_strong_secret|Same newly required settings; retains secret validation intent|
|test_tenant_isolation::test_route_registry_has_no_unclassified_routes|Adds notification/rotation routes and PUBLIC_TRACKING category with quota checks|
|test_reconciliation_migration::test_sqlite_payment_poll_migration / test_postgres_payment_poll_migration|Reflect historical orders before tracking_version exists; original payment/checkpoint assertions retained|
|test_refund_claim_migration::test_sqlite_refund_claim_migration / test_postgres_refund_claim_migration|Same historical insert correction; retains all refund/claim downgrade refusals|
|test_refund_migration::test_sqlite_refund_migration / test_postgres_refund_migration|Same correction; retains exact original payment/refund preservation assertions|
|test_shipping_migration::test_sqlite_shipping_migration / test_postgres_shipping_migration|Same correction; retains80/120-character legacy data and operational refusal checks|

Shared legacy_orders helper reflects actual columns and preserves original enum
names/currency defaults/UUIDs. No existing test is removed or weakened. SMTP crash
outcomes are conservatively failed, not retried without a remote acceptance lookup;
this tradeoff avoids duplicate email at the cost of possible missing delivery.

## B12. Newly discovered issues

Resolved selected-scope findings: stale shipment relationship in the immediate
prepaid response; cancellation racing an unconsumed delivery; legacy migration
fixtures using today's ORM before new columns; semantic status aliases colliding
with generic subscriptions without distinct IDs; and DNS validation/connect
rebinding, closed with numeric pinning and original Host/SNI. Earlier resolved
financial/fairness issues remain covered by the current full suites.

No selected blocker remains. Webhook delivery is at least once, not a remote
exactly-once guarantee. In-flight sends cannot be recalled after suspension,
deletion, URL change or secret rotation. DNS timing and production routing need
operator control; the local insecure override is forbidden non-locally. Body
privacy is an allowlist, not free-text PII classification. Remaining verification
limits are explicitly B13; no unrequested integration surfaces are added.

Resolved in this scope: SMTPException also inheriting OSError, QUIT errors after
acceptance, historical ORM seeds referencing a not-yet-existing tracking column,
and raw bearer links in Uvicorn/HTTPX access logging. PostgreSQL verification was
restarted after the full SQLite suite exposed disabled logger state left by
Alembic fileConfig in the new log test. An initial INFO-level-only change did not
fix it. A migration-then-logging test reproduced the failure; explicit logger
activation/propagation and INFO capture fix it without weakening assertions.
Both final suites collect this corrected fixture. Verification was also
restarted after stopped services/Windows-reserved Redis port were identified; the
obsolete process was terminated and final checks use the restored test services.
No selected implementation blocker remains.

## B13. NOT VERIFIED

- Live Razorpay money/session/refund/settlement and remote guarantees; optional
  live test mode is skipped. Reviewed official contract ledger remains B8.
- Current-tree full Docker e2e (including E19/E22–E26), operational migration to0021,
  hosted CI, production deployment/HA/soak and real external SMTP/TLS/egress.
  Dev-tools local SMTP/sink smoke is verified separately; no real external test calls.
- SMTP exactly-once/crash-delivery guarantees: an unknown outcome is deliberately
  failed without resend and may need manual investigation. Previously claimed
  sends may complete after opt-out/suspension.
- Unselected3B.10–3B.13: SSE, failed-jobs HTTP, OpenAPI export/final hygiene and
  full-phase Docker proof. No claims of complete Part2 implementation.
- Arbitrary production data repair/rollback, free-text PII classification, simulator
  restart durability or automatic merchant/signing-secret-history migration.
  Tracking key changes must be coordinated across nodes; previously accepted reads
  may finish while rotation commits. Tested sample roundtrips/refusals remain verified.

## B14. Reviewer commands (PowerShell)

If Windows reserves6379, recreate the same test-only port override before the PG
commands below (otherwise start postgres/redis normally and use6379 in TEST_REDIS_URL):

```powershell
@'
services:
  redis:
    ports: !override
      - "127.0.0.1:16379:6379"
'@ | Set-Content -LiteralPath .coverage-p89-redis-compose.yaml
docker compose -f docker-compose.yml -f .coverage-p89-redis-compose.yaml up -d postgres redis
```

The commands below assume those test services are running. Full-stack Docker
commands are reviewer instructions, not current-tree acceptance evidence.

```powershell
git switch phase-3b-integrations
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .
.venv\Scripts\mypy app
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:TEST_REDIS_URL -ErrorAction SilentlyContinue
$env:COVERAGE_FILE='.coverage.review.sqlite'
.venv\Scripts\python -m pytest -q --cov=app --cov-report=term-missing
$env:TEST_DATABASE_URL='postgresql+psycopg://orders:orders@localhost:5432/orders_test'
$env:TEST_REDIS_URL='redis://localhost:16379/15' # test-only Windows port override described in B6
$env:COVERAGE_FILE='.coverage.review.postgres'
.venv\Scripts\python -m pytest -q --cov=app --cov-report=term-missing
.venv\Scripts\python -m pytest -q -s app/tests/test_payment_scheduler_concurrency.py
.venv\Scripts\python -m pytest -q -s app/tests/test_part1_verification.py
.venv\Scripts\python -m pytest -q -s app/tests/test_shipping_lifecycle.py app/tests/test_shipping_migration.py
.venv\Scripts\python -m pytest -q -s app/tests/test_webhook_delivery_postgres.py app/tests/test_outbound_webhook_migration.py
.venv\Scripts\python -m pytest -q app/tests/test_ssrf.py app/tests/test_outbound_webhooks.py app/tests/test_webhook_delivery.py
docker compose stop api worker scheduler outbox-relay
docker compose build
docker compose run --rm migrate
docker compose up -d --no-build
.venv\Scripts\python scripts/e2e_integrations.py
docker compose exec -T worker celery -A app.workers.celery_app inspect ping --timeout=15
docker compose ps -a
git diff --check main...HEAD
git log --oneline main..HEAD
```

Never overlap pytest runs against one database. Migration cases use disposable
test schemas. The current acceptance entrypoint covers the reviewed Part1 subset;
full Part2 Docker experiments remain3B.13. --section supports targeted reruns.
Keep the Redis override for Docker startup on this Windows host; deferred full-stack
commands may need matching overrides. Use no real provider credentials in fixtures.

Additional selected-scope checks (no overlapping pytest on one DB):

```powershell
.venv\Scripts\python -m pytest -q app/tests/test_notifications.py app/tests/test_notification_channels.py
.venv\Scripts\python -m pytest -q -s app/tests/test_notification_postgres.py app/tests/test_notification_migration.py
.venv\Scripts\python -m pytest -q app/tests/test_public_tracking.py app/tests/test_tracking_logging.py
.venv\Scripts\python -m pytest -q -s app/tests/test_tracking_migration.py
docker compose --profile dev-tools config --quiet
```
