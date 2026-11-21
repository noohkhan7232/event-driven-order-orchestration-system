# Phase 3B — Part 1 cumulative report

21 November 2026, Asia/Calcutta. Branch `phase-3b-integrations`.
**3B.1–3B.3 were already complete. This continuation completes 3B.4 and the
selected provider/session scope of 3B.5** — provider v2, Razorpay, mock v2 and saga
changes1–3. Remaining saga steps4–9 and Part2 are outside this session. Earlier
verification snapshots remain below, explicitly historical. The implementation
commits are f43531b,7585220,78ada7d,72033a7 and9ef0307. No selected task is skipped.

# PART A — Implemented architecture and operations

## A1. Async payment saga

The session foundation is implemented. Intake accepts prepaid (default) or COD.
Default prepaid requests preserve legacy fingerprints. A non-default method is
included in the fingerprint, so changing it conflicts with the same request key.
Existing replay returns before configuration checks and creates no new intent.

```mermaid
stateDiagram-v2
    CREATED --> INVENTORY_RESERVED: reservation
    INVENTORY_RESERVED --> PAYMENT_PENDING: prepaid intent committed
    PAYMENT_PENDING --> PAID: normalized mock capture
    PAID --> FULFILLMENT_REQUESTED: shipment
    INVENTORY_RESERVED --> FULFILLMENT_REQUESTED: COD only
    PAYMENT_PENDING --> FAILED: declined session
    PAYMENT_PENDING --> CANCELLED: cancel releases stock
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

Mock auto-completion signs and parses a synthetic capture before using the shared
normalized capture handler in the final transaction. It preserves the existing
authorized native payment status while exposing provider_status=captured. COD
creates an initiated/pending_collection payment and shipment without a session.
Only COD permits the direct inventory_reserved→fulfillment_requested edge.
Delivered status notifications collect COD once without another transition.
COD cancellation releases stock and cancels its shipment without refunding.

Inbound HTTP ingestion, durable inbound deduplication, late/mismatched captures,
async refunds, expiry scheduling and reconciliation remain saga steps4–9. Their
sequence diagrams await those implementations. Real sessions stay pending until
that integration is installed. Part2 supplies the full shipping lifecycle.

## A2. Provider architecture

The detached PaymentProviderV2 contract defines session creation, signed webhook
parsing, refund, payment-status fetch and refund fetch. SessionOrder and
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
helpers remain for existing callers/tests and synchronous mock compensation.
The session workflow uses v2. No real provider refund runs inside those helpers.

Secrets are encrypted with Fernet through CredentialVault. Non-local settings
require valid CREDENTIALS_ENCRYPTION_KEY. Local writes also require a key and
return503 credentials_unavailable when absent. MultiFernet accepts a comma-separated
ring, newest first, enabling staged rotation. Only ciphertext is stored. Owner JWT
settings responses reveal the public key ID's last four characters, public webhook
identifier/URL, mode and active flag. The reserved webhook URL awaits step4.

## A3. Refunds and reconciliation

The provider v2 adapter exposes refund and fetch primitives. Existing compensation
continues using the synchronous local mock helper. Durable refunds, unknown-outcome
worker reconciliation and scheduler expiry remain outside the selected session.
No real refund reliability or exactly-once money movement is claimed here.

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
| PUBLIC_BASE_URL | http://localhost:8000, used for the reserved webhook URL |
| ALLOW_MOCK_PROVIDERS | Model default false; local Compose example explicitly true |
| MOCK_PAYMENT_AUTO_COMPLETE | True locally, false non-locally when unspecified; true forbidden non-locally |
| MOCK_WEBHOOK_SECRET | Local simulator default; configure through a secret store |
| MOCK_REFUND_FAIL_ATTEMPTS | 0, nonnegative integer |
| PAYMENT_WINDOW_SECONDS | 1800, positive integer |

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

Payment additions follow below. No PUBLIC_WEBHOOK/PUBLIC_TRACKING route
exists yet. The registry adds the adjustment route to TENANT_SCOPED; the existing
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
42 method/path entries, including23 tenant entries. The original80-case tenant
matrix remains, supplemented by settings role/ownership and secrecy tests.

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
below. Payment-session and credential operations are described below. Durable refunds await later saga steps. Reproduction commands are B14.

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
masked data. The returned webhook URL is reserved until inbound HTTP step4 exists.
DELETE deactivates the row. No credential plaintext is returned after write.

For rotation, deploy new,old keys in that order. CredentialVault.rotate re-encrypts
key_secret_enc and webhook_secret_enc under the first key in a controlled maintenance
transaction. Verify new-key decryption before commit, then retire old keys only after
all rows/processes and backup-retention requirements permit it. Keep secret values
out of commands, logs, reports and source control.

For pending sessions, inspect the owned order/payment row, provider_status and
attempt key. Retry resumes the same attempt. Cancellation releases inventory and
omits checkout. Expiry/refund/reconciliation operation requires the remaining saga
implementation. Local scripts/e2e_integrations.py verifies the selected session/COD
subset and restores actual worker settings in finally. It leaves unique local rows.

## A14. Limitations

Remaining3B.5 steps4–9 and the full3B.P1 acceptance experiments remain outside this session. Part2 is not implemented. Timeline IDs are allocation order,
not transaction commit order; future SSE must implement its specified lookback.
Memory quotas are process-local and suitable only for local/development/test.
Use shared Redis for multiple application workers. No row-level database security
is added; timeline isolation uses authoritative tenant ownership and scoped access.
Legacy mock compensation remains synchronous while session HTTP runs after commit; application-only tenancy,
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

### Current selected payment continuation

Baseline287 passed/14 skipped before edits. Five implementation commits complete
the selected scope. Local encryption/API isolation, detached contracts, adapter
transport tests, phased retries/cancellation, COD and live subset checks pass.
Current full results and any repeated gates appear in B4. No selected task is skipped.
Earlier B1 evidence is historical.

## B2. Task status

| ID | Task | Status | Commits | Notes |
|---|---|---|---|---|
| 3B.1 | Carry-over fixes(a–e) | DONE previously | 6eb6b04, 4ef2ff7 | Not redone in this continuation |
| 3B.2 | Rate limiting/abuse | DONE | b721647 | Shared Redis, local memory, policies, invite controls |
| 3B.3 | Timeline/status event | DONE | 1bb5fc1 | Transactional history/intents, safe backfill, no-loop consumption |
| 3B.4 | Credentials/configuration | DONE | f43531b,72033a7,9ef0307 | Encrypted settings, owner isolation, resolution and intake gate |
| 3B.5 | Async payments/refunds/COD | PARTIAL | 7585220,78ada7d,72033a7,9ef0307 | Selected provider part and saga1–3 DONE; steps4–9 outside session |
| 3B.P1 | Quality/Docker proof | PARTIAL | 4ef2ff7,b721647,1bb5fc1,9ef0307 | Selected gates, PG/Redis, migrations, session/COD Docker subset verified; full acceptance remains outside session |
| 3B.6 | Shipping lifecycle | Part 2 | — | Out of scope |
| 3B.7 | Outbound webhooks | Part 2 | — | Out of scope |
| 3B.8 | Notifications | Part 2 | — | Out of scope |
| 3B.9 | Public tracking | Part 2 | — | Out of scope |
| 3B.10 | SSE | Part 2 | — | Out of scope |
| 3B.11 | Failed-jobs API | Part 2 | — | Out of scope |
| 3B.12 | OpenAPI/final quality/docs | Part 2 | — | Part1 gates recorded separately |
| 3B.13 | Full-phase Docker proof | Part 2 | — | Part1 subset remains outstanding |

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

### Current payment continuation final outputs

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
| Provider-under-lock/refund journal | PARTIAL | Session HTTP releases locks, proven with PG NOWAIT; durable refunds deferred |
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

### Payment continuation deviations and modified existing tests

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

## B12. Newly discovered issues

Current regressions found and fixed: validation input leaked invite/password data;
oversized login bodies bypassed quota; concurrent cold initialization split quota
stores; empty invite configuration was accepted; arbitrary status reasons could
echo buyer/operator text. The destructive PG fixture's prepared-enum cache issue
was fixed only in test configuration. Failures and after results are in B3/B4.
Earlier missing API-key audit identity remains fixed. HTTP low-stock pagination
and other deferred Phase3A limitations remain explicit. This is no broad security
audit or arbitrary mid-transaction crash/production reliability proof.

### Payment findings resolved and remaining limitations

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

## B13. NOT VERIFIED

- Remaining3B.5 steps4–9 and their acceptance tests (outside selected scope). Redis tests
  and CI configuration were verified locally; hosted CI execution was not.
- Full Part1 E1–E18/E20/E28/E30 reruns; scoped quota/timeline Docker smoke and
  runtime/schema restoration are verified here.
- Live Razorpay captures/refunds, provider visibility/concurrent-create guarantees and async money movement. Local encryption and adapter docs/tests are verified below.
- Webhook HTTP concurrency and durable refund reliability. Session call lock release is verified below.
- Part2 features and production/HA/soak/hosted CI/push/PR/merge.
- Historical-data reconciliation or arbitrary production downgrade eligibility.
- Future combined migration heads, long-running quota load/soak, Redis failover/HA
  and multi-host proxy deployments; current local concurrency/outage tests passed.

### Current selected scope NOT VERIFIED

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
