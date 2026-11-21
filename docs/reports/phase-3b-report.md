# Phase 3B — Part 1 cumulative report

21 November 2026, Asia/Calcutta. Branch `phase-3b-integrations`.
**Current continuation completes only 3B.5 saga changes4,5,6:** verified inbound
payment webhooks, the payment.provider_event consumer, and durable asynchronous
refunds. 3B.1–3B.4 and the provider/session/COD scope were already complete.
Each selected task has its own green implementation commit:09413d8,f726b58,
6cb581c. Expiry/reconciliation schedulers (steps7/8) and Part2 remain outside
scope. The earlier step9 lock-release proof is retained and expanded for refunds.
Historical verification snapshots remain below; current results are identified
as the saga4–6 continuation. No selected task is skipped or blocked.

# PART A — Implemented architecture and operations

## A1. Async payment saga

The session, verified webhook and asynchronous refund paths are implemented. Intake accepts prepaid (default) or COD.
Default prepaid requests preserve legacy fingerprints. A non-default method is
included in the fingerprint, so changing it conflicts with the same request key.
Existing replay returns before configuration checks and creates no new intent.

```mermaid
stateDiagram-v2
    CREATED --> INVENTORY_RESERVED: reservation
    INVENTORY_RESERVED --> PAYMENT_PENDING: prepaid intent committed
    PAYMENT_PENDING --> PAID: verified matching unexpired capture
    PAID --> FULFILLMENT_REQUESTED: shipment
    INVENTORY_RESERVED --> FULFILLMENT_REQUESTED: COD only
    PAYMENT_PENDING --> FAILED: declined session
    PAYMENT_PENDING --> CANCELLED: cancel releases stock
    PAYMENT_PENDING --> PAYMENT_PENDING: mismatch journals refund
    PAID --> CANCELLED: stock release and refund request
    FULFILLMENT_REQUESTED --> CANCELLED: cancel shipment, release stock, request refund
    CANCELLED --> CANCELLED: late capture journals refund
    FAILED --> FAILED: late capture journals refund
    note right of PAYMENT_PENDING: Expiry scheduler remains step7
```

```mermaid
sequenceDiagram
    participant W as Worker
    participant DB as Database
    participant P as Detached provider
    W->>DB: Lock order, create attempt, transition pending, set expiry
    W->>DB: COMMIT and release locks
    W->>P: create_payment_session(snapshot, attempt, key)
    P-->>W: Session or typed failure
    W->>DB: Lock order again and check state, reference and marker
    W->>DB: Store eligible result and final marker, COMMIT
```

Reservation emits payment.requested. Legacy inventory.reserved messages remain
accepted for existing queued work. A retry resumes the durable pending attempt,
keeping its UUID, key and expiry. Initial keys are orderUUID:payment:1. Recovery
after a compensated failure creates a new numbered attempt. The newest pending
attempt is selected when resuming. The original event marker is written only in
the final transaction. Concurrent delivery rechecks that marker under the order
lock. Cancellation during provider I/O discards the result. Declines release stock
and fail with PAYMENT_SESSION_FAILED, preserving PAYMENT_DECLINED for mock injection.

Mock auto-completion signs and parses a synthetic capture, then stages the same
inbound receipt and outbox intent used by HTTP ingestion. The independent consumer
locks the owned order and applies financial effects in its own transaction. It preserves the existing
authorized native payment status while exposing provider_status=captured. COD
creates an initiated/pending_collection payment and shipment without a session.
Only COD permits the direct inventory_reserved→fulfillment_requested edge.
Delivered status notifications collect COD once without another transition.
COD cancellation releases stock and cancels its shipment without refunding.

POST /api/v1/webhooks/payments/{provider}/{webhook_public_id} streams at most64KiB.
It resolves the seller configuration, checks raw-body HMAC before parsing JSON,
and atomically stores a sanitized receipt plus payment.provider_event intent.
Duplicate provider/event-ID deliveries return200 without another intent. Unknown
configuration returns404, invalid signature/envelope401, oversized body413, and
declared IP plus public-ID quotas return429. Valid callbacks remain accepted after
configuration deactivation so previously accepted money can still be accounted for.

The consumer matches provider references within the receipt's authoritative tenant,
locks ORDER before RECEIPT/REFUND, then rechecks receipt status. Exact, unexpired
captures pay a pending order once. Amount/currency mismatch preserves the pending
session and journals an actual-charge refund; terminal/expired captures journal
LATE_PAYMENT, and distinct extra captures journal DUPLICATE_PAYMENT. Each charge
keeps its actual amount/currency and payment ID. Repeated charge IDs are no-ops.
payment_failed is nonterminal and keeps reserved stock; a later capture may succeed.
Refund callbacks verify amount/currency/reference and apply terminal effects once.
Primary payment summaries use numbered session attempts, so an extra refunded
charge cannot replace the buyer's pending/captured payment summary.

```mermaid
sequenceDiagram
    participant P as Payment provider
    participant API as Webhook API
    participant DB as Database
    participant W as Consumer
    P->>API: Raw body and signature/event-ID headers
    API->>API: Enforce cap, HMAC, parse and sanitize
    API->>DB: INSERT receipt and payment.provider_event outbox
    API->>DB: COMMIT
    API-->>P: 200 accepted (duplicates also 200)
    W->>DB: Lock owned order then receipt; recheck dedupe
    W->>DB: Capture or refund intent, marker and receipt status
    W->>DB: COMMIT; relay continues fulfillment/refund
```

Scheduled expiry and synthetic reconciliation remain steps7/8 and are not
implemented here. The existing capture handler already checks expiry under the
order lock. Part2 supplies the full shipping lifecycle.

## A2. Provider architecture

The detached PaymentProviderV2 contract defines session creation, signed webhook
parsing, refund, payment-status fetch, refund fetch and deterministic refund lookup. SessionOrder and
PaymentAttempt are immutable inputs without ORM relationships or buyer fields.
SessionResult contains a provider order ID, safe checkout data and app expiry.
ProviderEvent contains normalized IDs/type/integer amount/currency and allowlisted
raw event metadata. ProviderDeclined is business rejection and ProviderUnavailable
is transient, including outcome-unknown timeouts. The phased session path releases
its transaction before provider HTTP. Its real PostgreSQL NOWAIT proof is B7.

Active tenant-owned configuration selects Razorpay and decrypts only its key
secret. Otherwise only local/development/test with explicit ALLOW_MOCK_PROVIDERS
selects mock v2. Unconfigured prepaid intake returns422 payment_not_configured.
COD bypasses payment-provider resolution. Suspended tenants remain blocked.

Razorpay uses httpx with a fixed HTTPS API base, Basic authentication,10s timeout
and3s connect timeout. Currency support is intentionally INR only. Decimal amounts
convert exactly to integer paise. Receipts are32-character payment UUID hex and
notes contain only order_id/tenant_id. Every session call first fetches receipts
and verifies the exact receipt, amount, currency and ownership notes. No universal
Orders idempotency header is assumed. Refunds first search receipts, then use the
documented X-Refund-Idempotency header with a deterministic32-hex key and same body.
Provider bodies, credentials and transport exception text are excluded from errors.

Signed parsing compares raw-body HMAC-SHA256 hex in constant time before JSON
parsing, limits bodies to64KiB, requires the event ID and strips buyer/secret fields.
payment.captured and order.paid normalize to captured, payment.failed to failed.
refund.processed/failed normalize accordingly. refund.created normalizes to
processed only when its entity status is processed. B8 records every remote detail.

Mock v2 generates deterministic sessions and signed captures, preserves failure
injection and supports N transient refund failures. Simulated capture/refund state
survives adapter re-resolution in one process, protected by locks. Process restart
and multiple-process simulator state are outside that guarantee. Production-like
environments forbid MOCK_PAYMENT_AUTO_COMPLETE. Legacy mock authorize/refund
helpers remain for direct legacy callers/tests. Compensation services now journal
refunds for detached v2 execution. No real provider refund runs inside legacy helpers.

Secrets are encrypted with Fernet through CredentialVault. Non-local settings
require valid CREDENTIALS_ENCRYPTION_KEY. Local writes also require a key and
return503 credentials_unavailable when absent. MultiFernet accepts a comma-separated
ring, newest first, enabling staged rotation. Only ciphertext is stored. Owner JWT
settings responses reveal the public key ID's last four characters, public webhook
identifier/URL, mode and active flag. The webhook URL routes signature-verified callbacks to this tenant's journal.

## A3. Refunds and reconciliation

Cancellation, SHIPMENT_FAILED, RETRIES_EXHAUSTED, late and mismatched captures
insert refunds plus refund.requested outbox intents in the same transaction as
compensation/state changes. COD never refunds. Uncharged initiated attempts are
closed as abandoned; only captured/authorized money creates an obligation.
Cancelling FULFILLMENT_REQUESTED cancels its shipment with ShipmentEvent and audit,
releases stock and journals the refund atomically. Refund execution is independent.
Technical order recovery refuses refund_pending until every owed refund succeeds.

process_refund(refund_id) locks ORDER then REFUND, increments durable attempts,
records a claim token/lease and commits. Provider resolution/decryption uses the
original payment provider, including deactivated Razorpay configuration. No ORM
session or row lock crosses provider I/O. The detached key is paymentUUID:refund.
An unknown outcome is looked up by deterministic receipt before resubmission;
a known refund reference uses fetch_refund. Razorpay also sends the same documented
idempotency key/body. Mock failure injection uses durable attempt numbers across
prefork workers, while simulator lookup maps remain process-local.

The final transaction re-locks the owned order/refund, checks claim ownership and
provider reference, and commits success, Payment REFUNDED, audit and refund.succeeded
once. A verified processed callback wins over stale worker success/failure.
Pending/created results retain processing, never falsely mark money refunded, and
await a provider callback or explicit retry; scheduled reconciliation is step8.
Zero-value obligations complete locally without a provider call/reference.

Transient provider errors use Celery backoff with a durable maximum of
TASK_MAX_RETRIES+1 attempts. Lease waits do not consume that provider budget.
Permanent rejection/exhaustion writes failed, a static sanitized last_error,
refund_failed audit and one owned failed_jobs row. Success can still arrive later
by verified callback. No plaintext transport/provider exception is persisted.

```mermaid
sequenceDiagram
    participant C as Compensation transaction
    participant DB as Database
    participant W as Refund worker
    participant P as Detached provider
    C->>DB: State/stock/shipment effects + refund + refund.requested
    C->>DB: COMMIT
    W->>DB: Lock order/refund; claim lease, attempts++; COMMIT
    W->>P: Lookup after unknown outcome, otherwise refund/fetch
    P-->>W: Processed, pending or typed error
    W->>DB: Re-lock, claim/reference guards, success/failure effects; COMMIT
    Note over W,DB: Crash after remote success leaves recoverable lease/key
```

Exactly-once local effects are tested under duplicate delivery, callback races and
commit failure. Exactly-once remote money movement still depends on the provider's
idempotency/receipt visibility guarantees and is not claimed as live-verified.
Expiry plus late-payment scheduling/reconciliation diagrams await steps7/8.

## A4. Shipping and COD

Part 2 — full shipping lifecycle not yet implemented. COD intake/fulfillment and collection foundation are described in A1.

## A5. Outbound webhooks

Part 2 — not yet implemented. No SSRF or seller delivery subsystem is added.

## A6. Notifications

Part 2 — not yet implemented. No notification journal, templates or SMTP services.

## A7. Public tracking and SSE

Part 2 — not yet implemented. No public tracking or streaming surface is added.

## A8. Authentication and abuse controls

Rate limiting and registration controls (3B.2) are implemented below. Unknown-email
login performs bcrypt verification against a random dummy hash precomputed once
per process, using the same hashing settings as real users. Known users verify
against their stored hash. Both invalid branches retain identical generic403
`Invalid email or password`. This removes skipped password work, not every timing
difference or brute-force risk.

New API keys retain format `oos_live_<8 random alphanumerics>_<secret>` and store
the entire17-character public part as prefix. The column supports24 characters.
SHA256 covers the suffix after the stored prefix, including its leading separator.
Legacy12-character prefixes/hashes are unchanged. Authentication queries both
possible prefixes and compares each candidate digest using `hmac.compare_digest`;
no-row lookups still perform a dummy comparison. Both formats work even when they
share the same public nonce. Malformed/revoked/expired/wrong keys retain uniform401.
Allocation still retries ten collisions; last-used throttling remains unchanged.


### 3B.2 shared request protection

`RateLimiter.hit(key, limit, window)` returns allowed/limit/remaining/reset/retry_after.
Both backends use fixed windows starting at the first hit. Redis uses one atomic
Lua script (INCR, PTTL, first-hit/missing-TTL PEXPIRE and Redis TIME); memory uses
a lock and expires old windows. The factory serializes cold initialization so all
threads share one backend. Backend reset closes cached Redis clients. Redis keys
contain SHA256 digests of policy/window/identity, never raw email, IP or credentials.
The normalized email/IP pair uses an unambiguous JSON encoding.

| Surface | Default quota | Identity | Backend failure |
|---|---|---|---|
| POST auth/login | 10/60s AND 5/900s | IP; normalized email+IP | Closed,503 |
| POST auth/signup | 10/3600s | IP | Closed,503 |
| POST tenants/register | 5/3600s | IP | Closed,503 |
| Authenticated routes | 600/60s | API key UUID or user UUID; fixed metrics-token identity | Configurable, open by default |
| Docs/OpenAPI/health/ready GET/HEAD | 600/60s | IP | Open |
| Future payment webhook | 600/60s each | IP and webhook_public_id | Closed; policy declared, route absent |
| Future public tracking | 60/60s | IP | Closed; policy declared, route absent |

Successful checks attach X-RateLimit-Limit, X-RateLimit-Remaining and
X-RateLimit-Reset (Unix seconds). Multiple checks expose the last successful policy;
429 exposes the blocking policy and Retry-After. Errors use the existing envelope
and request ID. An unavailable closed backend returns503 with Retry-After:1.
Degradation logs only policy/mode and increments
`rate_limit_degraded_total{policy,mode}`. Configured Redis connection/socket timeout
is0.2s. An unset URL selects memory only locally; non-local missing/invalid Redis
configuration is unavailable, never silently replaced with memory.

TRUSTED_PROXY_COUNT=0 ignores X-Forwarded-For. Positive values select the Nth hop
from the right, validate it, and fall back to the socket peer for malformed/short
chains. Operators must use a trusted sanitizing ingress and block direct access
before enabling forwarded-IP trust. Missing peer identity becomes `unknown`.
Login applies its IP quota before reading a body, caps the body at16KiB and
preserves normal malformed-body validation. Repeated oversized login requests
therefore still reach429.

REGISTRATION_ENABLED=false returns generic403 `registration_closed`. When an
invite is configured, missing/wrong invites also return that generic403; matching
uses hmac.compare_digest. Configured invites must be nonempty. Invite fields are
SecretStr and never persisted or returned. Validation responses strip raw input
and context, including full request dictionaries that could contain passwords.

Atomic-script rationale: [Redis scripting documentation](https://redis.io/docs/latest/develop/programmability/eval-intro/).

## A9. Security and inventory consistency

| Threat | Mitigation | Evidence |
|---|---|---|
| Lost stock updates | PATCH and adjustments lock tenant-owned inventory with FOR UPDATE and refresh after waiting | SQL-shape assertion and real PG mixed race |
| Negative adjustment | Reject422 negative_stock before mutation; rollback | HTTP regression + stock conservation |
| Adjustment actor ambiguity | JWT actor_id; API-key UUID in audit payload, never its secret | HTTP audit regression |
| Cross-tenant mutation | Tenant predicate before scope check; foreign404 | Four-principal matrix |
| Unscoped repository access | Remove generic get/list and inventory escape hatches | Regression guard asserts methods absent |
| Hidden low-stock rows | Tenant and stock keyset pages, no global100-row cap | Two stores with105 low rows each |
| Short key prefix collisions | Full public prefix, legacy-aware digest split, ten retry budget | Compatibility and collision exhaustion tests |
| Login enumeration shortcut | Dummy password verification | Existing timing regression retained |
| Duplicate warehouse/stock failures | Typed409 with rollback | Existing d/e regression retained |
| Concurrent quota bypass | Atomic Redis Lua; locked memory backend; serialized cold factory | Real Redis concurrency and eight simultaneous cold callers |
| Forwarded-IP spoofing | Socket peer by default; explicit trusted hop count | HTTP forwarded-header regression |
| Credentials in validation errors | Remove raw input/context; keep field/type/message | Invalid and incomplete invite/password bodies |
| Limiter outage | Public auth fails closed; configurable authenticated mode; bounded metric labels | HTTP 503/fail-open tests |
| Untrusted status reasons | Only declared reason codes enter events; free text remains private | Buyer/operator text privacy regressions |

Inventory-only writes lock just the inventory row and never subsequently lock an
order. Workflow operations retain order-first, inventory-second ordering. Adjustments
apply `new=old+delta` under that lock, write `inventory_adjusted` audit data (old/new,
delta, reason and actor), then commit together. Zero delta, invalid input or negative
result is rejected; values above PostgreSQL INTEGER maximum return422
`stock_out_of_range`. Failures rollback. Absolute PATCH still means an operator
sets available stock; it now serializes with reservation/adjustment writes.

Low-stock maintenance iterates tenants ordered by UUID in100-row keyset pages,
then each tenant's qualifying inventory ordered by UUID in100-row keyset pages.
It logs count plus tenant_id, without SKU aggregation across stores. Its task result
is a tenant-ID→count mapping. The HTTP low-stock endpoint keeps its existing100-row
cap; this task removes the scheduler's global cap, not that separate API limitation.

### Payment ingress and refund threats

| Threat | Implemented controls | Proof |
|---|---|---|
| Forged/altered callback or JSON parse before verification | Raw HMAC comparison before JSON, uniform401, no stored receipt | invalid/missing/changed-body tests and Docker HTTP fixture |
| Replayed transport or equivalent captured event | Unique provider/event ID plus owned charge-ID and state guards | PG receipt/capture concurrency and duplicate intent counts |
| Cross-tenant money mutation | Config-resolved tenant, receipt ownership and scoped provider reference lookup | foreign/missing reference tests; existing tenant matrix retained |
| Buyer/secret fields in journals/errors | Normalized allowlist only, static worker errors, detached DTOs | secrecy/parser tests and failed-job exhaustion control |
| Callback abuse/large body | IP/public-ID quotas and streamed64KiB cap | registry/quota,413 and invalid-persistence tests |
| Double refund after crash or concurrent worker | Durable unique key, claims/lease, lookup-before-reissue, callback/claim guards | PG NOWAIT/duplicate claim, crash and callback race controls |

Merchant configuration deactivation does not erase obligations or reject valid
signed callbacks. The public-ID URL is a routing identifier, not authentication.

## A10. Data model and events

```mermaid
erDiagram
    TENANTS ||--o{ API_KEYS : owns
    TENANTS ||--o{ INVENTORY : owns
    USERS o|--o{ AUDIT_LOGS : actor
    API_KEYS {
        UUID tenant_id FK
        string key_prefix "24-character column; new prefix17 or legacy12"
        string key_hash "SHA256 of suffix after stored prefix"
    }
    INVENTORY {
        UUID tenant_id FK
        int available_quantity "locked before adjustment"
    }
    AUDIT_LOGS {
        UUID actor_id FK
        UUID entity_id "inventory UUID for adjustment audit"
        JSON payload "old/new/delta/reason; optional API key UUID"
    }
```

Revision0008_key_prefix widens one column. Revision0009_timeline adds the timeline
table below. Neither changes native PostgreSQL enum labels/types. Adjustment audits
contain no key plaintext.


### 3B.3 transactional timeline and notification intent

```mermaid
erDiagram
    TENANTS ||--o{ ORDER_TIMELINE : owns
    ORDERS ||--o{ ORDER_TIMELINE : records
    ORDER_TIMELINE {
        BIGINT id PK "SQLite Integer variant; autoincrement"
        UUID tenant_id FK
        UUID order_id FK
        string from_status "nullable"
        string to_status
        text reason "nullable; private"
        timestamp created_at "database default; timezone-aware"
    }
```

Indexes are `(tenant_id,id)` and `(order_id,id)`. Order creation records one
initial row from null to created in the same transaction as the existing audit and
order.created outbox intent; it does not add an initial status_changed event.
Every valid `transition` and `reopen_failed_order` appends one timeline row and one
`order.status_changed` outbox intent in the caller's transaction, alongside state
and audit writes. Invalid transitions and rollback persist none. Replay returns
before writing, so it adds no history or event. Workers propagate incoming
correlation IDs; HTTP transitions fall back to request_id.

| Event | Queue | Exact payload fields | Consumer behavior |
|---|---|---|---|
| order.status_changed | orders | order_id, from, to, reason, tenant_id | Informational; deduplicate/acknowledge; no state change |

Tenant identity comes from the owned Order, never the event/request body. Payloads
exclude buyer fields and secrets. Only INSUFFICIENT_INVENTORY, PAYMENT_DECLINED,
SHIPMENT_FAILED and RETRIES_EXHAUSTED are permitted reason codes; any arbitrary
free text becomes null in the event while remaining in private timeline/audit.
Future reason codes require an explicit allowlist update. Tests patch both
transition entry points to fail if an informational event starts another transition.

Migration0009 backfills one synthetic observation per legacy order: from=null,
to=lower(current status), reason=MIGRATION_BACKFILL, observed at migration time.
It neither invents historical transitions nor publishes backfill events. Downgrade
to0008 refuses before DDL if any operational history exists (non-null from_status
or a reason other than MIGRATION_BACKFILL, including null). Archive history under
an operator retention policy before considering such a downgrade; do not force it.

### Payment configuration and session additions

```mermaid
erDiagram
    TENANTS ||--o{ TENANT_PAYMENT_CONFIGS : owns
    ORDERS ||--o{ PAYMENTS : attempts
    TENANT_PAYMENT_CONFIGS {
        UUID tenant_id FK
        string provider "unique per tenant; razorpay"
        string mode "test or live"
        string key_id "public; API masks to last4"
        text key_secret_enc "Fernet ciphertext"
        text webhook_secret_enc "Fernet ciphertext"
        string webhook_public_id "random URL-safe unique"
        bool is_active
    }
    ORDERS {
        string payment_method "prepaid default or cod"
        timestamp payment_expires_at "nullable"
    }
    PAYMENTS {
        string idempotency_key "nullable unique for legacy compatibility"
        string provider_status "nullable string"
        JSON checkout_payload "safe allowlist only"
    }
```

0010 adds configuration with timestamps and unique(tenant_id,provider). Owner writes
serialize on the tenant row, covering first insert.0011 adds portable session columns
and a unique attempt-key index. Both preserve native enum types/labels and existing
rows. Operational credentials/session data prevents downgrade before DDL. The new
payment.requested event carries the same owned order ID/tenant/correlation metadata
as existing workflow intents and is queued on payments.

### Saga4–6 journals and event catalog

```mermaid
erDiagram
    TENANTS ||--o{ INBOUND_WEBHOOK_EVENTS : owns
    TENANTS ||--o{ REFUNDS : owes
    ORDERS ||--o{ REFUNDS : compensates
    PAYMENTS ||--o{ REFUNDS : refunds
    FAILED_JOBS o|--o{ REFUNDS : terminal_failure
    INBOUND_WEBHOOK_EVENTS {
        UUID id PK
        UUID tenant_id FK
        string provider "unique with provider_event_id"
        string provider_event_id
        string event_type
        JSON payload "normalized allowlist; no raw buyer/secret fields"
        timestamp received_at
        string status "received/processed/ignored"
    }
    REFUNDS {
        UUID id PK
        UUID tenant_id FK
        UUID order_id FK
        UUID payment_id FK
        decimal amount
        string currency
        string reason
        string status "requested/processing/succeeded/failed"
        int attempts
        string provider_refund_id
        string idempotency_key UK
        string last_error "static sanitized label"
        UUID claim_token
        timestamp lease_until
        UUID failed_job_id FK
        string correlation_id
        timestamp created_at
        timestamp updated_at
    }
```

0012_inbound adds receipt uniqueness;0013_refunds adds refund obligations and0014
adds recoverable claims/failure linkage. No native PostgreSQL enum label changes.
payment.provider_event: payments queue, aggregate=receipt UUID, payload=inbound_event_id.
refund.requested: payments queue, aggregate=refund UUID, payload=refund_id/order_id;
the publisher routes it to process_refund(UUID), preserving outbox task identity.
refund.succeeded: payments queue, aggregate=order UUID, payload=refund_id/order_id.
All intents retain owned tenant/correlation metadata and share business commits.

## A11. Configuration

Task1 introduced no settings. Task2 adds the complete configuration reference below;
Redis was already a runtime dependency. Payment settings added in this continuation follow.


| Environment variable | Default |
|---|---|
| RATE_LIMIT_REDIS_URL | unset |
| RATE_LIMIT_FAIL_OPEN | true |
| TRUSTED_PROXY_COUNT | 0 |
| RATE_LIMIT_LOGIN_IP_LIMIT | 10 |
| RATE_LIMIT_LOGIN_IP_WINDOW_SECONDS | 60 |
| RATE_LIMIT_LOGIN_EMAIL_LIMIT | 5 |
| RATE_LIMIT_LOGIN_EMAIL_WINDOW_SECONDS | 900 |
| RATE_LIMIT_REGISTER_LIMIT | 5 |
| RATE_LIMIT_REGISTER_WINDOW_SECONDS | 3600 |
| RATE_LIMIT_SIGNUP_LIMIT | 10 |
| RATE_LIMIT_SIGNUP_WINDOW_SECONDS | 3600 |
| RATE_LIMIT_AUTHENTICATED_LIMIT | 600 |
| RATE_LIMIT_AUTHENTICATED_WINDOW_SECONDS | 60 |
| RATE_LIMIT_PUBLIC_LIMIT | 600 |
| RATE_LIMIT_PUBLIC_WINDOW_SECONDS | 60 |
| RATE_LIMIT_WEBHOOK_LIMIT | 600 |
| RATE_LIMIT_WEBHOOK_WINDOW_SECONDS | 60 |
| RATE_LIMIT_TRACKING_LIMIT | 60 |
| RATE_LIMIT_TRACKING_WINDOW_SECONDS | 60 |
| SSE_TENANT_CONNECTION_LIMIT | 10 |
| SSE_IP_CONNECTION_LIMIT | 5 |
| REGISTRATION_ENABLED | true |
| REGISTRATION_INVITE_CODE | unset |

These are model defaults, not a dump of local secret settings. `.env.example`
selects shared Redis `redis://redis:6379/2` for Compose. The optional invite is
unset by default; configure a nonempty secret out of band. Limits/windows are
positive integers; trusted proxy count is nonnegative. Webhook/tracking policies
and SSE caps are reserved configuration only; their routes/streaming are not added.
CI postgres-checks starts Redis7 and sets TEST_REDIS_URL to its disposable DB15.
Real Redis tests skip with a clear reason when TEST_REDIS_URL is absent.

### Payment configuration reference

| Variable | Default/behavior |
|---|---|
| CREDENTIALS_ENCRYPTION_KEY | Unset locally; required and validated non-locally. Comma-separated Fernet keys, newest first |
| PUBLIC_BASE_URL | http://localhost:8000, used for the seller webhook URL |
| ALLOW_MOCK_PROVIDERS | Model default false; local Compose example explicitly true |
| MOCK_PAYMENT_AUTO_COMPLETE | True locally, false non-locally when unspecified; true forbidden non-locally |
| MOCK_WEBHOOK_SECRET | Local simulator default; configure through a secret store |
| MOCK_REFUND_FAIL_ATTEMPTS | 0, nonnegative integer |
| PAYMENT_WINDOW_SECONDS | 1800, positive integer |
| REFUND_LEASE_SECONDS | 60, positive integer; expired worker claims are reclaimable |

cryptography50.0.2 is now an explicit runtime dependency. Invite/password/key
validation input and context remain excluded from error responses.

## A12. API reference changes so far

| Method/path | Auth | Ownership/policy | Behavior |
|---|---|---|---|
| PATCH /api/v1/inventory/{id} | ADMIN/VENDOR JWT or inventory:write key | Tenant-first404; Authenticated principal quota | Lock/refresh before changing fields |
| POST /api/v1/inventory/{id}/adjustments | Same | Tenant-first404; customer/insufficient scope403 | `{delta:nonzero integer,reason:1..200 chars}` → InventoryRead; negative422; audit |
| POST /api/v1/api-keys | Tenant ADMIN JWT only | Existing key-management scope | New17-character prefix; full key once |
| POST /api/v1/auth/login | Public | IP and normalized email+IP quotas | Same generic invalid403, hash work in both branches |
| POST /api/v1/inventory/warehouses | Staff/key inventory:write | Tenant-owned uniqueness | Duplicate409 warehouse_code_conflict |
| POST /api/v1/inventory | Staff/key inventory:write | Tenant-owned uniqueness | Duplicate409 inventory_conflict |

Payment additions follow below. PUBLIC_WEBHOOK is classified separately; PUBLIC_TRACKING remains Part2. The registry adds the adjustment route to TENANT_SCOPED; the existing
public-route policy requirement is now enforced by the registry regression.

Tasks2/3 ended with39 method/path entries (13 public,20 tenant,6 platform).
The current payment continuation adds three tenant settings entries. Every public entry must declare a quota
policy. The tenant matrix retains20 method/path entries ×4 principal types.

### Payment API additions

| Method/path | Auth/ownership | Result |
|---|---|---|
| PUT /api/v1/settings/payments/razorpay | Active tenant ADMIN owner JWT only | Upsert encrypted secrets; masked key_id, webhook_url/public_id, mode, active |
| GET /api/v1/settings/payments/razorpay | Same | Owned masked configuration, or404 |
| DELETE /api/v1/settings/payments/razorpay | Same | Deactivate owned row,204; missing404 |
| POST /api/v1/orders | Existing tenant order principal | Optional prepaid/cod method; prepaid configuration gate |
| GET/list/cancel order responses | Existing tenant order ownership | payment_method, payment_status, payment_expires_at, checkout while pending prepaid |

Settings reject API keys, vendor/customer JWTs and tenant-less platform admins.
Foreign tenant configurations are absent in the caller's context. All three new
routes are TENANT_SCOPED and use authenticated quotas. The route registry now has
43 method/path entries:13 public,1 public webhook,23 tenant and6 platform. The original80-case tenant
matrix remains, supplemented by settings role/ownership and secrecy tests.

### Verified payment callback

| Method/path | Classification/auth | Responses |
|---|---|---|
| POST /api/v1/webhooks/payments/{provider}/{webhook_public_id} | PUBLIC_WEBHOOK, raw HMAC; IP600/min AND public-ID600/min defaults | 200 accepted/duplicate,404 unknown,401 invalid,413 >64KiB,429 quota |

Only Razorpay is an exposed callback provider. Mock completion stages the same
verified journal internally, without a public test-only ingress route. Receipt and
refund storage are internal; no Part2 failed-jobs/admin API is introduced.

## A13. Operations runbook

Use an inventory:write JWT/key to POST an adjustment with a meaningful reason.
Review `audit_logs` where entity_type=inventory, entity_id is the owned stock UUID,
action=inventory_adjusted. Delta changes available stock only; reservations remain
workflow-owned. Actor_id identifies JWT users; API-key UUID is recorded in payload.

For upgrade, stop application writers and run `alembic upgrade head`, then restart.
Legacy key hashes must not be rewritten: their plaintext is unavailable. Downgrade
to0007 succeeds while all stored prefixes fit12; otherwise it refuses **before DDL**
with a clear message. Do not truncate a prefix/hash to force downgrade. Explicitly
retire the affected rows only under an operator's data-retention policy.

The local stack was migrated/restored; schema width and injection=false are verified
below. Payment-session and credential operations are described below. Durable refunds are described in A3 and the continuation runbook below. Reproduction commands are B14.

For current quota troubleshooting, check the degradation metric/log policy and
mode, Redis reachability, and forwarded-proxy configuration. Never log invite,
email/IP inputs, API keys or Redis credentials. Disable registration or rotate its
invite through configuration and restart application processes. Changing policy
limits should be coordinated across workers sharing the same quota store.

Inspect timeline only through an authorized tenant/order context, for example:
`SELECT * FROM order_timeline WHERE tenant_id=:tenant AND order_id=:order ORDER BY id`.
Use `scripts/verify_rate_timeline.py` against the local mock stack to verify quota
headers, real relay/worker status notifications and idempotent replay. It creates
unique local test data and prints no credentials; it is not the full money E2E.

### Payment operations

Set a Fernet key in the secret store, configure PUBLIC_BASE_URL, then use the tenant
owner JWT to PUT Razorpay key_id/key_secret/webhook_secret/mode. GET verifies only
masked data. Configure the returned webhook URL in the seller's provider dashboard.
DELETE deactivates the row. No credential plaintext is returned after write.

For rotation, deploy new,old keys in that order. CredentialVault.rotate re-encrypts
key_secret_enc and webhook_secret_enc under the first key in a controlled maintenance
transaction. Verify new-key decryption before commit, then retire old keys only after
all rows/processes and backup-retention requirements permit it. Keep secret values
out of commands, logs, reports and source control.

For pending sessions, inspect the owned order/payment row, provider_status and
attempt key. Retry resumes the same attempt. Cancellation releases inventory and
omits checkout. Expiry and scheduled reconciliation require steps7/8; refund execution and callback ingestion are implemented. Local scripts/e2e_integrations.py verifies the selected session/COD
subset and restores actual worker settings in finally. It leaves unique local rows.

### Inbound and refund operations

Subscribe to payment.captured/payment.failed/order.paid and supported refund
events with the same webhook secret saved by the seller. Keep the exact raw body
through proxies. The event-ID header is mandatory. Signature failures store
nothing; inspect only normalized receipt metadata and safe status/error labels.
Deactivation blocks new sessions while preserving signed financial callbacks and
refund access. Key rotation must retain access to the original merchant account;
switching merchants does not migrate existing captured money/refund obligations.
Provider signing-secret retry overlap needs operator coordination; the app stores
one webhook secret per configuration, not a signing-secret history.

For a stuck refund inspect its owned payment/reference/key/status/attempts and
claim lease. Redelivery after lease expiry reuses the durable key and looks up an
unknown result first. A known provider refund ID is fetched, not submitted again.
Do not reset keys, force REFUNDED, or replay an order while a refund remains owed.
Pending provider responses rely on callbacks/manual task redelivery until step8.
Provider/network errors are generic; use provider-side test-mode observability
without copying credentials or buyer fields into application logs.

Stop writers before schema upgrade; preserve data/volumes. Populated inbound/refund
journals or operational claim/failure metadata deliberately block destructive
downgrades before DDL. scripts/e2e_payments.py checks E20 and scoped signed-ingress,
mismatch and late-cancel behavior with mock/fixture money only. Its HTTP capture
relabels a local pending session as a Razorpay fixture; it never creates a remote
Razorpay order. It removes only its own temporary dummy config and restores runtime
settings. Unique demonstration orders/receipts/refunds remain for inspection.

## A14. Limitations

Remaining3B.5 scheduling steps7/8 and the full3B.P1 acceptance experiments remain outside this session. Part2 is not implemented. Timeline IDs are allocation order,
not transaction commit order; future SSE must implement its specified lookback.
Memory quotas are process-local and suitable only for local/development/test.
Use shared Redis for multiple application workers. No row-level database security
is added; timeline isolation uses authoritative tenant ownership and scoped access.
Payment session and refund HTTP run outside database locks; application-only tenancy,
global email uniqueness, historical-data reconciliation and other Phase3A limitations
remain. No full Phase3B or production-readiness claim is made.

# PART B — Verification

## B1. Summary

Historical3B.2/3B.3 continuation: baseline244 passed/11 skipped before edits; 3B.2 and3B.3
completed and separately committed with green gates. Final SQLite287 passed/14
skipped; PostgreSQL+Redis301 passed. Local Docker rate/timeline smoke and0009
migration/downgrade refusal verified. No in-scope task skipped or concrete blocker
remains. The following summary is the historical3B.1 pass, not the current scope:

- Existing branch is retained; merged Phase3A files are confirmed on main.
- Baseline suite passes before edits:238 passed,9 skipped.
- Carry-over3B.1(a–c) is completed; d/e remain from6eb6b04.
- Inventory locking, audited adjustments and four-principal isolation are covered.
- Unscoped helpers are removed; scheduler scans all qualifying owned pages.
- New/legacy key formats, collision exhaustion and safe migration are covered.
- Both SQLite and real PostgreSQL regression results are recorded below.
- Docker schema migration/restoration is verified; acceptance E2E is not rerun.
- No existing assertion is weakened; intentional compatibility updates are listed.
- Remaining tasks are SKIPPED at a clean task boundary under working rule5.

### Historical provider/session continuation

Baseline287 passed/14 skipped before edits. Five implementation commits complete
the selected scope. Local encryption/API isolation, detached contracts, adapter
transport tests, phased retries/cancellation, COD and live subset checks pass.
Current full results and any repeated gates appear in B4. No selected task is skipped.
Earlier B1 evidence is historical.

### Saga4–6 continuation (current)

Read cumulative report and git log first on phase-3b-integrations; baseline:
329 passed,18 skipped in189.53s. Changes4/5/6 complete separately with green
Ruff, format, mypy, SQLite and PostgreSQL+Redis suites at their commit boundaries.
No earlier implementation task is redone. Current final outputs/coverage follow
in B4; scoped Docker results and the observed broker startup recovery are B6.

## B2. Task status

| ID | Task | Status | Commits | Notes |
|---|---|---|---|---|
| 3B.1 | Carry-over fixes(a–e) | DONE previously | 6eb6b04, 4ef2ff7 | Not redone in this continuation |
| 3B.2 | Rate limiting/abuse | DONE | b721647 | Shared Redis, local memory, policies, invite controls |
| 3B.3 | Timeline/status event | DONE | 1bb5fc1 | Transactional history/intents, safe backfill, no-loop consumption |
| 3B.4 | Credentials/configuration | DONE | f43531b,72033a7,9ef0307 | Encrypted settings, owner isolation, resolution and intake gate |
| 3B.5 | Async payments/refunds/COD | PARTIAL | 7585220,78ada7d,72033a7,9ef0307,09413d8,f726b58,6cb581c | Provider part and saga1–6 DONE; steps7/8 schedulers outside scope; step9 lock proof retained/expanded |
| 3B.P1 | Quality/Docker proof | PARTIAL | 4ef2ff7,b721647,1bb5fc1,9ef0307,09413d8,f726b58,6cb581c | Selected gates, PG/Redis, migrations and scoped session/COD/webhook/refund Docker proofs pass; full acceptance/live provider remain unverified |
| 3B.6 | Shipping lifecycle | Part 2 | — | Out of scope |
| 3B.7 | Outbound webhooks | Part 2 | — | Out of scope |
| 3B.8 | Notifications | Part 2 | — | Out of scope |
| 3B.9 | Public tracking | Part 2 | — | Out of scope |
| 3B.10 | SSE | Part 2 | — | Out of scope |
| 3B.11 | Failed-jobs API | Part 2 | — | Out of scope |
| 3B.12 | OpenAPI/final quality/docs | Part 2 | — | Part1 gates recorded separately |
| 3B.13 | Full-phase Docker proof | Part 2 | — | Part1 subset remains outstanding |

### Selected task commits

| Scope | Status | Green commit |
|---|---|---|
| 3B.5 change4: verified HTTP/journal/outbox | DONE | 09413d8 |
| 3B.5 change5: owned consumer and compensation requests | DONE | f726b58 |
| 3B.5 change6: durable asynchronous refund execution | DONE | 6cb581c |
| 3B.5 changes7/8: scheduled expiry/reconciliation | SKIPPED, outside selected scope | — |
| 3B.5 change9 proof | Previously DONE; refund NOWAIT/concurrent-claim proof added | 6cb581c |

3B.P1 remains PARTIAL because the full acceptance matrix/live provider are not
verified, not because any selected implementation task was abandoned.

## B3. TDD evidence

Historical3B.1 evidence (retained):

Before implementation of a/b/c:

```text
test_adjustment_audit_validation_and_patch_lock: assert 404 == 200
test_unscoped_repository_methods_cannot_return: assert not True
test_low_stock_scan_crosses_pages_and_keeps_tenants_separate:
TypeError: list indices must be integers or slices, not str
test_new_and_legacy_key_prefixes_authenticate: 12-character prefix !=17-character prefix
4 failed in 5.17s
```

Initial after run (carry-over/auth/isolation/worker selection):

```text
24 passed in 31.02s
```

Actor follow-up: the key adjustment audit test fails `KeyError: api_key_id`
(`1 failed in1.71s`), then passes in the final collection. Collision exhaustion,
PG mixed-race and migration tests are added as **positive controls** after core
implementation; no invented pre-fix failure is claimed for them. Existing d/e reds
were unknown-email verify call_count0 and duplicate warehouse IntegrityError; their
two-test after run was2 passed in2.99s. Baseline/final full results are separate runs.


### Current continuation: observed failures and corrections

| Run | Observed result | Correction/meaning |
|---|---|---|
| Task2 initial red | 13 failed,1 warning in11.43s | Missing limiter module; expected429/registration403 absent |
| Task2 initial after | 12 passed,1 skipped in20.13s | Initial policy/backend implementation |
| Invite/body regressions | 2 failed in2.53s | Validation echoed invite; repeated oversized login bypassed quota |
| Incomplete registration privacy | 1 failed in5.32s | Missing field echoed full credential dict; strip all raw validation input/context |
| Concurrent cold backend | 1 failed in2.78s | Eight stores instead of one; serialize factory initialization |
| Empty configured invite | 1 failed in2.48s | Settings accepted empty secret; enforce nonempty, then1 passed in0.90s |
| Task2 PG fixture run | 274 passed,2 errors in334.17s | Stale prepared native-enum type OIDs after destructive fixture DDL |
| Task2 final SQLite | 264 passed,13 skipped in248.95s | Green before task2 commit |
| Task2 final PG+Redis | 277 passed in326.97s | Green before task2 commit |
| Task3 initial timeline red | 19 failed in13.51s | Timeline model/behavior absent |
| Task3 migration red | 1 failed in4.81s | Timeline table absent at old head |
| Task3 initial after | 20 passed,1 skipped in16.35s | Timeline/migration implementation |
| Task3 privacy/compatibility | 21 failed,97 passed in10.41s | Three free-text payload leaks;15 old single-write expectations;3 old business-outbox counts |
| Task3 corrected selection | 138 passed,1 skipped in24.36s | Privacy reason policy and precise test compatibility updates |

The test-only PG engine now uses `prepare_threshold=None`: fixture drop/create
changes enum OIDs while pooled prepared statements can retain them. Production
connection configuration is unchanged. This follows [Psycopg prepared statement
configuration](https://www.psycopg.org/psycopg3/docs/advanced/prepare.html).
Once, two SQLite selections were accidentally overlapped and collided on shared
fixture DDL (`no such table: shipments`); that run was discarded and rerun serially.
No test or assertion was disabled. Additional edge/migration/no-loop tests added
after implementation are positive controls, not invented pre-fix failures.

### Payment continuation observed failures and fixes

| Run | Observed result and correction |
|---|---|
| Credential tests before implementation | 8 failed in2.97s; missing crypto/config API |
| Initial configuration selection | 33 passed in18.63s |
| Configuration migration fixture | UUID reflection on SQLite then missing primary-key flag failed; preserve portable UUID types and primary-key metadata; target1 passed in2.11s |
| Configuration full after correction | SQLite296 passed/15 skipped in147.81s, PG+Redis311 passed in294.47s |
| Detached contract | ImportError for missing PaymentAttempt before implementation; after full suite297 passed/15 skipped in138.60s |
| Razorpay adapter | ModuleNotFoundError before implementation; first adapter selection8 passed/1 skipped with82% coverage; added error controls; full311 passed/16 skipped in192.54s and90.99% adapter coverage |
| Mock v2 | 2 failed in2.16s before implementation; after full317 passed/16 skipped in189.64s |
| Saga tests | 4 failed in3.66s before implementation |
| Compatibility selection | 3 failed,114 passed,1 skipped in16.84s; old injection seam and old-head ORM fixture required updates |
| Corrected selection | 11 passed/1 skipped in9.74s |
| New test assertion placement | NameError in a new COD test,1 failed/325 passed/18 skipped in221.73s; moved the hash assertion back into its own test |
| PG migration assertion | 1 failed/342 passed/1 skipped in411.50s; exact Decimal comparison replaces float approximate fixture comparison |
| Fresh-adapter mock refund retry | 1 failed in1.40s; process-wide locked simulation state now survives re-resolution and rejects changed refund amounts |
| Session edge selection | 12 passed/2 skipped in18.70s; additional edge/lock/migration controls are positive controls |

An earlier approval review failed because account usage prevented review completion,
before command execution. After the user's Continue, approved commands resumed.
No approval check was bypassed. Earlier diagnostic runs included code/test updates
while processes were already loaded, so the final source-frozen outputs below
are authoritative. No assertion failure was suppressed or original assertion removed.

### Saga4–6 observed red/green evidence

- Baseline329 passed/18 skipped before edits.
- Inbound red:9 failed10.25s, missing route/model/settings. First implementation
  target1 failed/9 passed9.59s exposed a quota test that reused prior windows;
  resetting only that test's local windows restored the strict lower quota check.
  Step4 full green339/20 SQLite and358/1 PG+Redis before09413d8.
- Consumer red:7 failed5.33s before handler/refund journal. Target7 passed4.90s,
  then11 passed7.42s after positive controls for rollback/foreign/missing/ignored
  references and refunds. Step5 full green352/23 SQLite,374/1 PG+Redis beforef726b58.
- Refund red:4 errors3.05s (no durable compensation request). Initial target
  3 failed/38 passed/3 skipped19.11s exposed the asynchronous test seams/counts;
  next1 failed/38 passed/1 skipped15.55s exposed the old synchronous adapter fake.
  Fixed them minimally; target40 passed/1 skipped19.19s.
- Free-refund red1 failed1.41s proved zero money incorrectly resolved a provider;
  local completion now avoids that call. Failure-audit red1 failed1.56s proved
  missing refund_failed audit; terminal failure now writes it once with the job.
- Added migration preservation/refusal, order-summary, pending/declined/malformed
  provider result, callback race, stale-claim and no-lock tests as positive controls.
  Target40 passed/3 skipped26.11s; claims/consumer target13 passed/3 skipped7.42s.
- Full diagnostic SQLite1 failed/372 passed/25 skipped229.56s and PG2 failed/
  395 passed/1 skipped626.70s identified only old immediate-refund expectation and
  a concurrent workflow driver that omitted the new inbound consumer. Their final
  business assertions remain intact. Focused compatibility controls21 passed/
  1 skipped10.75s. Final full green runs in B4 supersede those diagnostics.
- Four parser positive controls reject signed boolean amounts/unsafe identifiers
  and ignore unsupported/pending-only events, covering security branches rather
  than weakening existing assertions. No provider HTTP test contacts Razorpay.

## B4. Raw outputs and production coverage

Historical3B.1 full outputs (preserved; current results follow):

```text
All checks passed!
112 files already formatted
Success: no issues found in 92 source files
```

```text
........................................................................ [ 28%]
........................................................................ [ 56%]
...............................ssssssss.s............................... [ 84%]
..s...............s....................                                  [100%]

---------- coverage: platform win32, python 3.12.6-final-0 -----------
Name                                Stmts   Miss  Cover   Missing
-----------------------------------------------------------------
app\__init__.py                         0      0   100%
app\api\__init__.py                     0      0   100%
app\api\v1\__init__.py                  0      0   100%
app\api\v1\endpoints\__init__.py        0      0   100%
app\api\v1\endpoints\admin.py          46      0   100%
app\api\v1\endpoints\auth.py           17      0   100%
app\api\v1\endpoints\health.py         33      4    88%   23-25, 35
app\api\v1\endpoints\inventory.py     111      7    94%   27, 44, 70, 107-108, 122, 145
app\api\v1\endpoints\orders.py         30      2    93%   47-49
app\api\v1\endpoints\products.py       27      2    93%   37-38
app\api\v1\endpoints\shipments.py      22      6    73%   29-34
app\api\v1\endpoints\tenancy.py        25      0   100%
app\api\v1\router.py                   11      0   100%
app\auth\dependencies.py               16      0   100%
app\auth\principal.py                  98      4    96%   59, 66, 83, 96
app\auth\security.py                   21      0   100%
app\core\config.py                     46      0   100%
app\core\exceptions.py                 28      0   100%
app\core\logging.py                    10      0   100%
app\db\base.py                         11      0   100%
app\db\session.py                      12      4    67%   14-18
app\domain\__init__.py                  0      0   100%
app\domain\order_state.py              20      0   100%
app\events\contracts.py                20      0   100%
app\events\publisher.py                 8      0   100%
app\main.py                            22      0   100%
app\middleware\request_context.py      25      0   100%
app\models\__init__.py                  2      0   100%
app\models\entities.py                182      0   100%
app\models\enums.py                    37      0   100%
app\observability\metrics.py            6      0   100%
app\providers\__init__.py               0      0   100%
app\providers\contracts.py             40      4    90%   43, 47, 55, 59
app\providers\mock.py                  29      0   100%
app\providers\registry.py              13      0   100%
app\repositories\base.py               11      0   100%
app\repositories\inventory.py          15      0   100%
app\repositories\orders.py             38      2    95%   37, 49
app\repositories\outbox.py             10      0   100%
app\repositories\users.py               7      0   100%
app\schemas\auth.py                    23      0   100%
app\schemas\common.py                  15      0   100%
app\schemas\inventory.py               39      1    97%   40
app\schemas\orders.py                  53      0   100%
app\schemas\products.py                30      1    97%   26
app\schemas\shipments.py                6      0   100%
app\schemas\tenancy.py                 40      0   100%
app\services\auth.py                   39      2    95%   28-30
app\services\catalog.py                53      2    96%   39, 64
app\services\failed_jobs.py            86      1    99%   67
app\services\inventory.py              37      1    97%   37
app\services\orders.py                105      3    97%   35, 100, 135
app\services\payments.py               32      0   100%
app\services\shipping.py               28      0   100%
app\services\tenancy.py                49      0   100%
app\workers\celery_app.py               8      0   100%
app\workers\outbox_relay.py            69      2    97%   30, 90
app\workers\tasks.py                  155      0   100%
-----------------------------------------------------------------
TOTAL                                1916     48    97%
Coverage JSON written to file .coverage-p1-sqlite.json

244 passed, 11 skipped in 97.92s (0:01:37)
```

```text
........................................................................ [ 28%]
........................................................................ [ 56%]
........................................................................ [ 84%]
.......................................                                  [100%]

---------- coverage: platform win32, python 3.12.6-final-0 -----------
Name                                Stmts   Miss  Cover   Missing
-----------------------------------------------------------------
app\__init__.py                         0      0   100%
app\api\__init__.py                     0      0   100%
app\api\v1\__init__.py                  0      0   100%
app\api\v1\endpoints\__init__.py        0      0   100%
app\api\v1\endpoints\admin.py          46      0   100%
app\api\v1\endpoints\auth.py           17      0   100%
app\api\v1\endpoints\health.py         33      4    88%   23-25, 35
app\api\v1\endpoints\inventory.py     111      7    94%   27, 44, 70, 107-108, 122, 145
app\api\v1\endpoints\orders.py         30      2    93%   47-49
app\api\v1\endpoints\products.py       27      2    93%   37-38
app\api\v1\endpoints\shipments.py      22      6    73%   29-34
app\api\v1\endpoints\tenancy.py        25      0   100%
app\api\v1\router.py                   11      0   100%
app\auth\dependencies.py               16      0   100%
app\auth\principal.py                  98      4    96%   59, 66, 83, 96
app\auth\security.py                   21      0   100%
app\core\config.py                     46      0   100%
app\core\exceptions.py                 28      0   100%
app\core\logging.py                    10      0   100%
app\db\base.py                         11      0   100%
app\db\session.py                      12      4    67%   14-18
app\domain\__init__.py                  0      0   100%
app\domain\order_state.py              20      0   100%
app\events\contracts.py                20      0   100%
app\events\publisher.py                 8      0   100%
app\main.py                            22      0   100%
app\middleware\request_context.py      25      0   100%
app\models\__init__.py                  2      0   100%
app\models\entities.py                182      0   100%
app\models\enums.py                    37      0   100%
app\observability\metrics.py            6      0   100%
app\providers\__init__.py               0      0   100%
app\providers\contracts.py             40      4    90%   43, 47, 55, 59
app\providers\mock.py                  29      0   100%
app\providers\registry.py              13      0   100%
app\repositories\base.py               11      0   100%
app\repositories\inventory.py          15      0   100%
app\repositories\orders.py             38      2    95%   37, 49
app\repositories\outbox.py             10      0   100%
app\repositories\users.py               7      0   100%
app\schemas\auth.py                    23      0   100%
app\schemas\common.py                  15      0   100%
app\schemas\inventory.py               39      1    97%   40
app\schemas\orders.py                  53      0   100%
app\schemas\products.py                30      1    97%   26
app\schemas\shipments.py                6      0   100%
app\schemas\tenancy.py                 40      0   100%
app\services\auth.py                   39      2    95%   28-30
app\services\catalog.py                53      2    96%   39, 64
app\services\failed_jobs.py            86      1    99%   67
app\services\inventory.py              37      1    97%   37
app\services\orders.py                105      3    97%   35, 100, 135
app\services\payments.py               32      0   100%
app\services\shipping.py               28      0   100%
app\services\tenancy.py                49      0   100%
app\workers\celery_app.py               8      0   100%
app\workers\outbox_relay.py            69      1    99%   90
app\workers\tasks.py                  155      3    98%   88-90
-----------------------------------------------------------------
TOTAL                                1916     50    97%
Coverage JSON written to file .coverage-p1-postgres.json

255 passed in 129.72s (0:02:09)
```

Overall production line coverage: SQLite **97.49%**, PostgreSQL **97.39%**. Per-module numbers appear in the raw tables.

Tests are omitted by pyproject coverage configuration. No new production module is
introduced (migration coverage is demonstrated separately). Redis backend/tests/CI were not verified in that historical pass; current evidence follows.


### Current continuation final gates and full suites

```text
All checks passed!
120 files already formatted
Success: no issues found in 98 source files
```

SQLite (real Redis tests skipped when URL absent):

```text
........................................................................ [ 23%]
........................................................................ [ 47%]
.....................................................ssssssss.s......... [ 71%]
....................s.s.......................s...............s......... [ 95%]
...........s.                                                            [100%]

---------- coverage: platform win32, python 3.12.6-final-0 -----------
Name                                Stmts   Miss  Cover   Missing
-----------------------------------------------------------------
app\__init__.py                         0      0   100%
app\api\__init__.py                     0      0   100%
app\api\v1\__init__.py                  0      0   100%
app\api\v1\endpoints\__init__.py        0      0   100%
app\api\v1\endpoints\admin.py          46      0   100%
app\api\v1\endpoints\auth.py           17      0   100%
app\api\v1\endpoints\health.py         35      4    89%   24-26, 37
app\api\v1\endpoints\inventory.py     111      7    94%   27, 44, 70, 107-108, 122, 145
app\api\v1\endpoints\orders.py         30      2    93%   47-49
app\api\v1\endpoints\products.py       27      2    93%   37-38
app\api\v1\endpoints\shipments.py      22      6    73%   29-34
app\api\v1\endpoints\tenancy.py        26      0   100%
app\api\v1\router.py                   11      0   100%
app\auth\dependencies.py               16      0   100%
app\auth\principal.py                 108      6    94%   61, 68, 85, 98, 154, 162
app\auth\security.py                   21      0   100%
app\core\config.py                     73      0   100%
app\core\exceptions.py                 28      0   100%
app\core\logging.py                    10      0   100%
app\core\ratelimit.py                 140      1    99%   51
app\db\base.py                         11      0   100%
app\db\session.py                      12      4    67%   14-18
app\domain\__init__.py                  0      0   100%
app\domain\order_state.py              23      0   100%
app\domain\timeline.py                 14      0   100%
app\events\contracts.py                21      0   100%
app\events\publisher.py                 8      0   100%
app\main.py                            30      0   100%
app\middleware\rate_limit.py           52      0   100%
app\middleware\request_context.py      25      0   100%
app\models\__init__.py                  2      0   100%
app\models\entities.py                192      0   100%
app\models\enums.py                    37      0   100%
app\observability\metrics.py            6      0   100%
app\providers\__init__.py               0      0   100%
app\providers\contracts.py             40      4    90%   43, 47, 55, 59
app\providers\mock.py                  29      0   100%
app\providers\registry.py              13      0   100%
app\repositories\base.py               11      0   100%
app\repositories\inventory.py          15      0   100%
app\repositories\orders.py             38      2    95%   37, 49
app\repositories\outbox.py             10      0   100%
app\repositories\users.py               7      0   100%
app\schemas\auth.py                    23      0   100%
app\schemas\common.py                  15      0   100%
app\schemas\inventory.py               39      1    97%   40
app\schemas\orders.py                  53      0   100%
app\schemas\products.py                30      1    97%   26
app\schemas\shipments.py                6      0   100%
app\schemas\tenancy.py                 41      0   100%
app\services\auth.py                   39      2    95%   28-30
app\services\catalog.py                53      2    96%   39, 64
app\services\failed_jobs.py            86      1    99%   67
app\services\inventory.py              37      1    97%   37
app\services\orders.py                107      3    97%   36, 102, 137
app\services\payments.py               32      0   100%
app\services\shipping.py               28      0   100%
app\services\tenancy.py                61      0   100%
app\workers\celery_app.py               8      0   100%
app\workers\outbox_relay.py            69      2    97%   30, 90
app\workers\tasks.py                  158      0   100%
-----------------------------------------------------------------
TOTAL                                2202     51    98%
Coverage JSON written to file .coverage-s23-task3-sqlite.json

287 passed, 14 skipped in 244.31s (0:04:04)
```

Real PostgreSQL and Redis:

```text
........................................................................ [ 23%]
........................................................................ [ 47%]
........................................................................ [ 71%]
........................................................................ [ 95%]
.............                                                            [100%]

---------- coverage: platform win32, python 3.12.6-final-0 -----------
Name                                Stmts   Miss  Cover   Missing
-----------------------------------------------------------------
app\__init__.py                         0      0   100%
app\api\__init__.py                     0      0   100%
app\api\v1\__init__.py                  0      0   100%
app\api\v1\endpoints\__init__.py        0      0   100%
app\api\v1\endpoints\admin.py          46      0   100%
app\api\v1\endpoints\auth.py           17      0   100%
app\api\v1\endpoints\health.py         35      4    89%   24-26, 37
app\api\v1\endpoints\inventory.py     111      7    94%   27, 44, 70, 107-108, 122, 145
app\api\v1\endpoints\orders.py         30      2    93%   47-49
app\api\v1\endpoints\products.py       27      2    93%   37-38
app\api\v1\endpoints\shipments.py      22      6    73%   29-34
app\api\v1\endpoints\tenancy.py        26      0   100%
app\api\v1\router.py                   11      0   100%
app\auth\dependencies.py               16      0   100%
app\auth\principal.py                 108      6    94%   61, 68, 85, 98, 154, 162
app\auth\security.py                   21      0   100%
app\core\config.py                     73      0   100%
app\core\exceptions.py                 28      0   100%
app\core\logging.py                    10      0   100%
app\core\ratelimit.py                 140      1    99%   51
app\db\base.py                         11      0   100%
app\db\session.py                      12      4    67%   14-18
app\domain\__init__.py                  0      0   100%
app\domain\order_state.py              23      0   100%
app\domain\timeline.py                 14      0   100%
app\events\contracts.py                21      0   100%
app\events\publisher.py                 8      0   100%
app\main.py                            30      0   100%
app\middleware\rate_limit.py           52      0   100%
app\middleware\request_context.py      25      0   100%
app\models\__init__.py                  2      0   100%
app\models\entities.py                192      0   100%
app\models\enums.py                    37      0   100%
app\observability\metrics.py            6      0   100%
app\providers\__init__.py               0      0   100%
app\providers\contracts.py             40      4    90%   43, 47, 55, 59
app\providers\mock.py                  29      0   100%
app\providers\registry.py              13      0   100%
app\repositories\base.py               11      0   100%
app\repositories\inventory.py          15      0   100%
app\repositories\orders.py             38      2    95%   37, 49
app\repositories\outbox.py             10      0   100%
app\repositories\users.py               7      0   100%
app\schemas\auth.py                    23      0   100%
app\schemas\common.py                  15      0   100%
app\schemas\inventory.py               39      1    97%   40
app\schemas\orders.py                  53      0   100%
app\schemas\products.py                30      1    97%   26
app\schemas\shipments.py                6      0   100%
app\schemas\tenancy.py                 41      0   100%
app\services\auth.py                   39      2    95%   28-30
app\services\catalog.py                53      2    96%   39, 64
app\services\failed_jobs.py            86      1    99%   67
app\services\inventory.py              37      1    97%   37
app\services\orders.py                107      3    97%   36, 102, 137
app\services\payments.py               32      0   100%
app\services\shipping.py               28      0   100%
app\services\tenancy.py                61      0   100%
app\workers\celery_app.py               8      0   100%
app\workers\outbox_relay.py            69      1    99%   90
app\workers\tasks.py                  158      3    98%   88-90
-----------------------------------------------------------------
TOTAL                                2202     53    98%
Coverage JSON written to file .coverage-s23-task3-postgres.json

301 passed in 378.94s (0:06:18)
```

Production line coverage: SQLite **97.68%** (2151/2202), PostgreSQL **97.59%**
(2149/2202). New core ratelimit module **99.29%**, middleware **100%**, timeline
helper **100%**. Test files are omitted by existing coverage configuration;
migrations are exercised explicitly. These are observed local runs, not hosted CI.

Final report-only commit gates were rerun after the report update:

```text
All checks passed!
120 files already formatted
Success: no issues found in 98 source files
TOTAL                                2202     51    98%
287 passed, 14 skipped in 177.21s (0:02:57)
```

Coverage remains97.68%. PostgreSQL+Redis was not repeated for this documentation-only
commit; its final implementation run above remains301 passed.

### Historical provider/session continuation final outputs

```text
All checks passed!
139 files already formatted
Success: no issues found in 114 source files
```

SQLite production-only coverage and tests:

```text
........................................................................ [ 20%]
........................................................................ [ 41%]
..................................................................s..... [ 62%]
......s.s..ssssssss.s..............................s.s..............s... [ 82%]
....................s...............s....................s.              [100%]

---------- coverage: platform win32, python 3.12.6-final-0 -----------
Name                                     Stmts   Miss  Cover   Missing
----------------------------------------------------------------------
app\__init__.py                              0      0   100%
app\api\__init__.py                          0      0   100%
app\api\v1\__init__.py                       0      0   100%
app\api\v1\endpoints\__init__.py             0      0   100%
app\api\v1\endpoints\admin.py               46      0   100%
app\api\v1\endpoints\auth.py                17      0   100%
app\api\v1\endpoints\health.py              35      4    89%   24-26, 37
app\api\v1\endpoints\inventory.py          111      7    94%   27, 44, 70, 107-108, 122, 145
app\api\v1\endpoints\orders.py              30      2    93%   47-49
app\api\v1\endpoints\payment_config.py      24      0   100%
app\api\v1\endpoints\products.py            27      2    93%   37-38
app\api\v1\endpoints\shipments.py           22      6    73%   29-34
app\api\v1\endpoints\tenancy.py             26      0   100%
app\api\v1\router.py                        12      0   100%
app\auth\dependencies.py                    16      0   100%
app\auth\principal.py                      108      6    94%   61, 68, 85, 98, 154, 162
app\auth\security.py                        21      0   100%
app\core\config.py                          96      0   100%
app\core\crypto.py                          31      0   100%
app\core\exceptions.py                      28      0   100%
app\core\logging.py                         10      0   100%
app\core\ratelimit.py                      140      1    99%   51
app\db\base.py                              11      0   100%
app\db\session.py                           12      4    67%   14-18
app\domain\__init__.py                       0      0   100%
app\domain\order_state.py                   24      0   100%
app\domain\timeline.py                      14      0   100%
app\events\contracts.py                     22      0   100%
app\events\publisher.py                      8      0   100%
app\main.py                                 30      0   100%
app\middleware\rate_limit.py                52      0   100%
app\middleware\request_context.py           25      0   100%
app\models\__init__.py                       2      0   100%
app\models\entities.py                     219      0   100%
app\models\enums.py                         37      0   100%
app\observability\metrics.py                 6      0   100%
app\providers\__init__.py                    0      0   100%
app\providers\contracts.py                  85      4    95%   117, 121, 129, 133
app\providers\mock.py                       29      6    79%   27-36
app\providers\mock_v2.py                    62      0   100%
app\providers\razorpay.py                  111     10    91%   27, 39, 44, 47, 129, 143, 163, 165, 167-168
app\providers\registry.py                   13      0   100%
app\providers\resolution.py                 22      0   100%
app\repositories\base.py                    11      0   100%
app\repositories\inventory.py               15      0   100%
app\repositories\orders.py                  38      2    95%   37, 49
app\repositories\outbox.py                  10      0   100%
app\repositories\users.py                    7      0   100%
app\schemas\auth.py                         23      0   100%
app\schemas\common.py                       15      0   100%
app\schemas\inventory.py                    39      1    97%   40
app\schemas\orders.py                       67      0   100%
app\schemas\payment_config.py               14      0   100%
app\schemas\products.py                     30      1    97%   26
app\schemas\shipments.py                     6      0   100%
app\schemas\tenancy.py                      41      0   100%
app\services\auth.py                        39      2    95%   28-30
app\services\catalog.py                     53      2    96%   39, 64
app\services\failed_jobs.py                 86      1    99%   67
app\services\inventory.py                   37      1    97%   37
app\services\orders.py                     112      3    97%   37, 106, 144
app\services\payment_config.py              37      3    92%   62-64
app\services\payment_events.py              21      0   100%
app\services\payment_sessions.py            92      3    97%   109, 133-134
app\services\payments.py                    32      3    91%   32-34
app\services\shipping.py                    28      0   100%
app\services\tenancy.py                     61      0   100%
app\workers\celery_app.py                    8      0   100%
app\workers\outbox_relay.py                 69      2    97%   30, 90
app\workers\tasks.py                       168      2    99%   178-179
----------------------------------------------------------------------
TOTAL                                     2743     78    97%
Coverage JSON written to file .coverage-s45-commit-result.json

329 passed, 18 skipped in 165.84s (0:02:45)
```

PostgreSQL and Redis:

```text
........................................................................ [ 20%]
........................................................................ [ 41%]
........................................................................ [ 62%]
....................................................................s... [ 82%]
...........................................................              [100%]

---------- coverage: platform win32, python 3.12.6-final-0 -----------
Name                                     Stmts   Miss  Cover   Missing
----------------------------------------------------------------------
app\__init__.py                              0      0   100%
app\api\__init__.py                          0      0   100%
app\api\v1\__init__.py                       0      0   100%
app\api\v1\endpoints\__init__.py             0      0   100%
app\api\v1\endpoints\admin.py               46      0   100%
app\api\v1\endpoints\auth.py                17      0   100%
app\api\v1\endpoints\health.py              35      4    89%   24-26, 37
app\api\v1\endpoints\inventory.py          111      7    94%   27, 44, 70, 107-108, 122, 145
app\api\v1\endpoints\orders.py              30      2    93%   47-49
app\api\v1\endpoints\payment_config.py      24      0   100%
app\api\v1\endpoints\products.py            27      2    93%   37-38
app\api\v1\endpoints\shipments.py           22      6    73%   29-34
app\api\v1\endpoints\tenancy.py             26      0   100%
app\api\v1\router.py                        12      0   100%
app\auth\dependencies.py                    16      0   100%
app\auth\principal.py                      108      6    94%   61, 68, 85, 98, 154, 162
app\auth\security.py                        21      0   100%
app\core\config.py                          96      0   100%
app\core\crypto.py                          31      0   100%
app\core\exceptions.py                      28      0   100%
app\core\logging.py                         10      0   100%
app\core\ratelimit.py                      140      1    99%   51
app\db\base.py                              11      0   100%
app\db\session.py                           12      4    67%   14-18
app\domain\__init__.py                       0      0   100%
app\domain\order_state.py                   24      0   100%
app\domain\timeline.py                      14      0   100%
app\events\contracts.py                     22      0   100%
app\events\publisher.py                      8      0   100%
app\main.py                                 30      0   100%
app\middleware\rate_limit.py                52      0   100%
app\middleware\request_context.py           25      0   100%
app\models\__init__.py                       2      0   100%
app\models\entities.py                     219      0   100%
app\models\enums.py                         37      0   100%
app\observability\metrics.py                 6      0   100%
app\providers\__init__.py                    0      0   100%
app\providers\contracts.py                  85      4    95%   117, 121, 129, 133
app\providers\mock.py                       29      6    79%   27-36
app\providers\mock_v2.py                    62      0   100%
app\providers\razorpay.py                  111     10    91%   27, 39, 44, 47, 129, 143, 163, 165, 167-168
app\providers\registry.py                   13      0   100%
app\providers\resolution.py                 22      0   100%
app\repositories\base.py                    11      0   100%
app\repositories\inventory.py               15      0   100%
app\repositories\orders.py                  38      2    95%   37, 49
app\repositories\outbox.py                  10      0   100%
app\repositories\users.py                    7      0   100%
app\schemas\auth.py                         23      0   100%
app\schemas\common.py                       15      0   100%
app\schemas\inventory.py                    39      1    97%   40
app\schemas\orders.py                       67      0   100%
app\schemas\payment_config.py               14      0   100%
app\schemas\products.py                     30      1    97%   26
app\schemas\shipments.py                     6      0   100%
app\schemas\tenancy.py                      41      0   100%
app\services\auth.py                        39      2    95%   28-30
app\services\catalog.py                     53      2    96%   39, 64
app\services\failed_jobs.py                 86      1    99%   67
app\services\inventory.py                   37      1    97%   37
app\services\orders.py                     112      3    97%   37, 106, 144
app\services\payment_config.py              37      3    92%   62-64
app\services\payment_events.py              21      0   100%
app\services\payment_sessions.py            92      1    99%   109
app\services\payments.py                    32      3    91%   32-34
app\services\shipping.py                    28      0   100%
app\services\tenancy.py                     61      0   100%
app\workers\celery_app.py                    8      0   100%
app\workers\outbox_relay.py                 69      1    99%   90
app\workers\tasks.py                       168      5    97%   92-94, 178-179
----------------------------------------------------------------------
TOTAL                                     2743     78    97%
Coverage JSON written to file .coverage-s45-commit-pg-result.json

346 passed, 1 skipped in 249.33s (0:04:09)
```

Overall production coverage is **97.16% SQLite**
and **97.16% PostgreSQL**. Test files remain omitted.

| New module | SQLite production line coverage |
|---|---|
| app\api\v1\endpoints\payment_config.py | 100.00% |
| app\core\crypto.py | 100.00% |
| app\providers\mock_v2.py | 100.00% |
| app\providers\razorpay.py | 90.99% |
| app\providers\resolution.py | 100.00% |
| app\schemas\payment_config.py | 100.00% |
| app\services\payment_config.py | 91.89% |
| app\services\payment_events.py | 100.00% |
| app\services\payment_sessions.py | 96.74% |

Final report-only commit gates were rerun on the finished source tree.

```text
All checks passed!
139 files already formatted
Success: no issues found in 114 source files
TOTAL                                2743     78    97%
329 passed, 18 skipped in 135.75s (0:02:15)
```

### Saga4–6 commit gates and final raw outputs

Earlier commit-boundary suites, in step4 SQLite/PG then step5 SQLite/PG order:

```text
339 passed, 20 skipped in 257.88s (0:04:17)
358 passed, 1 skipped in 381.90s (0:06:21)
352 passed, 23 skipped in 164.39s (0:02:44)
374 passed, 1 skipped in 274.79s (0:04:34)
```

```text
All checks passed!
154 files already formatted
Success: no issues found in 125 source files
```

SQLite final production-only coverage and suite:

```text
.........................................s..........s................... [ 17%]
........................................................................ [ 35%]
........................................................................ [ 53%]
........s...........s.s..ssssssss.s.................ss.................. [ 71%]
.......s.s..................s.s.s..........s............................ [ 89%]
...s...............s....................s.                               [100%]

---------- coverage: platform win32, python 3.12.6-final-0 -----------
Name                                       Stmts   Miss  Cover   Missing
------------------------------------------------------------------------
app\__init__.py                                0      0   100%
app\api\__init__.py                            0      0   100%
app\api\v1\__init__.py                         0      0   100%
app\api\v1\endpoints\__init__.py               0      0   100%
app\api\v1\endpoints\admin.py                 46      0   100%
app\api\v1\endpoints\auth.py                  17      0   100%
app\api\v1\endpoints\health.py                35      4    89%   24-26, 37
app\api\v1\endpoints\inventory.py            111      7    94%   27, 44, 70, 107-108, 122, 145
app\api\v1\endpoints\orders.py                30      2    93%   47-49
app\api\v1\endpoints\payment_config.py        24      0   100%
app\api\v1\endpoints\payment_webhooks.py      20      0   100%
app\api\v1\endpoints\products.py              27      2    93%   37-38
app\api\v1\endpoints\shipments.py             22      6    73%   29-34
app\api\v1\endpoints\tenancy.py               26      0   100%
app\api\v1\router.py                          13      0   100%
app\auth\dependencies.py                      16      0   100%
app\auth\principal.py                        108      6    94%   61, 68, 85, 98, 154, 162
app\auth\security.py                          21      0   100%
app\core\config.py                            97      0   100%
app\core\crypto.py                            31      0   100%
app\core\exceptions.py                        28      0   100%
app\core\logging.py                           10      0   100%
app\core\ratelimit.py                        140      1    99%   51
app\db\base.py                                11      0   100%
app\db\session.py                             12      4    67%   14-18
app\domain\__init__.py                         0      0   100%
app\domain\order_state.py                     24      0   100%
app\domain\timeline.py                        14      0   100%
app\events\contracts.py                       22      0   100%
app\events\publisher.py                        9      0   100%
app\main.py                                   30      0   100%
app\middleware\rate_limit.py                  52      0   100%
app\middleware\request_context.py             25      0   100%
app\models\__init__.py                         2      0   100%
app\models\entities.py                       256      0   100%
app\models\enums.py                           37      0   100%
app\observability\metrics.py                   6      0   100%
app\providers\__init__.py                      0      0   100%
app\providers\contracts.py                    85      4    95%   120, 124, 132, 136
app\providers\mock.py                         29      7    76%   27-36, 40
app\providers\mock_v2.py                      68      5    93%   110-114
app\providers\razorpay.py                    123      9    93%   129, 143, 159, 161-162, 176, 178, 180-181
app\providers\registry.py                     13      0   100%
app\providers\resolution.py                   32      0   100%
app\repositories\base.py                      11      0   100%
app\repositories\inventory.py                 15      0   100%
app\repositories\orders.py                    38      2    95%   37, 49
app\repositories\outbox.py                    10      0   100%
app\repositories\users.py                      7      0   100%
app\schemas\auth.py                           23      0   100%
app\schemas\common.py                         15      0   100%
app\schemas\inventory.py                      39      1    97%   40
app\schemas\orders.py                         67      0   100%
app\schemas\payment_config.py                 14      0   100%
app\schemas\products.py                       30      1    97%   26
app\schemas\shipments.py                       6      0   100%
app\schemas\tenancy.py                        41      0   100%
app\services\auth.py                          39      2    95%   28-30
app\services\catalog.py                       53      2    96%   39, 64
app\services\failed_jobs.py                   88      1    99%   67
app\services\inbound_payments.py              52      2    96%   85, 87
app\services\inventory.py                     37      1    97%   37
app\services\orders.py                       112      3    97%   37, 106, 144
app\services\payment_config.py                37      3    92%   62-64
app\services\payment_events.py                21      0   100%
app\services\payment_sessions.py              92      3    97%   109, 133-134
app\services\payments.py                      35      3    91%   33-35
app\services\provider_events.py               87      3    97%   124, 132-133
app\services\refund_intents.py                31      0   100%
app\services\refunds.py                      166     13    92%   35-36, 60, 69, 106-108, 114, 142, 164, 193, 201, 209
app\services\shipping.py                      28      0   100%
app\services\tenancy.py                       61      0   100%
app\workers\celery_app.py                      8      0   100%
app\workers\outbox_relay.py                   69      2    97%   30, 90
app\workers\tasks.py                         194      8    96%   85-86, 93-94, 97-98, 211-212
------------------------------------------------------------------------
TOTAL                                       3198    107    97%
Coverage JSON written to file .coverage-s456-green-result.json

377 passed, 25 skipped in 271.63s (0:04:31)
```

PostgreSQL+Redis final production-only coverage and suite:

```text
........................................................................ [ 17%]
........................................................................ [ 35%]
........................................................................ [ 53%]
........................................................................ [ 71%]
............................s........................................... [ 89%]
..........................................                               [100%]

---------- coverage: platform win32, python 3.12.6-final-0 -----------
Name                                       Stmts   Miss  Cover   Missing
------------------------------------------------------------------------
app\__init__.py                                0      0   100%
app\api\__init__.py                            0      0   100%
app\api\v1\__init__.py                         0      0   100%
app\api\v1\endpoints\__init__.py               0      0   100%
app\api\v1\endpoints\admin.py                 46      0   100%
app\api\v1\endpoints\auth.py                  17      0   100%
app\api\v1\endpoints\health.py                35      4    89%   24-26, 37
app\api\v1\endpoints\inventory.py            111      7    94%   27, 44, 70, 107-108, 122, 145
app\api\v1\endpoints\orders.py                30      2    93%   47-49
app\api\v1\endpoints\payment_config.py        24      0   100%
app\api\v1\endpoints\payment_webhooks.py      20      0   100%
app\api\v1\endpoints\products.py              27      2    93%   37-38
app\api\v1\endpoints\shipments.py             22      6    73%   29-34
app\api\v1\endpoints\tenancy.py               26      0   100%
app\api\v1\router.py                          13      0   100%
app\auth\dependencies.py                      16      0   100%
app\auth\principal.py                        108      6    94%   61, 68, 85, 98, 154, 162
app\auth\security.py                          21      0   100%
app\core\config.py                            97      0   100%
app\core\crypto.py                            31      0   100%
app\core\exceptions.py                        28      0   100%
app\core\logging.py                           10      0   100%
app\core\ratelimit.py                        140      1    99%   51
app\db\base.py                                11      0   100%
app\db\session.py                             12      4    67%   14-18
app\domain\__init__.py                         0      0   100%
app\domain\order_state.py                     24      0   100%
app\domain\timeline.py                        14      0   100%
app\events\contracts.py                       22      0   100%
app\events\publisher.py                        9      0   100%
app\main.py                                   30      0   100%
app\middleware\rate_limit.py                  52      0   100%
app\middleware\request_context.py             25      0   100%
app\models\__init__.py                         2      0   100%
app\models\entities.py                       256      0   100%
app\models\enums.py                           37      0   100%
app\observability\metrics.py                   6      0   100%
app\providers\__init__.py                      0      0   100%
app\providers\contracts.py                    85      4    95%   120, 124, 132, 136
app\providers\mock.py                         29      7    76%   27-36, 40
app\providers\mock_v2.py                      68      5    93%   110-114
app\providers\razorpay.py                    123      9    93%   129, 143, 159, 161-162, 176, 178, 180-181
app\providers\registry.py                     13      0   100%
app\providers\resolution.py                   32      0   100%
app\repositories\base.py                      11      0   100%
app\repositories\inventory.py                 15      0   100%
app\repositories\orders.py                    38      2    95%   37, 49
app\repositories\outbox.py                    10      0   100%
app\repositories\users.py                      7      0   100%
app\schemas\auth.py                           23      0   100%
app\schemas\common.py                         15      0   100%
app\schemas\inventory.py                      39      1    97%   40
app\schemas\orders.py                         67      0   100%
app\schemas\payment_config.py                 14      0   100%
app\schemas\products.py                       30      1    97%   26
app\schemas\shipments.py                       6      0   100%
app\schemas\tenancy.py                        41      0   100%
app\services\auth.py                          39      2    95%   28-30
app\services\catalog.py                       53      2    96%   39, 64
app\services\failed_jobs.py                   88      1    99%   67
app\services\inbound_payments.py              52      2    96%   85, 87
app\services\inventory.py                     37      1    97%   37
app\services\orders.py                       112      3    97%   37, 106, 144
app\services\payment_config.py                37      3    92%   62-64
app\services\payment_events.py                21      0   100%
app\services\payment_sessions.py              92      1    99%   109
app\services\payments.py                      35      3    91%   33-35
app\services\provider_events.py               87      1    99%   124
app\services\refund_intents.py                31      0   100%
app\services\refunds.py                      166      8    95%   60, 69, 114, 142, 164, 193, 201, 209
app\services\shipping.py                      28      0   100%
app\services\tenancy.py                       61      0   100%
app\workers\celery_app.py                      8      0   100%
app\workers\outbox_relay.py                   69      1    99%   90
app\workers\tasks.py                         194     11    94%   85-86, 93-94, 97-98, 125-127, 211-212
------------------------------------------------------------------------
TOTAL                                       3198    100    97%
Coverage JSON written to file .coverage-s456-green-pg-result.json

401 passed, 1 skipped in 502.40s (0:08:22)
```

| Layer | Module | Statement coverage |
|---|---|---|
| SQLite | app/api/v1/endpoints/payment_webhooks.py | 100.00% |
| SQLite | app/core/crypto.py | 100.00% |
| SQLite | app/core/ratelimit.py | 99.29% |
| SQLite | app/domain/timeline.py | 100.00% |
| SQLite | app/providers/mock_v2.py | 92.65% |
| SQLite | app/providers/razorpay.py | 92.68% |
| SQLite | app/services/inbound_payments.py | 96.15% |
| SQLite | app/services/payment_sessions.py | 96.74% |
| SQLite | app/services/provider_events.py | 96.55% |
| SQLite | app/services/refund_intents.py | 100.00% |
| SQLite | app/services/refunds.py | 92.17% |
| SQLite | TOTAL production (tests omitted) | 96.65% |
| PostgreSQL+Redis | app/api/v1/endpoints/payment_webhooks.py | 100.00% |
| PostgreSQL+Redis | app/core/crypto.py | 100.00% |
| PostgreSQL+Redis | app/core/ratelimit.py | 99.29% |
| PostgreSQL+Redis | app/domain/timeline.py | 100.00% |
| PostgreSQL+Redis | app/providers/mock_v2.py | 92.65% |
| PostgreSQL+Redis | app/providers/razorpay.py | 92.68% |
| PostgreSQL+Redis | app/services/inbound_payments.py | 96.15% |
| PostgreSQL+Redis | app/services/payment_sessions.py | 98.91% |
| PostgreSQL+Redis | app/services/provider_events.py | 98.85% |
| PostgreSQL+Redis | app/services/refund_intents.py | 100.00% |
| PostgreSQL+Redis | app/services/refunds.py | 95.18% |
| PostgreSQL+Redis | TOTAL production (tests omitted) | 96.87% |

SQLite skips PostgreSQL/Redis-only proofs plus the optional live provider test.
PG+Redis skips only live Razorpay test-mode credentials. Each selected new module
meets90%; full totals also meet90%. Coverage excludes app/tests via pyproject.

## B5. Migrations and legacy-data proof

0008_api_key_prefix.py widens api_keys.key_prefix12→24, preserving hashes, rows,
unique/index semantics. It changes no native enums. Downgrade refuses prefixes
over12 before alteration. SQLite/PG tests start at0007, insert a legacy key, upgrade,
verify its unchanged hash, downgrade/upgrade, insert a17-character prefix and verify
the clear refusal. PostgreSQL uses a disposable schema inside orders_test.

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial, initial order platform schema
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_idempotency, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Running upgrade 0003_reservation -> 0004_outbox, Transactional event intents; new table has no historical backfill.
INFO  [alembic.runtime.migration] Running upgrade 0004_outbox -> 0005_consumers, Consumer deduplication markers are committed with handler effects.
INFO  [alembic.runtime.migration] Running upgrade 0005_consumers -> 0006_replay, Store full failed-event identifiers and portable replay status.
INFO  [alembic.runtime.migration] Running upgrade 0006_replay -> 0007_tenancy, Tenant ownership and catalog backfill, without changing native enums.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0007_tenancy -> 0008_key_prefix, Widen key lookup prefixes while preserving legacy hashes.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0008_key_prefix -> 0007_tenancy, Widen key lookup prefixes while preserving legacy hashes.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0007_tenancy -> 0008_key_prefix, Widen key lookup prefixes while preserving legacy hashes.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0008_key_prefix -> 0007_tenancy, Widen key lookup prefixes while preserving legacy hashes.
LEGACY PREFIX PRESERVED; 0007/HEAD ROUNDTRIP PASS; NEW PREFIX DOWNGRADE REFUSED
.CONSERVATION PASS: initial=20 accepted_delta=20 reserved=30 available=10
.
2 passed in 3.68s
```

### Current0009 legacy observation and safe downgrade proof

SQLite and PostgreSQL start at0008 with a legacy PAID order, upgrade to0009,
verify its synthetic paid observation, downgrade/re-upgrade preserving the order,
then add operational history and verify refusal with both rows retained.
PostgreSQL uses a unique disposable schema in orders_test. Existing0006/0007
migration regressions also remain in the full green suites.

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial, initial order platform schema
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_idempotency, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Running upgrade 0003_reservation -> 0004_outbox, Transactional event intents; new table has no historical backfill.
INFO  [alembic.runtime.migration] Running upgrade 0004_outbox -> 0005_consumers, Consumer deduplication markers are committed with handler effects.
INFO  [alembic.runtime.migration] Running upgrade 0005_consumers -> 0006_replay, Store full failed-event identifiers and portable replay status.
INFO  [alembic.runtime.migration] Running upgrade 0006_replay -> 0007_tenancy, Tenant ownership and catalog backfill, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0007_tenancy -> 0008_key_prefix, Widen key lookup prefixes while preserving legacy hashes.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0008_key_prefix -> 0009_timeline, Persist order timeline; legacy rows are synthetic observations, not invented history.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0009_timeline -> 0008_key_prefix, Persist order timeline; legacy rows are synthetic observations, not invented history.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0008_key_prefix -> 0009_timeline, Persist order timeline; legacy rows are synthetic observations, not invented history.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0009_timeline -> 0008_key_prefix, Persist order timeline; legacy rows are synthetic observations, not invented history.
TIMELINE LEGACY BACKFILL AND 0008/HEAD ROUNDTRIP PASS; ORDER PRESERVED
OPERATIONAL TIMELINE DOWNGRADE REFUSAL PASS; HISTORY RETAINED
.
1 passed in 2.77s
```

### Payment migrations

0010 empty downgrade/upgrade succeeds and credential-row downgrade refuses while
preserving ciphertext.0011 backfills legacy orders to prepaid with null expiry,
preserves totals and rows through a reconstructible roundtrip, then refuses
operational COD/session history. SQLite and real PG tests exercise both. Existing
0007→head→back→head key proof and timeline legacy proof remain green. Explicit PostgreSQL proof follows.

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial, initial order platform schema
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_idempotency, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Running upgrade 0003_reservation -> 0004_outbox, Transactional event intents; new table has no historical backfill.
INFO  [alembic.runtime.migration] Running upgrade 0004_outbox -> 0005_consumers, Consumer deduplication markers are committed with handler effects.
INFO  [alembic.runtime.migration] Running upgrade 0005_consumers -> 0006_replay, Store full failed-event identifiers and portable replay status.
INFO  [alembic.runtime.migration] Running upgrade 0006_replay -> 0007_tenancy, Tenant ownership and catalog backfill, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0007_tenancy -> 0008_key_prefix, Widen key lookup prefixes while preserving legacy hashes.
INFO  [alembic.runtime.migration] Running upgrade 0008_key_prefix -> 0009_timeline, Persist order timeline; legacy rows are synthetic observations, not invented history.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0009_timeline -> 0010_payment_config, Encrypted tenant-owned payment settings; native enums are unchanged.
INFO  [alembic.runtime.migration] Running upgrade 0010_payment_config -> 0011_sessions, Portable payment session state, preserving all native enum labels.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0011_sessions -> 0010_payment_config, Portable payment session state, preserving all native enum labels.
INFO  [alembic.runtime.migration] Running downgrade 0010_payment_config -> 0009_timeline, Encrypted tenant-owned payment settings; native enums are unchanged.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0009_timeline -> 0010_payment_config, Encrypted tenant-owned payment settings; native enums are unchanged.
INFO  [alembic.runtime.migration] Running upgrade 0010_payment_config -> 0011_sessions, Portable payment session state, preserving all native enum labels.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0011_sessions -> 0010_payment_config, Portable payment session state, preserving all native enum labels.
INFO  [alembic.runtime.migration] Running downgrade 0010_payment_config -> 0009_timeline, Encrypted tenant-owned payment settings; native enums are unchanged.
PAYMENT CONFIG EMPTY ROUNDTRIP PASS; ENCRYPTED CREDENTIALS DOWNGRADE REFUSED AND PRESERVED
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial, initial order platform schema
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_idempotency, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Running upgrade 0003_reservation -> 0004_outbox, Transactional event intents; new table has no historical backfill.
INFO  [alembic.runtime.migration] Running upgrade 0004_outbox -> 0005_consumers, Consumer deduplication markers are committed with handler effects.
INFO  [alembic.runtime.migration] Running upgrade 0005_consumers -> 0006_replay, Store full failed-event identifiers and portable replay status.
INFO  [alembic.runtime.migration] Running upgrade 0006_replay -> 0007_tenancy, Tenant ownership and catalog backfill, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0007_tenancy -> 0008_key_prefix, Widen key lookup prefixes while preserving legacy hashes.
INFO  [alembic.runtime.migration] Running upgrade 0008_key_prefix -> 0009_timeline, Persist order timeline; legacy rows are synthetic observations, not invented history.
INFO  [alembic.runtime.migration] Running upgrade 0009_timeline -> 0010_payment_config, Encrypted tenant-owned payment settings; native enums are unchanged.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0010_payment_config -> 0011_sessions, Portable payment session state, preserving all native enum labels.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0011_sessions -> 0010_payment_config, Portable payment session state, preserving all native enum labels.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0010_payment_config -> 0011_sessions, Portable payment session state, preserving all native enum labels.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0011_sessions -> 0010_payment_config, Portable payment session state, preserving all native enum labels.
.SESSION LEGACY DEFAULT/BACKWARD ROUNDTRIP PASS; OPERATIONAL DOWNGRADE REFUSED
.PROVIDER IN FLIGHT: CONCURRENT ORDER AND INVENTORY FOR UPDATE NOWAIT PASS
.
3 passed in 6.60s
```

### Saga4–6 migration evidence

0012/0013/0014 roundtrip on SQLite and PostgreSQL. Legacy paid/cancelled order,
payment and pre-claim owed-refund rows retain money/identity. Populated journals
and each claim/lease/failed-job metadata case refuse downgrade before destructive
DDL. No native enum changes. Fresh-schema walks include the legacy0007 chain.
An arbitrary populated production rollback/reconciliation is not asserted.

```text
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial, initial order platform schema
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_idempotency, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Running upgrade 0003_reservation -> 0004_outbox, Transactional event intents; new table has no historical backfill.
INFO  [alembic.runtime.migration] Running upgrade 0004_outbox -> 0005_consumers, Consumer deduplication markers are committed with handler effects.
INFO  [alembic.runtime.migration] Running upgrade 0005_consumers -> 0006_replay, Store full failed-event identifiers and portable replay status.
INFO  [alembic.runtime.migration] Running upgrade 0006_replay -> 0007_tenancy, Tenant ownership and catalog backfill, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0007_tenancy -> 0008_key_prefix, Widen key lookup prefixes while preserving legacy hashes.
INFO  [alembic.runtime.migration] Running upgrade 0008_key_prefix -> 0009_timeline, Persist order timeline; legacy rows are synthetic observations, not invented history.
INFO  [alembic.runtime.migration] Running upgrade 0009_timeline -> 0010_payment_config, Encrypted tenant-owned payment settings; native enums are unchanged.
INFO  [alembic.runtime.migration] Running upgrade 0010_payment_config -> 0011_sessions, Portable payment session state, preserving all native enum labels.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0011_sessions -> 0012_inbound, Durable verified inbound payment receipts, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0012_inbound -> 0013_refunds, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Running upgrade 0013_refunds -> 0014_refund_claims, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Running downgrade 0013_refunds -> 0012_inbound, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Running downgrade 0012_inbound -> 0011_sessions, Durable verified inbound payment receipts, without changing native enums.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0011_sessions -> 0012_inbound, Durable verified inbound payment receipts, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0012_inbound -> 0013_refunds, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Running upgrade 0013_refunds -> 0014_refund_claims, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Running downgrade 0013_refunds -> 0012_inbound, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Running downgrade 0012_inbound -> 0011_sessions, Durable verified inbound payment receipts, without changing native enums.
INBOUND EMPTY ROUNDTRIP PASS; OPERATIONAL RECEIPT AND INTENT PRESERVED ON REFUSAL
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial, initial order platform schema
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_idempotency, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Running upgrade 0003_reservation -> 0004_outbox, Transactional event intents; new table has no historical backfill.
INFO  [alembic.runtime.migration] Running upgrade 0004_outbox -> 0005_consumers, Consumer deduplication markers are committed with handler effects.
INFO  [alembic.runtime.migration] Running upgrade 0005_consumers -> 0006_replay, Store full failed-event identifiers and portable replay status.
INFO  [alembic.runtime.migration] Running upgrade 0006_replay -> 0007_tenancy, Tenant ownership and catalog backfill, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0007_tenancy -> 0008_key_prefix, Widen key lookup prefixes while preserving legacy hashes.
INFO  [alembic.runtime.migration] Running upgrade 0008_key_prefix -> 0009_timeline, Persist order timeline; legacy rows are synthetic observations, not invented history.
INFO  [alembic.runtime.migration] Running upgrade 0009_timeline -> 0010_payment_config, Encrypted tenant-owned payment settings; native enums are unchanged.
INFO  [alembic.runtime.migration] Running upgrade 0010_payment_config -> 0011_sessions, Portable payment session state, preserving all native enum labels.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0011_sessions -> 0012_inbound, Durable verified inbound payment receipts, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0012_inbound -> 0013_refunds, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Running upgrade 0013_refunds -> 0014_refund_claims, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Running downgrade 0013_refunds -> 0012_inbound, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Running downgrade 0012_inbound -> 0011_sessions, Durable verified inbound payment receipts, without changing native enums.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0011_sessions -> 0012_inbound, Durable verified inbound payment receipts, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0012_inbound -> 0013_refunds, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Running upgrade 0013_refunds -> 0014_refund_claims, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Running downgrade 0013_refunds -> 0012_inbound, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Running downgrade 0012_inbound -> 0011_sessions, Durable verified inbound payment receipts, without changing native enums.
.INBOUND EMPTY ROUNDTRIP PASS; OPERATIONAL RECEIPT AND INTENT PRESERVED ON REFUSAL
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial, initial order platform schema
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_idempotency, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Running upgrade 0003_reservation -> 0004_outbox, Transactional event intents; new table has no historical backfill.
INFO  [alembic.runtime.migration] Running upgrade 0004_outbox -> 0005_consumers, Consumer deduplication markers are committed with handler effects.
INFO  [alembic.runtime.migration] Running upgrade 0005_consumers -> 0006_replay, Store full failed-event identifiers and portable replay status.
INFO  [alembic.runtime.migration] Running upgrade 0006_replay -> 0007_tenancy, Tenant ownership and catalog backfill, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0007_tenancy -> 0008_key_prefix, Widen key lookup prefixes while preserving legacy hashes.
INFO  [alembic.runtime.migration] Running upgrade 0008_key_prefix -> 0009_timeline, Persist order timeline; legacy rows are synthetic observations, not invented history.
INFO  [alembic.runtime.migration] Running upgrade 0009_timeline -> 0010_payment_config, Encrypted tenant-owned payment settings; native enums are unchanged.
INFO  [alembic.runtime.migration] Running upgrade 0010_payment_config -> 0011_sessions, Portable payment session state, preserving all native enum labels.
INFO  [alembic.runtime.migration] Running upgrade 0011_sessions -> 0012_inbound, Durable verified inbound payment receipts, without changing native enums.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0012_inbound -> 0013_refunds, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Running upgrade 0013_refunds -> 0014_refund_claims, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Running downgrade 0013_refunds -> 0012_inbound, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0012_inbound -> 0013_refunds, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Running upgrade 0013_refunds -> 0014_refund_claims, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Running downgrade 0013_refunds -> 0012_inbound, Durable refund requests required by captured-payment compensation.
.REFUND EMPTY ROUNDTRIP PASS; LEGACY PAYMENT AND DURABLE REQUEST PRESERVED
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial, initial order platform schema
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_idempotency, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Running upgrade 0003_reservation -> 0004_outbox, Transactional event intents; new table has no historical backfill.
INFO  [alembic.runtime.migration] Running upgrade 0004_outbox -> 0005_consumers, Consumer deduplication markers are committed with handler effects.
INFO  [alembic.runtime.migration] Running upgrade 0005_consumers -> 0006_replay, Store full failed-event identifiers and portable replay status.
INFO  [alembic.runtime.migration] Running upgrade 0006_replay -> 0007_tenancy, Tenant ownership and catalog backfill, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0007_tenancy -> 0008_key_prefix, Widen key lookup prefixes while preserving legacy hashes.
INFO  [alembic.runtime.migration] Running upgrade 0008_key_prefix -> 0009_timeline, Persist order timeline; legacy rows are synthetic observations, not invented history.
INFO  [alembic.runtime.migration] Running upgrade 0009_timeline -> 0010_payment_config, Encrypted tenant-owned payment settings; native enums are unchanged.
INFO  [alembic.runtime.migration] Running upgrade 0010_payment_config -> 0011_sessions, Portable payment session state, preserving all native enum labels.
INFO  [alembic.runtime.migration] Running upgrade 0011_sessions -> 0012_inbound, Durable verified inbound payment receipts, without changing native enums.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0012_inbound -> 0013_refunds, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Running upgrade 0013_refunds -> 0014_refund_claims, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Running downgrade 0013_refunds -> 0012_inbound, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0012_inbound -> 0013_refunds, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Running upgrade 0013_refunds -> 0014_refund_claims, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Running downgrade 0013_refunds -> 0012_inbound, Durable refund requests required by captured-payment compensation.
.REFUND EMPTY ROUNDTRIP PASS; LEGACY PAYMENT AND DURABLE REQUEST PRESERVED
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial, initial order platform schema
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_idempotency, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Running upgrade 0003_reservation -> 0004_outbox, Transactional event intents; new table has no historical backfill.
INFO  [alembic.runtime.migration] Running upgrade 0004_outbox -> 0005_consumers, Consumer deduplication markers are committed with handler effects.
INFO  [alembic.runtime.migration] Running upgrade 0005_consumers -> 0006_replay, Store full failed-event identifiers and portable replay status.
INFO  [alembic.runtime.migration] Running upgrade 0006_replay -> 0007_tenancy, Tenant ownership and catalog backfill, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0007_tenancy -> 0008_key_prefix, Widen key lookup prefixes while preserving legacy hashes.
INFO  [alembic.runtime.migration] Running upgrade 0008_key_prefix -> 0009_timeline, Persist order timeline; legacy rows are synthetic observations, not invented history.
INFO  [alembic.runtime.migration] Running upgrade 0009_timeline -> 0010_payment_config, Encrypted tenant-owned payment settings; native enums are unchanged.
INFO  [alembic.runtime.migration] Running upgrade 0010_payment_config -> 0011_sessions, Portable payment session state, preserving all native enum labels.
INFO  [alembic.runtime.migration] Running upgrade 0011_sessions -> 0012_inbound, Durable verified inbound payment receipts, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0012_inbound -> 0013_refunds, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0013_refunds -> 0014_refund_claims, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0013_refunds -> 0014_refund_claims, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
.REFUND CLAIM ROUNDTRIP PASS; LEGACY MONEY PRESERVED; ALL THREE METADATA DOWNGRADES REFUSED
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial, initial order platform schema
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_idempotency, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Running upgrade 0003_reservation -> 0004_outbox, Transactional event intents; new table has no historical backfill.
INFO  [alembic.runtime.migration] Running upgrade 0004_outbox -> 0005_consumers, Consumer deduplication markers are committed with handler effects.
INFO  [alembic.runtime.migration] Running upgrade 0005_consumers -> 0006_replay, Store full failed-event identifiers and portable replay status.
INFO  [alembic.runtime.migration] Running upgrade 0006_replay -> 0007_tenancy, Tenant ownership and catalog backfill, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0007_tenancy -> 0008_key_prefix, Widen key lookup prefixes while preserving legacy hashes.
INFO  [alembic.runtime.migration] Running upgrade 0008_key_prefix -> 0009_timeline, Persist order timeline; legacy rows are synthetic observations, not invented history.
INFO  [alembic.runtime.migration] Running upgrade 0009_timeline -> 0010_payment_config, Encrypted tenant-owned payment settings; native enums are unchanged.
INFO  [alembic.runtime.migration] Running upgrade 0010_payment_config -> 0011_sessions, Portable payment session state, preserving all native enum labels.
INFO  [alembic.runtime.migration] Running upgrade 0011_sessions -> 0012_inbound, Durable verified inbound payment receipts, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0012_inbound -> 0013_refunds, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0013_refunds -> 0014_refund_claims, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0013_refunds -> 0014_refund_claims, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0014_refund_claims -> 0013_refunds, Recoverable refund claims protect provider calls without holding row locks.
.REFUND CLAIM ROUNDTRIP PASS; LEGACY MONEY PRESERVED; ALL THREE METADATA DOWNGRADES REFUSED
.
6 passed in 23.89s
```

Local operational upgrade with writers stopped and volumes preserved:

```text
Container event-driven-order-orchestration-system-postgres-1 Running
 Container event-driven-order-orchestration-system-postgres-1 Waiting
 Container event-driven-order-orchestration-system-postgres-1 Healthy
 Container event-driven-order-orchestration-system-migrate-run-9f9ad22ace89 Creating
 Container event-driven-order-orchestration-system-migrate-run-9f9ad22ace89 Created
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0011_sessions -> 0012_inbound, Durable verified inbound payment receipts, without changing native enums.
INFO  [alembic.runtime.migration] Running upgrade 0012_inbound -> 0013_refunds, Durable refund requests required by captured-payment compensation.
INFO  [alembic.runtime.migration] Running upgrade 0013_refunds -> 0014_refund_claims, Recoverable refund claims protect provider calls without holding row locks.
```

## B6. Docker acceptance and restored runtime

E1–E18/E20/E28/E30 are NOT VERIFIED in this continuation; the scoped
rate/timeline smoke is recorded below. Historical3B.1 runtime restoration follows. Historical Phase3A E1–E14 results are not relabeled as new evidence. Existing
writers were stopped, migration applied, and the stack restored without deleting
data/volumes. Runtime check and compose status:

```text
key_prefix_length= 24
failure_injection_enabled= False
NAME                                                     IMAGE                                                  COMMAND                  SERVICE        CREATED          STATUS                          PORTS
event-driven-order-orchestration-system-api-1            event-driven-order-orchestration-system-api            "uvicorn app.main:ap…"   api            36 minutes ago   Up About a minute               0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
event-driven-order-orchestration-system-migrate-1        event-driven-order-orchestration-system-migrate        "alembic upgrade head"   migrate        36 minutes ago   Exited (0) About a minute ago
event-driven-order-orchestration-system-outbox-relay-1   event-driven-order-orchestration-system-outbox-relay   "python -m app.worke…"   outbox-relay   36 minutes ago   Up About a minute               8000/tcp
event-driven-order-orchestration-system-postgres-1       postgres:16-alpine                                     "docker-entrypoint.s…"   postgres       2 months ago     Up 36 minutes (healthy)         0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
event-driven-order-orchestration-system-rabbitmq-1       rabbitmq:3.13-management-alpine                        "docker-entrypoint.s…"   rabbitmq       2 months ago     Up 36 minutes (healthy)         0.0.0.0:5672->5672/tcp, [::]:5672->5672/tcp, 0.0.0.0:15672->15672/tcp, [::]:15672->15672/tcp
event-driven-order-orchestration-system-redis-1          redis:7-alpine                                         "docker-entrypoint.s…"   redis          2 months ago     Up 36 minutes                   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
event-driven-order-orchestration-system-scheduler-1      event-driven-order-orchestration-system-scheduler      "celery -A app.worke…"   scheduler      36 minutes ago   Up About a minute               8000/tcp
event-driven-order-orchestration-system-worker-1         event-driven-order-orchestration-system-worker         "celery -A app.worke…"   worker         36 minutes ago   Up About a minute               8000/tcp
```

### Current local Docker smoke and restored runtime

Writers were stopped before schema/code changes,0009 applied with the migrate
service, then `docker compose up -d --no-build` restored all writers. No volumes
or existing rows were deleted. The configured shared limiter and real relay/worker
were used. The mock-flow smoke creates one order, observes five ordered timeline
states and four status intents/consumer markers, then replays without extra writes.
This is a subset proof, not the full payment acceptance suite.

```text
PASS local Docker smoke: public/auth quota headers; 5 ordered timeline rows; 4 status intents/consumer markers; replay unchanged
```

```text
rate_limit_backend= RedisRateLimiter
shared_redis_reachable= True
trusted_proxy_count= 0
registration_enabled= True
invite_required= False
failure_injection_enabled= False
migration= 0009_timeline
synthetic_timeline_rows= 227
NAME                                                     IMAGE                                                  COMMAND                  SERVICE        CREATED         STATUS                     PORTS
event-driven-order-orchestration-system-api-1            event-driven-order-orchestration-system-api            "uvicorn app.main:ap…"   api            2 minutes ago   Up 2 minutes               0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
event-driven-order-orchestration-system-migrate-1        event-driven-order-orchestration-system-migrate        "alembic upgrade head"   migrate        2 minutes ago   Exited (0) 2 minutes ago
event-driven-order-orchestration-system-outbox-relay-1   event-driven-order-orchestration-system-outbox-relay   "python -m app.worke…"   outbox-relay   2 minutes ago   Up 2 minutes               8000/tcp
event-driven-order-orchestration-system-postgres-1       postgres:16-alpine                                     "docker-entrypoint.s…"   postgres       2 months ago    Up 3 hours (healthy)       0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
event-driven-order-orchestration-system-rabbitmq-1       rabbitmq:3.13-management-alpine                        "docker-entrypoint.s…"   rabbitmq       2 months ago    Up 3 hours (healthy)       0.0.0.0:5672->5672/tcp, [::]:5672->5672/tcp, 0.0.0.0:15672->15672/tcp, [::]:15672->15672/tcp
event-driven-order-orchestration-system-redis-1          redis:7-alpine                                         "docker-entrypoint.s…"   redis          2 months ago    Up 3 hours                 0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
event-driven-order-orchestration-system-scheduler-1      event-driven-order-orchestration-system-scheduler      "celery -A app.worke…"   scheduler      2 minutes ago   Up 2 minutes               8000/tcp
event-driven-order-orchestration-system-worker-1         event-driven-order-orchestration-system-worker         "celery -A app.worke…"   worker         2 minutes ago   Up 2 minutes               8000/tcp
```

The first final-report pytest invocation stopped before collection: coverage cleanup
matched the redirected output filename, which Windows had locked. Distinct data
and output prefixes fixed the invocation; no test failure was suppressed.

One inline Python runtime-check command failed PowerShell quoting before running;
the same read-only check succeeded when passed as a file. No failed command is
counted as successful evidence.

### Selected payment Docker subset

Images were rebuilt with cryptography50.0.2. Writers were stopped before operational
migration,0011 applied, and all services restarted. Current scope smoke results:

```text
PASS session subset: pending checkout, durable attempt, cancellation and stock release
PASS E18 subset: COD fulfillment without session; cancellation releases stock and shipment
RESTORED worker runtime {"auto_complete": true, "allow_mock": true, "failure_injection": false}
```

The preceding rate/timeline live smoke also passed with five timeline rows, four
status intents/consumer markers and unchanged replay. Operational runtime observed:

```text
cryptography_version= 50.0.2
environment= local
allow_mock_providers= True
mock_payment_auto_complete= True
mock_refund_fail_attempts= 0
payment_window_seconds= 1800
failure_injection_enabled= False
credential_key_configured= False
limiter_backend= RedisRateLimiter
migration= 0011_sessions
```

API, worker, scheduler and relay were Up, PostgreSQL/RabbitMQ healthy, Redis Up,
and migrate exited0. Local settings writes require an explicitly configured key.
E1–E18/E20/E28/E30 as a full acceptance collection remain NOT VERIFIED. The new
script labels its pending-session and E18 portions as subsets.

Final restored Compose status:

```text
NAME                                                     IMAGE                                                  COMMAND                  SERVICE        CREATED          STATUS                      PORTS
event-driven-order-orchestration-system-api-1            event-driven-order-orchestration-system-api            "uvicorn app.main:ap…"   api            17 minutes ago   Up 17 minutes               0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
event-driven-order-orchestration-system-migrate-1        event-driven-order-orchestration-system-migrate        "alembic upgrade head"   migrate        17 minutes ago   Exited (0) 12 minutes ago
event-driven-order-orchestration-system-outbox-relay-1   event-driven-order-orchestration-system-outbox-relay   "python -m app.worke…"   outbox-relay   17 minutes ago   Up 17 minutes               8000/tcp
event-driven-order-orchestration-system-postgres-1       postgres:16-alpine                                     "docker-entrypoint.s…"   postgres       2 months ago     Up 43 minutes (healthy)     0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
event-driven-order-orchestration-system-rabbitmq-1       rabbitmq:3.13-management-alpine                        "docker-entrypoint.s…"   rabbitmq       2 months ago     Up 43 minutes (healthy)     0.0.0.0:5672->5672/tcp, [::]:5672->5672/tcp, 0.0.0.0:15672->15672/tcp, [::]:15672->15672/tcp
event-driven-order-orchestration-system-redis-1          redis:7-alpine                                         "docker-entrypoint.s…"   redis          2 months ago     Up 43 minutes               0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
event-driven-order-orchestration-system-scheduler-1      event-driven-order-orchestration-system-scheduler      "celery -A app.worke…"   scheduler      17 minutes ago   Up 17 minutes               8000/tcp
event-driven-order-orchestration-system-worker-1         event-driven-order-orchestration-system-worker         "celery -A app.worke…"   worker         12 minutes ago   Up 12 minutes               8000/tcp
```

### Saga4–6 Docker proofs and restored state

Built all five application images; applied0012→0014 with writers stopped.
The following uses real PostgreSQL/RabbitMQ/Redis, outbox relay and prefork worker.
HTTP E15 uses a deliberately seeded local provider-session fixture; it is not a
remote Razorpay session/capture proof. E17 uses signed internal mock ingestion
through the same journal; no public mock test hook is added. Late cancellation
is covered; automatic expiry E16 remains outside scope.

```text
PASS E20: broker/prefork worker; two transient failures, attempts=3, one refund success/audit; shipment cancelled, stock restored
PASS E17 pipeline: signed mock mismatch -> receipt -> broker consumer -> async refund; pending, no shipment
PASS late-cancel pipeline: signed late payment refunded, cancellation and stock preserved (expiry scheduling outside scope)
PASS E15 HTTP fixture: raw HMAC accepted once, duplicate=200, bad/missing=401, oversize=413; broker fulfillment one payment/shipment; NO remote Razorpay session/money
RESTORED api {"auto_complete": true, "refund_fail_attempts": 0, "allow_mock": true, "failure_injection": false, "encryption_configured": false}
RESTORED worker {"auto_complete": true, "refund_fail_attempts": 0, "allow_mock": true, "failure_injection": false, "encryption_configured": false}
PASS session subset: pending checkout, durable attempt, cancellation and stock release
PASS E18 subset: COD fulfillment without session; cancellation releases stock and shipment
RESTORED worker runtime {"auto_complete": true, "allow_mock": true, "failure_injection": false}
```

After the session/COD script recreated the default worker, its Celery mingle
startup hit kombu OperationalError:timed out. The first timeline smoke therefore
failed with Timeline/status consumer completion timed out. Broker diagnostics
reported no resource alarms. Restarted only the worker, observed real consumption,
and reran the timeline smoke successfully; no volumes/data were reset.

```text
PASS local Docker smoke: public/auth quota headers; 5 ordered timeline rows; 4 status intents/consumer markers; replay unchanged
->  celery@f971a246c869: OK
        pong

1 node online.
NAME                                                     IMAGE                                                  COMMAND                  SERVICE        CREATED          STATUS                     PORTS
event-driven-order-orchestration-system-api-1            event-driven-order-orchestration-system-api            "uvicorn app.main:ap…"   api            11 minutes ago   Up 11 minutes              0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
event-driven-order-orchestration-system-migrate-1        event-driven-order-orchestration-system-migrate        "alembic upgrade head"   migrate        13 minutes ago   Exited (0) 7 minutes ago
event-driven-order-orchestration-system-outbox-relay-1   event-driven-order-orchestration-system-outbox-relay   "python -m app.worke…"   outbox-relay   13 minutes ago   Up 13 minutes              8000/tcp
event-driven-order-orchestration-system-postgres-1       postgres:16-alpine                                     "docker-entrypoint.s…"   postgres       2 months ago     Up 2 hours (healthy)       0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
event-driven-order-orchestration-system-rabbitmq-1       rabbitmq:3.13-management-alpine                        "docker-entrypoint.s…"   rabbitmq       2 months ago     Up 2 hours (healthy)       0.0.0.0:5672->5672/tcp, [::]:5672->5672/tcp, 0.0.0.0:15672->15672/tcp, [::]:15672->15672/tcp
event-driven-order-orchestration-system-redis-1          redis:7-alpine                                         "docker-entrypoint.s…"   redis          2 months ago     Up 2 hours                 0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
event-driven-order-orchestration-system-scheduler-1      event-driven-order-orchestration-system-scheduler      "celery -A app.worke…"   scheduler      13 minutes ago   Up 13 minutes              8000/tcp
event-driven-order-orchestration-system-worker-1         event-driven-order-orchestration-system-worker         "celery -A app.worke…"   worker         9 minutes ago    Up 6 minutes               8000/tcp
{"auto_complete": true, "refund_fail_attempts": 0, "refund_lease_seconds": 60, "allow_mock": true, "failure_injection": false, "encryption_configured": false}
    version_num
--------------------
 0014_refund_claims
(1 row)
```

Full E1–E14, E16 automatic expiry, E28/E30 Docker matrices are NOT VERIFIED in
this continuation. Existing unit/PG strict business/concurrency assertions remain
green; scoped quota/timeline and checkout/COD Docker checks above are verified.

## B7. Proofs and isolation

The PG mixed race applies twenty signed adjustments alongside thirty reservations,
checking available=20+accepted deltas−reserved and reserved_quantity=reserved orders.
Raw output is B5. API tests verify audit actor/reason/counts and patch FOR UPDATE SQL.
The isolation matrix now covers20 tenant method/path combinations ×4 principals
(80 combinations), including foreign404 and own-role/key adjustment behavior. The
unclassified-route guard includes the new endpoint. Both run in the full suite.
Lock-free providers, webhook concurrency and refund exactly-once proofs are NOT VERIFIED.

Current added controls: real Redis concurrent hits admit exactly7 of40 attempts;
the cold-factory test returns one store to eight simultaneous callers. HTTP tests
cover window expiry, principal/IP separation, forwarded-header trust, request IDs,
429/503 headers, registration gates and privacy. All15 valid transition edges
retain state/audit assertions plus timeline/intent checks; illegal edges and
rollback persist no history. Informational event redelivery creates only one
consumer marker and no transition, and the real Docker flow confirms four such
markers without a notification loop. Tenant isolation tests remain green.

### Payment proof coverage

The PostgreSQL provider stub blocks until a separate thread/session acquires
FOR UPDATE NOWAIT on both the order and its inventory. This runs while the provider
call is in flight and the workflow session reports no transaction. It passed in
the final PG suite. Retry tests preserve the payment UUID/key after transient error
and a final-phase crash. Cancellation during provider I/O persists no returned
reference. Invalid/duplicate capture paths add no transition. COD fulfillment,
collection redelivery, cancellation and shipment-failure conservation are covered.
The legacy lifecycle table and no-transition-loop guard remain green.

### Saga4–6 proofs

PG concurrent same event-ID receipts produce one receipt/intent. Concurrent same
receipt and distinct captured-equivalent event IDs produce one paid transition
and fulfillment intent. Consumer tests cover tenant/reference isolation, rollback
of receipt status/marker/financial effects, payment_failed→captured, mismatch,
expired/terminal/extra charge refunds, and monotonic callback success.

The blocking refund-provider PG test proves ORDER, REFUND and INVENTORY are all
NOWAIT-lockable from another session during HTTP. A duplicate worker observes the
committed lease instead of another provider call. Crash-after-provider-success
reuses the durable key and fetches before a second submission. Callback/stale
claim races cannot replace success or the current claim. Cancellation rollback
preserves shipment/status/event/audit, stock, payment and refund/outbox atomically.
The original80-case tenant matrix remains; the registry classifies all43 method/
path entries and requires declared policies for every public/public-webhook entry.

```text
CONCURRENT VERIFIED RECEIPTS: ONE JOURNAL ROW AND ONE INTENT
.CONCURRENT CAPTURES: ONE PAID TRANSITION AND ONE FULFILLMENT INTENT
.CONCURRENT CAPTURES: ONE PAID TRANSITION AND ONE FULFILLMENT INTENT
.REFUND PROVIDER IN FLIGHT: ORDER, REFUND AND INVENTORY NOWAIT PASS; ONE CLAIM/EFFECT
.
4 passed in 7.10s
```

## B8. Razorpay verification ledger

The following paragraph records the historical pre-adapter pass. No Razorpay API/field/header/event was used then. Official provider-doc verification and live test mode were NOT VERIFIED in that historical pass because the adapter was absent. Added
tests perform no real external network calls.

### Razorpay and crypto verification ledger for this continuation

Documentation was checked on21 November2026. VERIFIED here means the official
source documents the named contract. MockTransport tests verify our request/parser
behavior. Live provider semantics remain subject to the explicit limits in B13.

| Contract used | Status and primary source |
|---|---|
| POST /v1/orders, Basic auth, integer amount/currency, unique receipt≤40, notes and order id response | VERIFIED [Create order](https://razorpay.com/docs/api/orders/create/) |
| GET /v1/orders receipt filter, collection items, pagination | VERIFIED [Fetch orders](https://razorpay.com/docs/api/orders/fetch-all/). Local code requires exact receipt/amount/currency/notes match |
| GET /v1/orders/{id}/payments and id/order_id/status/amount/currency | VERIFIED [Fetch payments](https://razorpay.com/docs/api/orders/fetch-payments/) |
| POST /v1/payments/{id}/refund, amount/speed/receipt, refund id/status, X-Refund-Idempotency, same body/key and retryable409 | VERIFIED [Normal refund](https://razorpay.com/docs/api/refunds/create-normal/). Header constraints≥10 characters, alphanumeric/underscore/hyphen. Documented availability India/Malaysia |
| GET /v1/payments/{id}/refunds | VERIFIED [Refund API index](https://razorpay.com/docs/api/refunds/) |
| GET /v1/refunds/{id}, id/status | VERIFIED [Fetch refund](https://razorpay.com/docs/api/refunds/fetch-with-id/) |
| Raw HMAC-SHA256 hex, X-Razorpay-Signature and x-razorpay-event-id | VERIFIED [Webhook validation](https://razorpay.com/docs/webhooks/validate-test/) |
| payment.captured/payment.failed payment.entity id/order_id/amount/currency | VERIFIED [Payment webhooks](https://razorpay.com/docs/webhooks/payments/) |
| order.paid includes captured payment entity | VERIFIED [Order webhooks](https://razorpay.com/docs/webhooks/orders/). Local normalized captured-equivalent requires downstream state/id guards |
| refund.created/processed/failed, refund and payment entities | VERIFIED [Refund webhooks](https://razorpay.com/docs/webhooks/refunds/). Created alone does not imply completion |
| app checkout expires_at, provider_status, normalized types and receipt hashing | INTERNAL application fields, no remote API field claim |
| Universal Orders idempotency header/atomic lookup-create guarantee | UNVERIFIED, no such header invented. Exact receipt lookup precedes each create attempt |
| Fernet authenticated encryption and MultiFernet newest-first rotation | VERIFIED [Cryptography](https://cryptography.io/en/latest/fernet/) |
| Live Razorpay session/capture/refund and provider race/visibility guarantees | NOT VERIFIED. Test-mode session test skipped because credentials are absent |

### Saga4–6 additional official verification (21 November2026)

| Contract checked | Result/source |
|---|---|
| Raw-body HMAC-SHA256 hex; X-Razorpay-Signature; x-razorpay-event-id dedupe; old signing secret needed for provider retries after rotation | VERIFIED [Validate/test webhooks](https://razorpay.com/docs/webhooks/validate-test/). App supports one stored signing secret; rotation overlap is an operational limitation |
| POST /v1/payments/{id}/refund; amount/speed=normal/receipt; X-Refund-Idempotency minimum10 allowed characters, identical body/key; retry409; documented India/Malaysia availability | VERIFIED [Normal refund](https://razorpay.com/docs/api/refunds/create-normal/). Adapter uses deterministic32-hex receipt/header, fixed body and typed transient409 |
| GET /v1/payments/{id}/refunds; count≤100, skip≥0; collection items id/payment_id/amount/currency/receipt/status pending/processed/failed | VERIFIED [Fetch payment refunds](https://razorpay.com/docs/api/refunds/fetch-multiple-refund-payment/). Unknown-result lookup requires matching receipt, payment and amount before returning a result |
| find_refund(), claim_token, lease_until, normalized internal events and failed-job linkage | INTERNAL application contracts, not invented Razorpay endpoints/fields |
| Real account entitlement, remote visibility/concurrency/idempotency and money settlement | NOT VERIFIED without live/test-mode credentials; all HTTP adapter tests use MockTransport |

The earlier event/field/fetch ledger remains applicable. Documentation verification
does not claim live API execution or remote exactly-once settlement.

## B9. Phase3A carry-over

| Item | Status | Reason |
|---|---|---|
| Inventory PATCH race/adjustments | RESOLVED | Locked writes + audit + PG conservation |
| Unscoped repository/service inventory helpers | RESOLVED | Deleted, guarded against reintroduction |
| Scheduler global low-stock cap/aggregation | RESOLVED | Per-tenant keyset scans/counts |
| HTTP low-stock100-row cap | DEFERRED | Separate unchanged API limitation |
| Short key prefix | RESOLVED | New17, storage24, legacy12 compatibility |
| Duplicate warehouse/inventory500 | RESOLVED | Previous6eb6b04 typed409 |
| Missing-user login hash shortcut | RESOLVED | Previous6eb6b04 dummy hash |
| Provider-under-lock/refund journal | PARTIAL | Session/refund HTTP release locks with PG NOWAIT proofs; durable journals/worker implemented, live provider unverified |
| Abuse controls | RESOLVED in selected scope | Atomic shared quota policies, outage modes, registration/invite gates |
| Timeline/status notification foundation | RESOLVED | Transactional rows/intents, safe backfill, privacy and no-loop guards |
| Shipping lifecycle, notifications, tracking, SSE, failed-jobs API | DEFERRED | Part2 |
| Historical data/VALIDATED states, RLS/email uniqueness, failure-store outage | DEFERRED | Existing limitations retained |
| RabbitMQ HA/volume, real monitoring, hosted CI/deployment | DEFERRED | No operational redesign |

## B10. Changed files and commits

Historical3B.1 snapshot before its report-only commit (preserved):

```text
alembic/versions/0008_api_key_prefix.py |  25 ++
 app/api/v1/endpoints/inventory.py       |  72 +++++-
 app/auth/principal.py                   |  16 +-
 app/core/exceptions.py                  |   8 +
 app/models/entities.py                  |   2 +-
 app/repositories/base.py                |  10 -
 app/repositories/inventory.py           |  10 -
 app/schemas/inventory.py                |  12 +
 app/services/auth.py                    |   7 +-
 app/services/inventory.py               |  14 +-
 app/services/tenancy.py                 |   4 +-
 app/tests/test_integration_carryover.py |  35 +++
 app/tests/test_postgres_reliability.py  |  69 +++++
 app/tests/test_prefix_migration.py      |  81 ++++++
 app/tests/test_principal_auth.py        |   2 +-
 app/tests/test_reliability_edges.py     |   4 +-
 app/tests/test_remaining_carryover.py   | 133 ++++++++++
 app/tests/test_tenant_isolation.py      |  15 ++
 app/workers/tasks.py                    |  41 ++-
 docs/reports/phase-3b-report.md         | 431 ++++++++++++++++++++++++++++++++
 20 files changed, 936 insertions(+), 55 deletions(-)
```

```text
4ef2ff7 fix(tenancy): finish inventory isolation and key prefix carry-over
1800ebd docs: add phase 3b integrations documentation and verification report
6eb6b04 fix(security): harden login verification and duplicate inventory errors
```


### Current snapshot before the final report-only commit

```text
 .env.example                            |  25 ++
 .github/workflows/ci.yml                |   7 +
 README.md                               |  60 +++-
 alembic/versions/0008_api_key_prefix.py |  25 ++
 alembic/versions/0009_order_timeline.py |  42 +++
 app/api/v1/endpoints/health.py          |   7 +-
 app/api/v1/endpoints/inventory.py       |  72 +++-
 app/api/v1/endpoints/tenancy.py         |   5 +-
 app/auth/principal.py                   |  34 +-
 app/core/config.py                      |  30 +-
 app/core/exceptions.py                  |   8 +
 app/core/ratelimit.py                   | 246 ++++++++++++++
 app/domain/order_state.py               |  11 +-
 app/domain/timeline.py                  |  52 +++
 app/events/contracts.py                 |   1 +
 app/main.py                             |  15 +-
 app/middleware/rate_limit.py            |  73 ++++
 app/models/__init__.py                  |   2 +
 app/models/entities.py                  |  20 +-
 app/repositories/base.py                |  10 -
 app/repositories/inventory.py           |  10 -
 app/schemas/inventory.py                |  12 +
 app/schemas/tenancy.py                  |   3 +-
 app/services/auth.py                    |   7 +-
 app/services/failed_jobs.py             |   2 +-
 app/services/inventory.py               |  14 +-
 app/services/orders.py                  |   2 +
 app/services/tenancy.py                 |  22 +-
 app/tests/conftest.py                   |  16 +-
 app/tests/test_consumers.py             |   7 +-
 app/tests/test_integration_carryover.py |  35 ++
 app/tests/test_inventory_atomic.py      |  13 +-
 app/tests/test_order_recovery.py        |   3 +-
 app/tests/test_order_state.py           |  12 +-
 app/tests/test_order_timeline.py        | 136 ++++++++
 app/tests/test_postgres_reliability.py  |  80 ++++-
 app/tests/test_prefix_migration.py      |  81 +++++
 app/tests/test_principal_auth.py        |   2 +-
 app/tests/test_ratelimit.py             | 356 ++++++++++++++++++++
 app/tests/test_reliability_edges.py     |   4 +-
 app/tests/test_remaining_carryover.py   | 133 ++++++++
 app/tests/test_tenant_isolation.py      |  19 ++
 app/tests/test_timeline_migration.py    |  95 ++++++
 app/workers/tasks.py                    |  60 +++-
 docs/reports/phase-3b-report.md         | 569 ++++++++++++++++++++++++++++++++
 pytest.ini                              |   1 +
 scripts/verify_rate_timeline.py         | 155 +++++++++
 47 files changed, 2507 insertions(+), 87 deletions(-)

1bb5fc1 feat(events): persist order timelines and transactional status intents
b721647 feat(security): add shared rate limits and registration controls
fbc43ce docs: add phase 3b part 1 report
4ef2ff7 fix(tenancy): finish inventory isolation and key prefix carry-over
1800ebd docs: add phase 3b integrations documentation and verification report
6eb6b04 fix(security): harden login verification and duplicate inventory errors
```

The final report-only commit is additional; this snapshot cannot include its own hash.

### Current payment change snapshot before report-only commit

```text
 .env.example                                 |   33 +-
 .github/workflows/ci.yml                     |    7 +
 README.md                                    |  126 ++-
 alembic/versions/0008_api_key_prefix.py      |   25 +
 alembic/versions/0009_order_timeline.py      |   42 +
 alembic/versions/0010_payment_config.py      |   36 +
 alembic/versions/0011_payment_sessions.py    |   37 +
 app/api/v1/endpoints/health.py               |    7 +-
 app/api/v1/endpoints/inventory.py            |   72 +-
 app/api/v1/endpoints/payment_config.py       |   41 +
 app/api/v1/endpoints/tenancy.py              |    5 +-
 app/api/v1/router.py                         |    3 +-
 app/auth/principal.py                        |   34 +-
 app/core/config.py                           |   59 +-
 app/core/crypto.py                           |   44 +
 app/core/exceptions.py                       |    8 +
 app/core/ratelimit.py                        |  246 ++++++
 app/domain/order_state.py                    |   18 +-
 app/domain/timeline.py                       |   54 ++
 app/events/contracts.py                      |    2 +
 app/main.py                                  |   15 +-
 app/middleware/rate_limit.py                 |   73 ++
 app/models/__init__.py                       |    2 +
 app/models/entities.py                       |   56 +-
 app/providers/contracts.py                   |   74 ++
 app/providers/mock_v2.py                     |  111 +++
 app/providers/razorpay.py                    |  188 +++++
 app/providers/resolution.py                  |   34 +
 app/repositories/base.py                     |   10 -
 app/repositories/inventory.py                |   10 -
 app/schemas/inventory.py                     |   12 +
 app/schemas/orders.py                        |   16 +
 app/schemas/payment_config.py                |   19 +
 app/schemas/tenancy.py                       |    3 +-
 app/services/auth.py                         |    7 +-
 app/services/failed_jobs.py                  |    2 +-
 app/services/inventory.py                    |   14 +-
 app/services/orders.py                       |    9 +
 app/services/payment_config.py               |   64 ++
 app/services/payment_events.py               |   42 +
 app/services/payment_sessions.py             |  155 ++++
 app/services/tenancy.py                      |   22 +-
 app/tests/conftest.py                        |   23 +-
 app/tests/test_consumers.py                  |    7 +-
 app/tests/test_failure_scenarios.py          |   20 +-
 app/tests/test_integration_carryover.py      |   35 +
 app/tests/test_inventory_atomic.py           |   13 +-
 app/tests/test_mock_v2.py                    |   94 +++
 app/tests/test_order_recovery.py             |    3 +-
 app/tests/test_order_state.py                |   12 +-
 app/tests/test_order_timeline.py             |  136 +++
 app/tests/test_payment_config.py             |  160 ++++
 app/tests/test_payment_config_migration.py   |   74 ++
 app/tests/test_payment_sessions.py           |  253 ++++++
 app/tests/test_payment_sessions_migration.py |   78 ++
 app/tests/test_postgres_reliability.py       |   80 +-
 app/tests/test_prefix_migration.py           |   81 ++
 app/tests/test_principal_auth.py             |    2 +-
 app/tests/test_provider_inputs.py            |   17 +
 app/tests/test_providers.py                  |    9 +-
 app/tests/test_ratelimit.py                  |  356 ++++++++
 app/tests/test_razorpay.py                   |  163 ++++
 app/tests/test_reliability_edges.py          |    4 +-
 app/tests/test_remaining_carryover.py        |  133 +++
 app/tests/test_settings_hardening.py         |    9 +-
 app/tests/test_tenant_isolation.py           |   22 +
 app/tests/test_timeline_migration.py         |  105 +++
 app/workers/tasks.py                         |   79 +-
 docs/reports/phase-3b-report.md              | 1139 ++++++++++++++++++++++++++
 pytest.ini                                   |    1 +
 requirements.txt                             |    1 +
 scripts/e2e_integrations.py                  |   99 +++
 scripts/verify_rate_timeline.py              |  155 ++++
 73 files changed, 5105 insertions(+), 95 deletions(-)

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

### Saga4–6 snapshot before final report-only commit

```text
.env.example                                 |   34 +-
 .github/workflows/ci.yml                     |    7 +
 README.md                                    |  126 +-
 alembic/versions/0008_api_key_prefix.py      |   25 +
 alembic/versions/0009_order_timeline.py      |   42 +
 alembic/versions/0010_payment_config.py      |   36 +
 alembic/versions/0011_payment_sessions.py    |   37 +
 alembic/versions/0012_inbound_webhooks.py    |   31 +
 alembic/versions/0013_refunds.py             |   39 +
 alembic/versions/0014_refund_claims.py       |   34 +
 app/api/v1/endpoints/health.py               |    7 +-
 app/api/v1/endpoints/inventory.py            |   72 +-
 app/api/v1/endpoints/payment_config.py       |   41 +
 app/api/v1/endpoints/payment_webhooks.py     |   41 +
 app/api/v1/endpoints/tenancy.py              |    5 +-
 app/api/v1/router.py                         |   15 +-
 app/auth/principal.py                        |   34 +-
 app/core/config.py                           |   60 +-
 app/core/crypto.py                           |   44 +
 app/core/exceptions.py                       |    8 +
 app/core/ratelimit.py                        |  246 ++++
 app/domain/order_state.py                    |   18 +-
 app/domain/timeline.py                       |   54 +
 app/events/contracts.py                      |    2 +
 app/events/publisher.py                      |    5 +-
 app/main.py                                  |   15 +-
 app/middleware/rate_limit.py                 |   73 +
 app/models/__init__.py                       |    2 +
 app/models/entities.py                       |  103 +-
 app/providers/contracts.py                   |   77 ++
 app/providers/mock_v2.py                     |  118 ++
 app/providers/razorpay.py                    |  201 +++
 app/providers/resolution.py                  |   54 +
 app/repositories/base.py                     |   10 -
 app/repositories/inventory.py                |   10 -
 app/schemas/inventory.py                     |   12 +
 app/schemas/orders.py                        |   16 +
 app/schemas/payment_config.py                |   19 +
 app/schemas/tenancy.py                       |    3 +-
 app/services/auth.py                         |    7 +-
 app/services/failed_jobs.py                  |   12 +-
 app/services/inbound_payments.py             |  118 ++
 app/services/inventory.py                    |   14 +-
 app/services/orders.py                       |    9 +
 app/services/payment_config.py               |   64 +
 app/services/payment_events.py               |   42 +
 app/services/payment_sessions.py             |  155 +++
 app/services/payments.py                     |   17 +-
 app/services/provider_events.py              |  148 ++
 app/services/refund_intents.py               |   74 +
 app/services/refunds.py                      |  232 ++++
 app/services/tenancy.py                      |   22 +-
 app/tests/conftest.py                        |   24 +-
 app/tests/test_cancel_hardening.py           |    7 +
 app/tests/test_consumers.py                  |   39 +-
 app/tests/test_failure_scenarios.py          |   20 +-
 app/tests/test_inbound_migration.py          |   57 +
 app/tests/test_inbound_payments.py           |  163 +++
 app/tests/test_integration_carryover.py      |   35 +
 app/tests/test_inventory_atomic.py           |   21 +-
 app/tests/test_mock_v2.py                    |  102 ++
 app/tests/test_order_recovery.py             |    9 +-
 app/tests/test_order_state.py                |   12 +-
 app/tests/test_order_timeline.py             |  136 ++
 app/tests/test_payment_config.py             |  160 +++
 app/tests/test_payment_config_migration.py   |   74 +
 app/tests/test_payment_sessions.py           |  253 ++++
 app/tests/test_payment_sessions_migration.py |   78 ++
 app/tests/test_postgres_reliability.py       |   84 +-
 app/tests/test_prefix_migration.py           |   81 ++
 app/tests/test_principal_auth.py             |    2 +-
 app/tests/test_provider_events.py            |  306 +++++
 app/tests/test_provider_inputs.py            |   17 +
 app/tests/test_providers.py                  |   28 +-
 app/tests/test_ratelimit.py                  |  356 +++++
 app/tests/test_razorpay.py                   |  189 +++
 app/tests/test_refund_claim_migration.py     |  111 ++
 app/tests/test_refund_migration.py           |   90 ++
 app/tests/test_refund_worker.py              |  462 +++++++
 app/tests/test_reliability_edges.py          |    4 +-
 app/tests/test_remaining_carryover.py        |  133 ++
 app/tests/test_settings_hardening.py         |    9 +-
 app/tests/test_tenant_isolation.py           |   25 +-
 app/tests/test_timeline_migration.py         |  105 ++
 app/workers/celery_app.py                    |    1 +
 app/workers/tasks.py                         |  116 +-
 docs/reports/phase-3b-report.md              | 1862 ++++++++++++++++++++++++++
 pytest.ini                                   |    1 +
 requirements.txt                             |    1 +
 scripts/e2e_integrations.py                  |   99 ++
 scripts/e2e_payments.py                      |  388 ++++++
 scripts/verify_rate_timeline.py              |  155 +++
 92 files changed, 8387 insertions(+), 118 deletions(-)
```

```text
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

The following report-only commit updates this same cumulative document; its own
object ID cannot appear inside itself. No push/PR/merge was performed.

## B11. Deviations and modified existing tests

- Historical3B.1 stopped under its former working rule5. This continuation follows
  the user's explicit2/3 scope and anti-early-stop rules: both scoped tasks completed;
  no current budget-based stop or in-scope skipped task.
- Only relevant task1 files/report limitations are read, as the shortened preflight requests.
- Positive controls added after implementation are explicitly identified in B3.
- Out-of-range positive adjustments get a portable422 instead of PG INTEGER overflow.
- API-key audit identity uses the key UUID in payload because actor_id is a user FK.
- Low-stock maintenance returns tenant counts instead of a cross-tenant SKU list.
- No secrets, live provider call, native enum label, remote push or Part2 route is added.

| Modified pre-existing test | Reason; strictness retained |
|---|---|
| test_principal_auth::test_api_key_show_once_hash_uniform_auth_and_throttle | Expected prefix12→17 only; secrecy, uniform errors, comparison and throttle assertions remain |
| test_reliability_edges::test_missing_order_and_scheduled_task_controls | Assert default tenant count1 instead of cross-tenant SKU list; missing-order and scheduled controls remain |
| test_tenant_isolation::test_tenant_resource_isolation_matrix | Add foreign adjustment404 and owned role checks; previous checks remain |
| test_tenant_isolation::test_route_registry_has_no_unclassified_routes | Historical adjustment registration plus current assertion that every public method/path declares a quota policy; no category bypass |

Current continuation modifies exactly these six existing test functions (the public
policy guard above plus five below). Existing business-outbox assertions remain
equally strict by filtering the newly introduced informational type, then separately
asserting its expected count/content.

| Modified pre-existing test | Reason; strictness retained |
|---|---|
| test_consumers.py::test_duplicate_redelivery_has_one_effect_per_step | Retain exactly3 business intents; separately assert4 status intents; correlation assertion covers all events |
| test_inventory_atomic.py::test_partial_inventory_reservation_is_never_left_behind | Keep exactly1 inventory.failed; separately require failed status intent and reason code; stock checks retained |
| test_order_recovery.py::test_recovery_scope_suspension_discard_and_missing_order | Retain0 business intents and separately1 failure notification; scope/suspension/discard/missing assertions retained |
| test_order_state.py::test_all_order_transitions | Give mock order an explicit tenant UUID; require3 writes (audit, timeline, outbox) instead of1; retain exact audit/state and invalid-no-effect checks |
| test_postgres_reliability.py::test_postgres_two_tenants_same_sku_have_independent_stock | Retain4 business intents per tenant; additionally require4 status intents and tenant ownership per tenant; stock assertions retained |

Shared conftest changes: reset limiter stores between tests without disabling
quotas or raising limits; disable Psycopg preparation only on the destructive test
engine to prevent stale enum OIDs. Neither changes a production assertion.
Event reasons deliberately allow only declared domain codes; free text stays in
private history rather than crossing a future public event boundary. Initial
creation adds a timeline observation and retains order.created without an extra
status_changed event, following the prompt's initial-row requirement.

No original test is deleted or weakened. New PG/SQLite migration, collision and
adjustment tests are additions; prior d/e tests remain unchanged.

### Historical provider/session continuation deviations and modified existing tests

| Existing test/helper | Reason and preserved strictness |
|---|---|
| test_providers.py::test_provider_registry_rejects_unknown_and_nonlocal_mock | Supply a generated encryption key for the valid non-local configuration; retain unknown/forbidden provider assertions |
| test_settings_hardening.py::test_production_accepts_strong_secret | Supply the newly required encryption key; retain strong-secret acceptance assertion |
| test_tenant_isolation.py::test_route_registry_has_no_unclassified_routes | Classify all three settings methods as tenant-scoped; retain exhaustive route and public-policy guards |
| test_failure_scenarios.py::test_business_failure_compensates_without_retry | Patch the v2 session settings seam alongside legacy shipping; retain exact failure reason, compensation, audit and stock checks |
| test_failure_scenarios.py::test_transient_injection_then_retry_has_once_only_effects | Patch v2 injection seam; require one durable initiated payment/pending order instead of zero rolled-back payments; retain exact marker, stock and once-only retry checks |
| test_timeline_migration.py::test_sqlite_timeline_backfill_roundtrip and test_postgres_timeline_backfill_roundtrip via legacy_timeline_roundtrip | Insert the legacy order using reflected old-schema columns with portable UUID types. Retain every backfill/index/row/roundtrip/refusal assertion |
| test_mock_v2.py::test_mock_v2_signed_capture_deterministic_sessions_and_refund_retry | New this session, extended after its initial commit to verify fresh-adapter status/refund state and changed-amount rejection |

The new conftest fixture explicitly enables local mock permission for test workflows.
It neither disables quotas nor changes deployed defaults. Compose explicitly enables
local mocks. No original assertion/test was removed. Tests added after core fixes
are positive controls unless B3 records an observed red.

The credential endpoints were committed independently, then provider contracts,
Razorpay, mock/resolution, and the coherent saga1–3 sub-part. Provider resolution
and intake gating complete3B.4 across those dependency commits. Legacy mock helpers
remain to preserve existing direct callers. Recovered attempts use numbered keys
after the initial literal payment:1, preserving technical-failure replay intent.

Because HTTP inbound steps4/5 are outside the selected scope, synthetic completion
uses the signed parser and shared normalized capture handler in the final transaction.
The durable HTTP ingress/dedupe consumer is deferred, explicitly NOT VERIFIED.
No new HTTP webhook route, async refund journal, expiry worker or Part2 surface is added.

### Saga4–6 deviations and every modified pre-existing test

The following eight existing test functions changed relative to startingf07a44e;
no test/assertion was deleted or weakened. No service assertion was changed to
broker send_task: the publisher/relay boundary tests still legitimately patch it.
Financial service tests assert durable rows and run the actual worker afterward.

| Test | Reason and retained strictness |
|---|---|
| test_tenant_isolation.py::test_route_registry_has_no_unclassified_routes | Classify the new callback as PUBLIC_WEBHOOK and require its quota policy; exhaustive guard retained |
| test_consumers.py::test_duplicate_redelivery_has_one_effect_per_step | Require exactly4 business intents/markers instead of3 for the additional inbox consumer; separately require one payment_provider marker/intent; retain one payment/shipment, stock,4 status intents and correlation checks |
| test_cancel_hardening.py::test_cancel_refunds_authorized_payment_once | First assert refund requested/payment still authorized, run real refund worker, retain REFUNDED and one financial audit across repeated cancel |
| test_inventory_atomic.py::test_fulfillment_cancel_compensates_shipment_payment_inventory_atomically | Add strict requested obligation/intent assertions before actual worker, retain shipment cancellation/event/audit, final REFUNDED, stock and duplicate-cancel checks |
| test_order_recovery.py::test_recovery_reopens_compensated_order_and_finishes_once | Assert durable refund request then complete actual worker before recovery; retain refund, recovery event, no duplicate inventory/payment/shipment checks |
| test_payment_sessions.py::test_crash_in_final_phase_retries_the_same_durable_attempt | Move failure injection from inline payment.authorized to payment.provider_event intent, now the session final-commit boundary; retain rollback, same UUID/key and resumed-session assertions |
| test_postgres_reliability.py::test_postgres_concurrent_duplicate_delivery | Drive the new committed inbox phase after each eight-way concurrent business delivery; retain exact one payment/shipment and conserved stock, supplemented by new concurrent provider-consumer tests |
| test_providers.py::test_provider_calls_persist_references_and_idempotency_keys | Replace synchronous refund fake with detached v2 result, assert requested row before real worker, retain references/status and exact call list with durable paymentUUID:refund key; refund now follows shipment cancellation |

Shared fixture/helper changes: conftest.workflow total10 and
test_cancel_hardening.order_stock total20 now equal their existing priced items,
so financial amount checks cover nonzero money rather than implicit zero totals.
test_consumers.consume drives only this order's mock receipt/refund workers, never
manufactures financial state or bypasses a guard. New drain_financial helper scopes
receipt matching to tenant/provider/order references. The new step5 deliver helper
calls the real provider consumer directly so callback tests do not auto-execute
unrelated real-provider refund obligations. New mock-restart/parser controls in
existing files add tests; they do not modify earlier test bodies.

Dependency split: the refund table/request helper is included in step5 because
that consumer must journal compensation atomically; step6 independently adds
execution claims/worker and replaces synchronous compensation. No native enum
labels, mock public ingress, expiry/reconciliation scheduler, Part2 API or remote
write/push is added. The new find_refund contract formalizes mandatory lookup after
unknown outcomes. Unknown-result remote exactly-once behavior is not fabricated.

## B12. Newly discovered issues

Current regressions found and fixed: validation input leaked invite/password data;
oversized login bodies bypassed quota; concurrent cold initialization split quota
stores; empty invite configuration was accepted; arbitrary status reasons could
echo buyer/operator text. The destructive PG fixture's prepared-enum cache issue
was fixed only in test configuration. Failures and after results are in B3/B4.
Earlier missing API-key audit identity remains fixed. HTTP low-stock pagination
and other deferred Phase3A limitations remain explicit. This is no broad security
audit or arbitrary mid-transaction crash/production reliability proof.

### Historical provider/session findings and limitations

Resolved findings include stale pending-attempt selection after recovery and fresh
mock adapter refund/capture state reset. State is now shared under process-local
locks, and changed refund amounts for one key are rejected. Diagnostic fixture
failures involved reflected UUID metadata, a misplaced new-test assertion and
Decimal/float comparison. Final runs below replace those diagnostics.

Provider receipt lookup and final transaction guards preserve local effects.
Exactly-once remote order creation under eventual receipt visibility/concurrency
remains a provider limitation requiring live verification. Multi-process/restart
mock state is not durable. Durable refund/reconciliation and inbound behavior await
the remaining saga implementation.

### Saga4–6 findings resolved/current limits

Resolved: inactive configuration could otherwise reject legitimate financial
callbacks; an extra refunded charge could overwrite primary payment summary;
same-second technical recovery could resume an abandoned initiated attempt;
fresh prefork mock workers could reset injected retry counts; and a failure lacked
its once-only refund audit. SQL/claim state, immutable DTOs and final guards cover
these cases. Crash/lease/provider-result and callback races are directly tested.
The local broker startup timeout was recovered and recorded in B6.

Remote refund idempotency still depends on provider support and receipt visibility.
Processing refunds without callbacks await step8 or explicit retry. Simulator
lookup maps remain process-local even though deterministic references and durable
failure counters support the verified prefork retry path. Merchant replacement and
old signing-secret overlap need operator coordination; no history store is added.

## B13. NOT VERIFIED

### Current saga4–6 limits

- Live Razorpay credentials/session/capture/refund/settlement, entitlement and
  remote receipt visibility/concurrent idempotency guarantees. Optional live test
  skipped; local HMAC HTTP fixture is not remote money verification.
- Scheduled expiry/reconciliation (3B.5 steps7/8) and expiry-versus-webhook race,
  automatic stuck-refund polling, historical-data reconciliation.
- Full Docker E1–E14/E16/E28/E30 reruns. Scoped E15 fixture, E17 internal signed
  pipeline, late-cancel, E18/session subset, E20 and quota/timeline smoke passed.
- Part2, hosted CI, production/HA/soak, remote push/PR/merge and arbitrary populated
  production downgrade. The safe tested downgrade paths/refusals are verified.
- Multi-process/restart simulator lookup-map durability, signing-secret overlap
  history and automatic merchant-account migration. Durable refund retry counters
  and deterministic identities are verified across prefork workers.

No selected task is skipped or blocked. Earlier limits below are retained as
historical evidence; their old missing-webhook/refund claims are superseded by
the implementation and results above.

### Historical NOT VERIFIED snapshots (superseded where implemented)

- Remaining3B.5 scheduling steps7/8 and their acceptance tests (outside selected scope). Redis tests
  and CI configuration were verified locally; hosted CI execution was not.
- Full Part1 E1–E18/E20/E28/E30 reruns; scoped quota/timeline Docker smoke and
  runtime/schema restoration are verified here.
- Live Razorpay captures/refunds, provider visibility/concurrent-create guarantees and async money movement. Local encryption and adapter docs/tests are verified below.
- Webhook HTTP concurrency and durable refund reliability. Session call lock release is verified below.
- Part2 features and production/HA/soak/hosted CI/push/PR/merge.
- Historical-data reconciliation or arbitrary production downgrade eligibility.
- Future combined migration heads, long-running quota load/soak, Redis failover/HA
  and multi-host proxy deployments; current local concurrency/outage tests passed.

### Historical provider/session scope NOT VERIFIED

- Real Razorpay credentials, sessions, captures/refunds and provider race/visibility behavior.
- Saga4–9 HTTP ingest/journal, duplicate captures, async refund worker, expiry and reconciliation.
- Full acceptance E1–E18/E20/E28/E30 and Part2 shipping lifecycle/integrations.
- Multi-process/restart mock simulator state, hosted CI, production/HA/soak and remote push/PR/merge.
- Local Docker encrypted credential writes, which need an operator-provided key. SQLite/PG TestClient encryption and ownership tests passed.

No selected implementation task remains skipped or blocked.

## B14. Reviewer commands (PowerShell)

```powershell
git switch phase-3b-integrations
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .
.venv\Scripts\mypy app
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:TEST_REDIS_URL -ErrorAction SilentlyContinue
$env:COVERAGE_FILE='.coverage.s23.sqlite'
.venv\Scripts\python -m pytest -q --cov=app --cov-report=term-missing
$env:TEST_DATABASE_URL='postgresql+psycopg://orders:orders@localhost:5432/orders_test'
$env:TEST_REDIS_URL='redis://localhost:6379/15'
$env:COVERAGE_FILE='.coverage.s23.postgres'
.venv\Scripts\python -m pytest -q --cov=app --cov-report=term-missing
.venv\Scripts\python -m pytest -q -s app/tests/test_prefix_migration.py::test_postgres_prefix_migration app/tests/test_postgres_reliability.py::test_postgres_adjustments_and_thirty_reservations_conserve_stock
.venv\Scripts\python -m pytest -q -s app/tests/test_timeline_migration.py::test_postgres_timeline_backfill_roundtrip
Remove-Item Env:TEST_DATABASE_URL
Remove-Item Env:TEST_REDIS_URL
# Operational upgrade: stop writers; preserve database/volumes.
docker compose stop api worker scheduler outbox-relay
docker compose run --rm migrate
docker compose up -d --no-build
.venv\Scripts\python scripts/verify_rate_timeline.py
docker compose ps -a
git diff --check main...HEAD
git diff --stat main...HEAD
git log --oneline main..HEAD
```

Only use the disposable `_test` database for pytest. Migration fixtures use unique
schemas. Stop writers before operational schema changes. No report output includes
key plaintext; ignored local coverage files supply the committed raw evidence.

Never overlap pytest runs against the same SQLite file or PostgreSQL test database.

### Payment continuation reviewer commands

```powershell
.venv\Scripts\python -m pytest -q app/tests/test_payment_config.py app/tests/test_payment_sessions.py app/tests/test_mock_v2.py app/tests/test_razorpay.py
# With the disposable PostgreSQL/Redis variables from B14 set, run serially:
.venv\Scripts\python -m pytest -q -s app/tests/test_payment_config_migration.py::test_postgres_payment_config_migration app/tests/test_payment_sessions_migration.py::test_postgres_session_migration app/tests/test_payment_sessions.py::test_postgres_provider_call_releases_order_and_inventory_locks
docker compose stop api worker scheduler outbox-relay
docker compose build
docker compose run --rm migrate
docker compose up -d --no-build
.venv\Scripts\python scripts/e2e_integrations.py
.venv\Scripts\python scripts/verify_rate_timeline.py
docker compose ps -a
```

Run the full SQLite/PostgreSQL+Redis layers using B14, and never overlap tests
sharing one database. The scoped script temporarily changes only worker auto-complete
and restores/reads actual defaults in finally. Live adapter tests run only when
RAZORPAY_TEST_KEY_ID/RAZORPAY_TEST_KEY_SECRET exist and the key is test-mode.

### Saga4–6 reviewer commands

```powershell
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .
.venv\Scripts\mypy app
# Run complete SQLite and PostgreSQL+Redis suites serially per database as above.
.venv\Scripts\python -m pytest -q app/tests/test_inbound_payments.py app/tests/test_provider_events.py app/tests/test_refund_worker.py
# With TEST_DATABASE_URL/TEST_REDIS_URL set to disposable test resources:
.venv\Scripts\python -m pytest -q -s app/tests/test_inbound_migration.py app/tests/test_refund_migration.py app/tests/test_refund_claim_migration.py
.venv\Scripts\python -m pytest -q -s app/tests/test_refund_worker.py::test_postgres_refund_claim_deduplicates_and_releases_all_locks_during_provider_call
docker compose stop api worker scheduler outbox-relay
docker compose build
docker compose run --rm migrate
docker compose up -d --no-build
.venv\Scripts\python scripts/e2e_payments.py
.venv\Scripts\python scripts/e2e_integrations.py
.venv\Scripts\python scripts/verify_rate_timeline.py
docker compose exec -T worker celery -A app.workers.celery_app inspect ping --timeout=15
docker compose ps -a
git diff --check main...HEAD
```

These local scripts create unique proof rows and preserve existing data/volumes.
Do not supply real provider keys to fixture-based experiments. The report's green
suites are the explicit observed outputs, not a claim that all deferred acceptance
experiments or live provider operations were run.
