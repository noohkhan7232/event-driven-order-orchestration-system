# Phase 3A — Multi-tenant foundation

Verification date: 21 November 2026 (Asia/Calcutta). Branch: `phase-3a-tenancy`.
This file documents implemented behavior and separates local verification from unverified deployment claims.

# PART A — Architecture and operations

## A1. Tenancy model

A seller owns one tenant. Non-platform users belong to exactly one tenant. CUSTOMER users can operate only their own orders; VENDOR staff manage tenant resources; ADMIN users are tenant owners. A platform administrator has `tenant_id=NULL` and `is_platform_admin=true`. Both ORM validation and a database CHECK enforce that ownership invariant. Emails remain globally unique.

Warehouses are tenant-owned as well as inventory: sellers can reuse warehouse codes and SKUs without sharing stock. Products are unique by `(tenant_id, sku)`. JWT idempotency uses `user:<UUID>`; every integration key for a tenant shares the `tenant` principal scope. Therefore rotating keys does not permit duplicate requests with the same integration idempotency key.

```mermaid
erDiagram
    TENANTS ||--o{ USERS : owns
    TENANTS ||--o{ API_KEYS : owns
    USERS ||--o{ API_KEYS : creates
    TENANTS ||--o{ PRODUCTS : catalogs
    TENANTS ||--o{ WAREHOUSES : owns
    TENANTS ||--o{ INVENTORY : owns
    WAREHOUSES ||--o{ INVENTORY : stores
    TENANTS ||--o{ ORDERS : owns
    USERS o|--o{ ORDERS : places
    ORDERS ||--|{ ORDER_ITEMS : snapshots
    ORDERS ||--o{ PAYMENTS : pays
    ORDERS ||--o{ SHIPMENTS : fulfills
    SHIPMENTS ||--o{ SHIPMENT_EVENTS : records
    TENANTS o|--o{ OUTBOX_EVENTS : annotates
    TENANTS o|--o{ FAILED_JOBS : annotates
    USERS o|--o{ AUDIT_LOGS : acts
    TENANTS {
        UUID id PK
        string slug UK
        string status
        string default_currency
    }
    API_KEYS {
        UUID tenant_id FK
        string key_prefix UK
        string key_hash
        JSON scopes
        datetime expires_at
        datetime revoked_at
        datetime last_used_at
    }
    ORDERS {
        UUID tenant_id FK
        UUID customer_id FK
        string principal_scope
        string idempotency_key
        string external_reference
        string buyer_name
        string buyer_email
        string buyer_phone
    }
    PRODUCTS {
        UUID tenant_id FK
        string sku
        decimal price
        string currency
        boolean is_active
    }
    PAYMENTS {
        UUID order_id FK
        string provider
        string provider_reference
    }
    SHIPMENTS {
        UUID order_id FK
        string provider
        string provider_reference
    }
```

## A2. Authentication and authorization

```mermaid
flowchart TD
    R[Request] --> C{Credential headers}
    C -->|Both present| A[400 ambiguous_credentials]
    C -->|Neither| N[401 unauthenticated]
    C -->|Bearer JWT| J[Verify signature and load active user from DB]
    C -->|X-API-Key| K[Lookup 12-character prefix and compare SHA256 suffix digest]
    J --> T[Load tenant from database]
    K --> V{Format, digest, expiry and revocation valid?}
    V -->|No| N
    V -->|Yes| T
    T --> S{Tenant active?}
    S -->|No| D[403 tenant_suspended]
    S -->|Yes or platform identity| P[Principal and scope checks]
```

JWT `tid` is informational. Every request reloads the user, active flag and tenant from the database; forged/stale role or tenant claims do not grant access. Platform identities have no tenant and cannot use tenant-scoped business endpoints.

| Principal | Orders | Catalog/inventory | Key management | Platform |
|---|---|---|---|---|
| CUSTOMER JWT | Read/write own orders | None | None | None |
| VENDOR JWT | Read/write whole tenant | All four scopes | None | None |
| Tenant ADMIN JWT | Read/write whole tenant | All four scopes | Create/list/revoke | None |
| API key | Explicit `orders:read`/`orders:write` | Explicit catalog/inventory read/write scopes | Never | Never |
| Platform JWT | No tenant business scope | No tenant business scope | None | Tenant administration, metrics/workers |
| Metrics token | None | None | None | Metrics/workers only |

Key format is `oos_live_<8 alphanumeric random characters>_<token_urlsafe(32)>`. The first 12 characters are the public prefix (the literal `oos_live_` plus three random characters). SHA-256 covers **every remaining character**, including the remainder of the nonce and separator. Prefix collisions retry allocation up to ten times. No full plaintext is stored. Only the creation response returns it; lists expose prefix, scopes and lifecycle timestamps.

`hmac.compare_digest` executes even for malformed, unknown, revoked and expired credentials, using a dummy digest when necessary. All invalid-key cases return the same generic 401. This protects digest comparison; it does not promise identical whole-request latency. Valid keys update `last_used_at` with a conditional write at most once per five minutes. DELETE revokes idempotently; expiry is evaluated at authentication time. JWT ADMIN is required to manage keys. Key plaintext must be held in the seller's server secret store.

**WARNING:** public tenant registration and customer signup do not have rate limiting or captcha. Abuse protection belongs to 3B. Unknown signup tenant slugs return a generic 404; successful signup necessarily reveals that a store accepts signup. Duplicate emails are globally conflicting, a documented remaining information boundary.

## A3. Isolation guarantees

Business HTTP handlers use one explicit `Principal`. Repositories receive tenant IDs; individual resource lookups apply tenant ownership before checking role/scope, returning 404 for foreign IDs. Lists/counts use the same tenant predicates. Customer order queries also constrain `customer_id`. Foreign warehouse IDs cannot be used to insert inventory. Shipment ownership is reached through its order.

Trusted workers lock an aggregate by its internal order ID and derive ownership from the order, never the incoming event's tenant claim. Inventory locking filters `order.tenant_id` and follows the established order-first, inventory-second lock order. Worker events and failed-job rows inherit that authoritative tenant. Recovery accepts optional explicit tenant scope and locks order before job. Audit rows are reached by their owned entity; no public audit/payment/failed-job browsing endpoint is added.

The matrix tests 19 tenant method/path combinations against four principal types (ADMIN, VENDOR, CUSTOMER, API key): 76 route/principal combinations, including allowed own-resource creation and forbidden foreign-resource access where applicable. Public onboarding is tested separately. Six platform method/path combinations are classified separately. The registry enumerates actual routes (including docs/OpenAPI HEAD routes) and rejects every unclassified method/path.

There is no PostgreSQL RLS. Trusted internal services can intentionally perform unscoped aggregate lookup. RLS remains a future defense-in-depth layer. Global email uniqueness and platform operational logs are not tenant-private APIs.

## A4. Catalog and pricing

Products have nonnegative `Numeric(12,2)` prices, three uppercase currency letters, immutable SKU/currency and soft archive via `is_active=false`. Prices permit zero. Inventory requires a product in the same tenant and derives its name; worker reservation preserves the order item's catalog snapshot.

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant DB
    Client->>API: Order items, optional currency/buyer fields, idempotency key
    API->>DB: Lookup tenant + principal scope + key
    alt Existing request
        API->>API: Compare canonical validated payload fingerprint
        API-->>Client: Original snapshot or 409 conflict
    else New request
        API->>DB: One product query for all tenant SKUs
        API->>API: Active/missing, common currency, amount bounds
        API->>DB: Snapshot items + order + audit + outbox in one commit
        API-->>Client: 202 accepted
    end
```

Totals use Decimal multiplication/summation and quantize to cents. `3 × 19.99 = 59.97`. Existing orders retain unit prices and names after product edits/archive. Replays resolve before catalog validation, including legacy rows without fingerprints. Currency is optional and otherwise inferred from products. Optional external reference and buyer fields are validated, stored and returned on orders. Buyer values never enter generated domain events; SQLAlchemy hides SQL parameters in exception traces.

| Code | HTTP | Meaning |
|---|---|---|
| unknown_or_inactive_sku | 422 | Sorted offending SKUs in details |
| mixed_currency | 422 | Items span currencies |
| currency_mismatch | 422 | Supplied currency differs |
| order_total_out_of_range | 422 | Total exceeds Numeric(12,2) maximum |
| product_required | 422 | Inventory SKU has no owned product |
| conflict | 409 | Duplicate tenant SKU |
| idempotency_key_conflict | 409 | Same scoped key, different validated payload |
| not_found | 404 | Missing or foreign resource |

Order lists filter status, inclusive created_from/created_to UTC bounds and exact external reference, with consistent pagination totals. Product lists support active status and escaped literal SKU/name prefix search.

## A5. Order recovery

```mermaid
stateDiagram-v2
    CREATED --> INVENTORY_RESERVED
    INVENTORY_RESERVED --> PAID
    PAID --> FULFILLMENT_REQUESTED
    CREATED --> FAILED: retries exhausted + compensation
    INVENTORY_RESERVED --> FAILED: retries exhausted + compensation
    PAID --> FAILED: retries exhausted + compensation
    FAILED --> CREATED: reopen_failed_order / RETRIES_EXHAUSTED only
    FULFILLMENT_REQUESTED --> CANCELLED: refund + release + shipment cancel
```

The diagram abbreviates intermediate PAYMENT_PENDING and other existing graph transitions. Only `transition` and `reopen_failed_order` write lifecycle status. Reopen clears failure reason and records actor/reason in `order_reopened`; the caller adds a fresh `order.created` intent in the same transaction. Old events/markers remain historical. Business declines and cancelled/delivered orders cannot reopen.

| Replay branch | Result |
|---|---|
| Job already REPLAYED | 409 job_already_replayed |
| FAILED / RETRIES_EXHAUSTED | Reopen; fresh event ID; orders queue; job REPLAYED |
| Nonterminal order | Reschedule original identity through outbox; job REPLAYED |
| Business FAILED, CANCELLED, DELIVERED or missing order | 409 order_not_retryable; job unchanged |

Suspended tenants cannot be recovered (409 tenant_suspended). Discard marks a job DISCARDED with an actor audit and no event. Discarded jobs cannot replay. These are service functions, with no HTTP recovery/discard endpoints. PostgreSQL row locking serializes double replay: exactly one succeeds and creates one fresh event. Refund/release compensation remains idempotent. A recovered order reserves again and can authorize a new payment; historical refunded attempts remain visible.

## A6. Provider adapter architecture

`PaymentProvider` defines authorize/refund; `ShippingProvider` defines create/cancel. Frozen result dataclasses carry typed status, provider reference and sanitized metadata. `ProviderDeclined` is a BusinessError, never retried. `ProviderUnavailable` is a TransientError and participates in existing retry/backoff/final-compensation handling. Services persist provider name and authorization/shipment references. Raw metadata is not copied into logs or events.

Only `mock` adapters exist. Settings reject unknown provider names at startup. Non-local environments require explicit `ALLOW_MOCK_PROVIDERS=true`. All FAIL-PAYMENT, FAIL-SHIPMENT and FAIL-TRANSIENT SKU behavior lives in mocks and is gated by local-only failure injection; workers contain no SKU-prefix checks. Transient injection happens during payment authorization, so an earlier committed inventory reservation persists across retries and is released on exhaustion.

Every adapter call receives a deterministic key. Authorize uses `<order UUID>:authorize:<reopen count>`; shipment creation uses `<order UUID>:shipment:<reopen count>`; refund uses `<order UUID>:refund:<payment UUID>`; cancellation uses `<order UUID>:cancel:<shipment UUID>`. Redelivery keeps the same key; explicit reopen advances the attempt. Mock references are deterministic SHA-256 derivatives. Real adapters must respect those keys and sanitize their `raw` dictionaries.

**3B risk:** adapter calls occur while database row locks and a transaction are held. Real network calls need bounded connect/read timeouts, provider-side idempotency and reconciliation, and a design that shortens transactions without losing durable intent. No real HTTP provider, credentials, webhook or deployment change is implemented here.

## A7. Event catalog and consumers

All DomainEvents include optional `tenant_id` alongside event ID/type, aggregate ID, occurred_at, correlation_id and payload. Existing legacy producers remain parseable. Order creation/cancellation and worker emits populate it from the persisted order. Outbox and failed jobs carry tenant columns; replay preserves the tenant. Markers remain unique per consumer/event identity.

| Event | Queue | Business payload |
|---|---|---|
| order.created | orders | order_id |
| inventory.reserved | payments | order_id, reason |
| inventory.failed | inventory | order_id, reason |
| payment.authorized | shipments | order_id, reason |
| payment.failed | payments | order_id, reason |
| shipment.created / shipment.failed | shipments | order_id, reason |
| order.cancelled | orders | order_id |

Informational events remain harmless to the workflow consumer. Contracts also retain shipment.delayed/order.completed names; this phase adds no new active producer for them. Buyer name/email/phone are forbidden in generated event payloads. Outbox acceptance is transactional; relay publication is at least once with confirms; consumption combines row locking, markers and state guards. In-flight work continues for suspended tenants; suspension blocks new authenticated requests and operator replay.

## A8. Configuration reference

| Variable | Default | Meaning / production note |
|---|---|---|
| PAYMENT_PROVIDER | mock | Only mock is registered; unknown names fail validation |
| SHIPPING_PROVIDER | mock | Same startup validation |
| ALLOW_MOCK_PROVIDERS | false | Must be explicitly true for mock use outside local/development/test |
| PROCESSED_EVENTS_RETENTION_DAYS | 30 | Positive days; must exceed maximum redelivery/retry window |
| FAILURE_INJECTION_ENABLED | false | Local/development/test only; now implemented inside mocks |
| FAILURE_TRANSIENT_ATTEMPTS | 2 | Mock failures before successful authorization; EXHAUST exceeds retry budget |
| TASK_MAX_RETRIES | 3 | Existing retry budget, also controls EXHAUST injection |
| METRICS_TOKEN | unset | Optional metrics/workers token; platform JWT alternative in every environment |
| API_PORT | 8000 | Host Compose port; use e.g. 18000 for Windows port conflicts |

Existing DATABASE_URL, SECRET_KEY, broker/backend and outbox configuration retain their documented meanings. No tenant is inferred from an environment default. The explicit default-tenant UUID is a migration/fixture/seed constant, not an ORM ownership fallback. Relay maintenance deletes published outbox rows under its separate seven-day policy, and processed markers hourly under the new policy. Failed jobs are never automatically deleted. RabbitMQ currently has no queue TTL, so operators must bound replay/redelivery history before enabling finite marker retention.

## A9. API reference

All business paths below have `/api/v1` prefix. Standard authentication failures are 400 ambiguous_credentials, 401 unauthenticated and 403 tenant_suspended; schema validation is 422. Scope denial on an owned resource is 403; foreign IDs are 404.

| Method | Path | Authentication / scope | Ownership / other errors |
|---|---|---|---|
| POST | /tenants/register | Public | Atomic tenant+owner; duplicate slug/email409 |
| POST | /auth/signup | Public, tenant_slug required | CUSTOMER only; unknown store404, suspended403, email409 |
| POST | /auth/login | Public credentials | JWT contains informational tid; bad credentials401 |
| GET | /auth/me | Any active JWT | Reloaded user only; key403 |
| POST | /api-keys | Tenant ADMIN JWT | Owned key; full secret once |
| GET | /api-keys | Tenant ADMIN JWT | Owned list, no secrets |
| DELETE | /api-keys/{key_id} | Tenant ADMIN JWT | Owned row first; foreign404; idempotent204 |
| POST | /products | Staff JWT / catalog:write key | Owned product; duplicate409 |
| GET | /products | Staff JWT / catalog:read key | Owned filtered page/count |
| GET | /products/{product_id} | Staff JWT / catalog:read key | Owned row or404 |
| PATCH | /products/{product_id} | Staff JWT / catalog:write key | Owned row; immutable SKU/currency422 |
| POST | /inventory/warehouses | Staff JWT / inventory:write key | Creates owned warehouse |
| POST | /inventory | Staff JWT / inventory:write key | Owned warehouse404; product_required422 |
| GET | /inventory | Staff JWT / inventory:read key | Owned page/count |
| GET | /inventory/low-stock | Staff JWT / inventory:read key | Owned low stock, up to100 |
| GET | /inventory/{inventory_id} | Staff JWT / inventory:read key | Owned row or404 |
| PATCH | /inventory/{inventory_id} | Staff JWT / inventory:write key | Owned row; product_required422 |
| POST | /orders | JWT / orders:write key | Principal-scoped idempotency; catalog422; conflict409 |
| GET | /orders | JWT / orders:read key | Customer own; staff/key whole tenant; filtered totals |
| GET | /orders/{order_id} | JWT / orders:read key | Customer own or404; tenant-first |
| POST | /orders/{order_id}/cancel | JWT / orders:write key | Owned/customer own; terminal409; atomic compensation |
| POST | /shipments/{shipment_id}/events | Staff JWT / orders:write key | Through owned order; customers403, foreign404 |
| GET | /admin/tenants | Platform JWT | Page/count; optional status filter |
| GET | /admin/tenants/{tenant_id} | Platform JWT | Missing404 |
| POST | /admin/tenants/{tenant_id}/suspend | Platform JWT | Locked status change + actor audit |
| POST | /admin/tenants/{tenant_id}/activate | Platform JWT | Locked status change + actor audit |
| GET | /system/metrics | Platform JWT or metrics token | Global operational data |
| GET | /system/workers | Platform JWT or metrics token | Existing static monitoring |
| GET | /system/health | Public | Liveness |
| GET | /system/ready | Public | Dependency readiness, may503 |
| GET/HEAD | /api/v1/openapi.json | Public | Full path, schema |
| GET/HEAD | /docs, /docs/oauth2-redirect, /redoc | Public | Full paths, documentation |

## A10. Operations runbook

1. Use the PowerShell curl quickstart in README to register a tenant, log in, create a product/warehouse/stock, issue a scoped key and place an order. Registration creates an owner, not a platform administrator. Store the creation-only secret securely.
2. JWT owner: DELETE `/api/v1/api-keys/<id>` to revoke. GET lists lifecycle metadata without recovering plaintext. Rotate by creating another key before revoking the old one; integration idempotency remains tenant-scoped.
3. Provision a platform identity using the local seed for demos (public credentials; never production). Log in via the running API. POST `/api/v1/admin/tenants/<id>/suspend` or `/activate`. The metrics token cannot administer tenants.
4. Inspect failed jobs with an explicit tenant predicate:

```sql
SELECT id, aggregate_id, event_id, status, retry_count, created_at
FROM failed_jobs WHERE tenant_id = '<tenant UUID>' ORDER BY created_at DESC;
```

5. In an authorized application environment with the injection problem/provider outage resolved:

```python
from uuid import UUID
from app.db.session import SessionLocal
from app.services.failed_jobs import replay_failed_job, discard_failed_job

with SessionLocal() as db:
    intent = replay_failed_job(db, UUID("<job>"), actor=UUID("<operator-user>"), tenant_id=UUID("<tenant>"))
# Alternative operator decision: discard_failed_job with the same scoped arguments.
```

Actor is an existing User UUID for audit FK integrity. These internal operator functions assume the caller has already authorized that operator; they are not unauthenticated HTTP commands. Disable injection on the worker before recovering EXHAUST demo orders. A second replay returns409 and cannot create another saga attempt.

6. Stop application writers, back up the database and run `alembic upgrade head` against the intended DATABASE_URL. Revision0007 backfills legacy rows. Never downgrade while workers/relay/API write. Downgrade deliberately refuses multi-tenant or non-reconstructible data; export/migrate it explicitly rather than forcing a lossy rollback.
7. Use a dedicated database whose name ends `_test` for PostgreSQL tests. Pytest recreates tables; never point it at the application database. Migration tests use a unique disposable schema inside that test DB. Run both E2E scripts against the real local stack; they retain uniquely named demo rows and restart selected services without deleting volumes.

## A11. Limitations and deferred work

No real payment/carrier adapters, credentials, provider HTTP calls/webhooks, outbound webhooks, notifications, UI, rate limits, refresh tokens, RLS, production deployment or RabbitMQ volume changes are included. Currency validation checks shape, not an authoritative ISO currency registry. Emails remain globally unique. Historical failed-job args/kwargs and ambiguous legacy reservations need manual reconciliation. VALIDATED legacy orders and automatic shipment-event-to-order SHIPPED/DELIVERED transitions remain unchanged. Monitoring/delayed-shipment detection remain inherited stubs. A database outage can still defeat failure persistence; no independent durable failure store is added. Published outbox retention and marker retention require an operational redelivery policy.

# PART B — Verification report

## B1. Summary

- Main contained Phase2 migrations0004–0006 and the report; it was fast-forwarded before creating this branch.
- Tenant-owned data, database-authoritative principals, keys and platform separation are implemented.
- Catalog snapshots replace intake placeholder pricing; metadata stays out of generated events/SQL parameters.
- Technical recovery creates a fresh saga; business failures remain terminal.
- Mock provider boundaries, tenant administration and marker retention are implemented.
- SQLite and real PostgreSQL results and production coverage follow below.
- Real Docker E1–E14 and three inherited supplements are exercised; harness failures and corrections are disclosed.
- Hosted CI and production/external-provider behavior are not claimed.

## B2. Task status

| ID | Task | Status | Commit hash(es) | Notes |
|---|---|---|---|---|
| 3A.1 | Request ID | DONE | 7be17dc, d49f85b | Includes later500 echo regression |
| 3A.2 | Models/migration | DONE | a06c1b0 | SQLite/PG legacy roundtrip and refusal |
| 3A.3 | Principal/onboarding/keys | DONE | 277bab6 | Uniform401, scope/lifecycle tests |
| 3A.4 | Resource isolation | DONE | 277bab6, d49f85b | Matrix, registry, PG worker collision test |
| 3A.5 | Catalog/pricing | DONE | d49f85b | Decimal, snapshots, filters, PII failure test |
| 3A.6 | Recovery | DONE | d49f85b | Four branches, compensation, PG double replay |
| 3A.7 | Providers | DONE | d49f85b | Mock only, deterministic call keys |
| 3A.8 | Platform admin/demo seed | DONE | d49f85b | Suspension/audits, local seed/show-once |
| 3A.9 | Marker retention | DONE | d49f85b | Strict boundary; failed jobs retained |
| 3A.10 | Quality/coverage/PG | DONE | d49f85b | Local checks; hosted CI NOT VERIFIED |
| 3A.11 | Real-stack proof | DONE | d49f85b | E1–E14; legacy migration raw output below |

## B3. TDD evidence

Short raw excerpts from observed pre-implementation failures are retained here; later complete command output appears in B4–B6. Counts refer to those specific runs, not the final expanded suite.

| Task | Test / observed before | Observed after / controls |
|---|---|---|
| 1 | request ID parameter cases: `7 failed, 3 passed, 1 skipped`; long PG header: `StringDataRightTruncation ... varchar(255)` | Targeted PG request-ID run: `11 passed`; valid/absent IDs are positive controls |
| 1 supplement | test_unhandled_error_echoes_effective_request_id: missing `x-request-id`, `KeyError` on500 | Targeted catalog/request-ID run: `16 passed, 1 skipped` |
| 2 | Tenant model import failed; legacy upgrade test lacked `tenants` table | SQLite legacy roundtrip and PG roundtrip/refusal passed; reserved/invalid slug cases added |
| 3 | First principal tests: `3 failed`, registration/key routes404 | Auth suite passed; compare_digest, key lifecycle and throttling controls retained |
| 3 supplement | unknown signup tenant with known email returned409 instead of404 | Tenant lookup precedes duplicate-email check; regression passes |
| 4 | matrix: foreign order GET returned200 instead of404; tenant owner metrics returned200 instead of403 | Four-role matrix and registry pass; platform JWT and forged-claim cases are positive controls |
| 5 | First catalog tests: `3 failed`, products404 | Targeted catalog/isolation/hardening: `16 passed` before further expansion |
| 5 supplement | overflow order returned202 instead of422; SQL exception trace contained synthetic buyer values | Total guard + hide_parameters; targeted catalog/request-ID run: `16 passed, 1 skipped` |
| 5 controls | Legacy hash/null-hash replay, one-query product lookup, date/pagination filters | Positive controls added after core implementation; not claimed as reds |
| 6 | New recovery tests: `TypeError: replay_failed_job() got an unexpected keyword argument 'actor'`; missing discard import; `5 failed` | Recovery + inherited reliability selection: `19 passed`; PG double replay: `1 passed` |
| 7 | Registry test `DID NOT RAISE ValidationError`; provider imports `ModuleNotFoundError`; `3 failed` | Provider/failure/cancel/recovery/catalog selection: `30 passed` |
| 8 | Platform admin test expected403, route returned404; `1 failed` | Platform+matrix selection: `8 passed`; seed/in-flight tests are later positive controls |
| 9 | `ImportError: cannot import name cleanup_processed`; `1 failed` | Retention/config selection: `14 passed`; deterministic PG boundary rerun: `1 passed` |
| 10–11 | Coverage exploration found provider registry85%; initial PG boundary expected1 deleted2; E12 harness failed | Added meaningful invalid-runtime controls, fixed boundary clock setup and used HTTP login in E12; final outputs below |

The first PG retention test compared a cutoff derived in one transaction with a new cleanup transaction clock, so its exact-boundary row was already older by microseconds. The corrected test freezes the captured **database-derived** instant for cleanup and keeps all three strict assertions (before deleted; equal/after retained). Production still uses database time and strict `<`. No production retention defect is hidden.

Initial E12 created a token with host settings while the Docker API used its configured secret. The runner now authenticates the platform user through the API, exactly as a real client does. Initial E9–E11/E13–E14 already passed; final E9–E14 all pass. No business assertion was relaxed.

## B4. Raw quality, database and coverage outputs

```text
All checks passed!
107 files already formatted
Success: no issues found in 89 source files
```

SQLite production coverage (tests omitted by pyproject configuration):

```text
........................................................................ [ 29%]
........................................................................ [ 58%]
.............................sssssss............................s....... [ 88%]
........s....................                                            [100%]

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
app\api\v1\endpoints\inventory.py      72     13    82%   22, 81, 87-88, 99-107
app\api\v1\endpoints\orders.py         30      2    93%   47-49
app\api\v1\endpoints\products.py       27      2    93%   37-38
app\api\v1\endpoints\shipments.py      22      6    73%   29-34
app\api\v1\endpoints\tenancy.py        25      0   100%
app\api\v1\router.py                   11      0   100%
app\auth\dependencies.py               16      0   100%
app\auth\principal.py                  93      4    96%   59, 66, 83, 96
app\auth\security.py                   21      0   100%
app\core\config.py                     46      0   100%
app\core\exceptions.py                 24      0   100%
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
app\repositories\base.py               19      1    95%   17
app\repositories\inventory.py          17      1    94%   13
app\repositories\orders.py             38      2    95%   37, 49
app\repositories\outbox.py             10      0   100%
app\repositories\users.py               7      0   100%
app\schemas\auth.py                    23      0   100%
app\schemas\common.py                  15      0   100%
app\schemas\inventory.py               30      1    97%   40
app\schemas\orders.py                  53      0   100%
app\schemas\products.py                30      1    97%   26
app\schemas\shipments.py                6      0   100%
app\schemas\tenancy.py                 40      0   100%
app\services\auth.py                   36      3    92%   24-26, 47
app\services\catalog.py                53      2    96%   39, 64
app\services\failed_jobs.py            86      1    99%   67
app\services\inventory.py              45      5    89%   37, 52-55
app\services\orders.py                105      3    97%   35, 100, 135
app\services\payments.py               32      0   100%
app\services\shipping.py               28      0   100%
app\services\tenancy.py                49      3    94%   61-63
app\workers\celery_app.py               8      0   100%
app\workers\outbox_relay.py            69      2    97%   30, 90
app\workers\tasks.py                  132      0   100%
-----------------------------------------------------------------
TOTAL                                1851     64    97%
Coverage JSON written to file .coverage-sqlite.json

236 passed, 9 skipped in 139.79s (0:02:19)
```

Full real PostgreSQL run, including postgres-marked tests:

```text
........................................................................ [ 29%]
........................................................................ [ 58%]
........................................................................ [ 88%]
.............................                                            [100%]

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
app\api\v1\endpoints\inventory.py      72     13    82%   22, 81, 87-88, 99-107
app\api\v1\endpoints\orders.py         30      2    93%   47-49
app\api\v1\endpoints\products.py       27      2    93%   37-38
app\api\v1\endpoints\shipments.py      22      6    73%   29-34
app\api\v1\endpoints\tenancy.py        25      0   100%
app\api\v1\router.py                   11      0   100%
app\auth\dependencies.py               16      0   100%
app\auth\principal.py                  93      4    96%   59, 66, 83, 96
app\auth\security.py                   21      0   100%
app\core\config.py                     46      0   100%
app\core\exceptions.py                 24      0   100%
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
app\repositories\base.py               19      1    95%   17
app\repositories\inventory.py          17      1    94%   13
app\repositories\orders.py             38      2    95%   37, 49
app\repositories\outbox.py             10      0   100%
app\repositories\users.py               7      0   100%
app\schemas\auth.py                    23      0   100%
app\schemas\common.py                  15      0   100%
app\schemas\inventory.py               30      1    97%   40
app\schemas\orders.py                  53      0   100%
app\schemas\products.py                30      1    97%   26
app\schemas\shipments.py                6      0   100%
app\schemas\tenancy.py                 40      0   100%
app\services\auth.py                   36      3    92%   24-26, 47
app\services\catalog.py                53      2    96%   39, 64
app\services\failed_jobs.py            86      1    99%   67
app\services\inventory.py              45      5    89%   37, 52-55
app\services\orders.py                105      3    97%   35, 100, 135
app\services\payments.py               32      0   100%
app\services\shipping.py               28      0   100%
app\services\tenancy.py                49      3    94%   61-63
app\workers\celery_app.py               8      0   100%
app\workers\outbox_relay.py            69      1    99%   90
app\workers\tasks.py                  132      3    98%   87-89
-----------------------------------------------------------------
TOTAL                                1851     66    96%
Coverage JSON written to file .coverage-postgres.json

245 passed in 169.70s (0:02:49)
```

Production line coverage (independent run files; tests omitted):

| Module | SQLite % | PostgreSQL % | Target ≥90% |
|---|---:|---:|---|
| `app/api/v1/endpoints/admin.py` | 100.00 | 100.00 | PASS |
| `app/api/v1/endpoints/products.py` | 92.59 | 92.59 | PASS |
| `app/api/v1/endpoints/tenancy.py` | 100.00 | 100.00 | PASS |
| `app/auth/dependencies.py` | 100.00 | 100.00 | PASS |
| `app/auth/principal.py` | 95.70 | 95.70 | PASS |
| `app/auth/security.py` | 100.00 | 100.00 | PASS |
| `app/domain/order_state.py` | 100.00 | 100.00 | PASS |
| `app/providers/__init__.py` | 100.00 | 100.00 | PASS |
| `app/providers/contracts.py` | 90.00 | 90.00 | PASS |
| `app/providers/mock.py` | 100.00 | 100.00 | PASS |
| `app/providers/registry.py` | 100.00 | 100.00 | PASS |
| `app/schemas/products.py` | 96.67 | 96.67 | PASS |
| `app/schemas/tenancy.py` | 100.00 | 100.00 | PASS |
| `app/services/auth.py` | 91.67 | 91.67 | PASS |
| `app/services/catalog.py` | 96.23 | 96.23 | PASS |
| `app/services/failed_jobs.py` | 98.84 | 98.84 | PASS |
| `app/services/tenancy.py` | 93.88 | 93.88 | PASS |

Overall SQLite: **96.54%**; PostgreSQL: **96.43%**.

Recovery function line coverage from the SQLite production run:

| Function | Executed / executable lines | Coverage |
|---|---:|---:|
| `replay_failed_job` | 33 / 33 | 100.00% |
| `discard_failed_job` | 9 / 9 | 100.00% |
| `reopen_failed_order` | 6 / 6 | 100.00% |

## B5. Migrations

`alembic/versions/0007_tenancy_core.py` (revision0007_tenancy, down0006_replay) creates tenants/API keys/products; owns users/orders/warehouses/inventory; adds tenant metadata to outbox/failed jobs, buyer/idempotency scope to orders and provider references to payments/shipments. It uses portable batch alterations and does not add labels to native PostgreSQL enums. DISCARDED is added only to the existing VARCHAR-backed JobStatus mapping.

Legacy default store UUID is `00000000-0000-0000-0000-000000000001`, active, INR. Every old user/order/stock/warehouse/outbox/failed job is attached. Principal scopes use hyphenated user UUIDs. Catalog derives distinct legacy SKUs, inventory names and latest order-item price or49.99 fallback, with tenant-default currency. Payment provider strings over40 fail upgrade clearly rather than truncate.

Downgrade validates **before** destructive operations: multi-tenant ownership, nullable customers, platform admins, API keys, global uniqueness conflicts, buyer/provider-reference fields and catalog changes that cannot be reconstructed all refuse. Reconstructible single-tenant legacy data roundtrips without losing the seeded order. The disposable PG test begins at0006, seeds legacy user/warehouse/inventory/order/item, checks19.99INR product and ownership, downgrades, upgrades, inserts another tenant and verifies refusal.

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial, initial order platform schema
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_idempotency, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Running upgrade 0003_reservation -> 0004_outbox, Transactional event intents; new table has no historical backfill.
INFO  [alembic.runtime.migration] Running upgrade 0004_outbox -> 0005_consumers, Consumer deduplication markers are committed with handler effects.
INFO  [alembic.runtime.migration] Running upgrade 0005_consumers -> 0006_replay, Store full failed-event identifiers and portable replay status.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0006_replay -> 0007_tenancy, Tenant ownership and catalog backfill, without changing native enums.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0007_tenancy -> 0006_replay, Tenant ownership and catalog backfill, without changing native enums.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0006_replay -> 0007_tenancy, Tenant ownership and catalog backfill, without changing native enums.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0007_tenancy -> 0006_replay, Tenant ownership and catalog backfill, without changing native enums.
LEGACY BACKFILL/ROUNDTRIP PASS; MULTI-TENANT DOWNGRADE REFUSAL PASS
.
1 passed in 7.51s
```

## B6. Real Docker experiments

E1–E8 plus S5/S6/S10:

```text
PASS E1 happy path: fulfillment_requested; payment=1 shipment=1 stock=(9,1)
PASS E2 insufficient inventory: failed/INSUFFICIENT_INVENTORY; stock=(0,0); retry jobs=0
PASS E3 payment decline: failed/PAYMENT_DECLINED; stock=(10,0); retry jobs=0
PASS E4 shipment failure: failed/SHIPMENT_FAILED; stock=(10,0); retry jobs=0
PASS E5 worker crash and redelivery: SIGKILL before consumption; resumed; broker redelivery; one reservation/payment/shipment
PASS E6 broker outage: five HTTP 202; pending backlog=5; recovered all five; no duplicate effects
PASS E7 request idempotency: one order; one order.created intent; one payment/shipment
PASS E8 concurrent orders: 30-vs-10: fulfilled=10 failed=20 available=0 reserved=10 conserved=10
PASS S5 transient retry: transient retry succeeds; exactly one reservation/payment/shipment
PASS S6 exhausted retry and replay: retries exhausted=3; compensated; fresh replay without injection completed; one payment/shipment
PASS S10 cancellation at every requested state: CREATED/INVENTORY_RESERVED/PAID/FULFILLMENT_REQUESTED; repeated cancel; stock/refund/shipment correct
RESULT: 11/11 experiments passed
NAME                                                     IMAGE                                                  COMMAND                  SERVICE        CREATED              STATUS                   PORTS
event-driven-order-orchestration-system-api-1            event-driven-order-orchestration-system-api            "uvicorn app.main:ap…"   api            3 minutes ago        Up 3 minutes             0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
event-driven-order-orchestration-system-outbox-relay-1   event-driven-order-orchestration-system-outbox-relay   "python -m app.worke…"   outbox-relay   3 minutes ago        Up 7 seconds             8000/tcp
event-driven-order-orchestration-system-postgres-1       postgres:16-alpine                                     "docker-entrypoint.s…"   postgres       2 months ago         Up 2 hours (healthy)     0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
event-driven-order-orchestration-system-rabbitmq-1       rabbitmq:3.13-management-alpine                        "docker-entrypoint.s…"   rabbitmq       2 months ago         Up 2 minutes (healthy)   0.0.0.0:5672->5672/tcp, [::]:5672->5672/tcp, 0.0.0.0:15672->15672/tcp, [::]:15672->15672/tcp
event-driven-order-orchestration-system-redis-1          redis:7-alpine                                         "docker-entrypoint.s…"   redis          2 months ago         Up 2 hours               0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
event-driven-order-orchestration-system-scheduler-1      event-driven-order-orchestration-system-scheduler      "celery -A app.worke…"   scheduler      3 minutes ago        Up 3 minutes             8000/tcp
event-driven-order-orchestration-system-worker-1         event-driven-order-orchestration-system-worker         "celery -A app.worke…"   worker         About a minute ago   Up About a minute        8000/tcp
```

E9–E14 and stack status:

```text
PASS E9 two tenant workflows: two registered stores; same SKU; key orders completed independently; stock=(17,3) each
PASS E10 isolation: B JWT/key foreign order/product/inventory=404; list totals=1; A IDs absent; shared idempotency independent
PASS E11 pricing: 19.99 x 3=59.97; product changed to25; snapshot unchanged; unknown SKU=422
PASS E12 key lifecycle and suspension: revoked/expired=401; suspended JWT/key=403; activation restored both
PASS E13 exhausted recovery: retries exhausted=3; compensated; fresh replay without injection completed; one payment/shipment
PASS E14 cross-tenant concurrency: 20 concurrent orders per tenant, shared SKU: fulfilled=10 failed=10 each; stock independent
RESULT: 6/6 tenancy experiments passed
NAME                                                     IMAGE                                                  COMMAND                  SERVICE        CREATED          STATUS                   PORTS
event-driven-order-orchestration-system-api-1            event-driven-order-orchestration-system-api            "uvicorn app.main:ap…"   api            19 seconds ago   Up 4 seconds             0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
event-driven-order-orchestration-system-outbox-relay-1   event-driven-order-orchestration-system-outbox-relay   "python -m app.worke…"   outbox-relay   19 seconds ago   Up 4 seconds             8000/tcp
event-driven-order-orchestration-system-postgres-1       postgres:16-alpine                                     "docker-entrypoint.s…"   postgres       2 months ago     Up 2 hours (healthy)     0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
event-driven-order-orchestration-system-rabbitmq-1       rabbitmq:3.13-management-alpine                        "docker-entrypoint.s…"   rabbitmq       2 months ago     Up 6 minutes (healthy)   0.0.0.0:5672->5672/tcp, [::]:5672->5672/tcp, 0.0.0.0:15672->15672/tcp, [::]:15672->15672/tcp
event-driven-order-orchestration-system-redis-1          redis:7-alpine                                         "docker-entrypoint.s…"   redis          2 months ago     Up 2 hours               0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
event-driven-order-orchestration-system-scheduler-1      event-driven-order-orchestration-system-scheduler      "celery -A app.worke…"   scheduler      14 seconds ago   Up 1 second              8000/tcp
event-driven-order-orchestration-system-worker-1         event-driven-order-orchestration-system-worker         "celery -A app.worke…"   worker         16 seconds ago   Up 3 seconds             8000/tcp
```

```text
failure_injection_enabled= False
NAME                                                     IMAGE                                                  COMMAND                  SERVICE        CREATED          STATUS                     PORTS
event-driven-order-orchestration-system-api-1            event-driven-order-orchestration-system-api            "uvicorn app.main:ap…"   api            6 minutes ago    Up 6 minutes               0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
event-driven-order-orchestration-system-migrate-1        event-driven-order-orchestration-system-migrate        "alembic upgrade head"   migrate        15 minutes ago   Exited (0) 6 minutes ago   
event-driven-order-orchestration-system-outbox-relay-1   event-driven-order-orchestration-system-outbox-relay   "python -m app.worke…"   outbox-relay   6 minutes ago    Up 6 minutes               8000/tcp
event-driven-order-orchestration-system-postgres-1       postgres:16-alpine                                     "docker-entrypoint.s…"   postgres       2 months ago     Up 2 hours (healthy)       0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
event-driven-order-orchestration-system-rabbitmq-1       rabbitmq:3.13-management-alpine                        "docker-entrypoint.s…"   rabbitmq       2 months ago     Up 12 minutes (healthy)    0.0.0.0:5672->5672/tcp, [::]:5672->5672/tcp, 0.0.0.0:15672->15672/tcp, [::]:15672->15672/tcp
event-driven-order-orchestration-system-redis-1          redis:7-alpine                                         "docker-entrypoint.s…"   redis          2 months ago     Up 2 hours                 0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
event-driven-order-orchestration-system-scheduler-1      event-driven-order-orchestration-system-scheduler      "celery -A app.worke…"   scheduler      6 minutes ago    Up 6 minutes               8000/tcp
event-driven-order-orchestration-system-worker-1         event-driven-order-orchestration-system-worker         "celery -A app.worke…"   worker         6 minutes ago    Up 6 minutes               8000/tcp
```

The initial E12 failure is disclosed in B3. Both runners leave unique demo rows; no application volumes/data were deleted. Tenancy runner restores injection=false and the runtime check above verifies it. E14 starts with three historical units already reserved per tenant from E9, adds ten available units, then verifies exactly ten additional fulfillments and final reserved13, available0 in each store. E13 fails before any authorization exists, so it has zero historical refunds and exactly one successful payment/shipment after recovery. The SQLite S6 extension additionally starts after authorization and proves its earlier refund occurs exactly once.

## B7. Isolation evidence

`test_tenant_resource_isolation_matrix` runs four parameter cases, covering the 19 tenant method/path entries: 76 route/principal combinations. It covers foreign product/order/inventory reads and writes, foreign warehouse references, shipment mutation, key revocation, all owned lists/counts, scoped creation, self identity and independent shared idempotency keys/SKUs. Auth/scope denial on disallowed own endpoints remains403; foreign IDs remain404. Catalog mutation attempts do not change A's data; shipment and stock checks retain strict invariants.

`test_route_registry_has_no_unclassified_routes` checks actual route method/path pairs against public, tenant and platform sets. New routes fail until classified. PG worker test processes eight colliding-SKU events across two tenants and observes two reservations/two failures in each. E10 checks both JWT and key against foreign resources and owned list totals; E14 processes40 real requests with independent stock.

```text
.....                                                                    [100%]
5 passed in 8.64s
```

## B8. Phase 2 carry-over

| Item | Status | Reason |
|---|---|---|
| Unbounded request IDs / PG correlation truncation | RESOLVED | Strict64-character acceptance; invalid IDs replaced;500 echo covered |
| Replay did not restart terminal technical sagas | RESOLVED | Controlled reopen, fresh event, full completion proof |
| No provider boundary | RESOLVED for this phase | Interfaces + mock registry; real integrations deferred3B |
| Processed marker growth | RESOLVED with policy caveat | Hourly30-day cleanup; operator must bound redelivery window |
| Historical ambiguous reservations / old args-kwargs jobs | DEFERRED | Manual reconciliation outside this phase |
| Legacy VALIDATED states | DEFERRED | No new native enum values or graph reconciliation |
| Shipment events do not advance order SHIPPED/DELIVERED | DEFERRED | Existing behavior; no requested new order commands |
| DB outage can defeat failure persistence | DEFERRED | Independent durable failure store not introduced |
| RabbitMQ volume/HA/filesystem-loss resilience | DEFERRED | Explicit deployment/volume out of scope |
| Static worker monitoring/delayed-shipment stub | DEFERRED | No requested monitoring redesign |
| Exact mid-transaction OS kill / relay confirm-commit-gap kill | DEFERRED | Inherited kill-before-consumption plus rollback/redelivery proof only |
| Hosted CI/branch policy/pre-commit hook execution | DEFERRED | Local checks verified; no remote invocation authorized/performed |
| Multi-tenancy | RESOLVED at application layer | Explicit scoping + DB ownership + PG/E2E; RLS deferred |

## B9. Files and commits

Snapshot immediately before the report-only commit; the report deliberately does not include its own hash/diff.

```text
.env.example                           |   4 +
 README.md                              |  61 +++++++-
 alembic/versions/0007_tenancy_core.py  | 251 +++++++++++++++++++++++++++++++++
 app/api/v1/endpoints/admin.py          |  79 +++++++++++
 app/api/v1/endpoints/health.py         |  44 +++---
 app/api/v1/endpoints/inventory.py      |  91 +++++++++---
 app/api/v1/endpoints/orders.py         |  38 +++--
 app/api/v1/endpoints/products.py       |  50 +++++++
 app/api/v1/endpoints/shipments.py      |  17 ++-
 app/api/v1/endpoints/tenancy.py        |  36 +++++
 app/api/v1/router.py                   |   7 +-
 app/auth/dependencies.py               |  31 +---
 app/auth/principal.py                  | 145 +++++++++++++++++++
 app/core/config.py                     |   8 ++
 app/db/session.py                      |   2 +-
 app/domain/order_state.py              |  12 ++
 app/events/contracts.py                |   1 +
 app/main.py                            |   3 +
 app/middleware/request_context.py      |   4 +-
 app/models/entities.py                 | 108 ++++++++++++--
 app/models/enums.py                    |   1 +
 app/providers/__init__.py              |   1 +
 app/providers/contracts.py             |  59 ++++++++
 app/providers/mock.py                  |  54 +++++++
 app/providers/registry.py              |  17 +++
 app/repositories/inventory.py          |  22 ++-
 app/repositories/orders.py             |  47 +++++-
 app/repositories/outbox.py             |   1 +
 app/schemas/auth.py                    |   3 +
 app/schemas/inventory.py               |  16 ++-
 app/schemas/orders.py                  |  12 +-
 app/schemas/products.py                |  38 +++++
 app/schemas/tenancy.py                 |  54 +++++++
 app/services/auth.py                   |  17 ++-
 app/services/catalog.py                |  78 ++++++++++
 app/services/failed_jobs.py            | 114 ++++++++++++---
 app/services/inventory.py              |   5 +-
 app/services/orders.py                 |  85 +++++++----
 app/services/payments.py               |  32 ++++-
 app/services/shipping.py               |  22 ++-
 app/services/tenancy.py                |  73 ++++++++++
 app/tests/conftest.py                  |  56 ++++++--
 app/tests/test_auth_and_orders.py      |  19 ++-
 app/tests/test_cancel_hardening.py     |  25 +++-
 app/tests/test_catalog_pricing.py      | 237 +++++++++++++++++++++++++++++++
 app/tests/test_demo_seed.py            |  30 ++++
 app/tests/test_exception_hardening.py  |   4 +-
 app/tests/test_failure_scenarios.py    |  39 +++--
 app/tests/test_hardening.py            |   8 +-
 app/tests/test_marker_retention.py     |  34 +++++
 app/tests/test_metrics_hardening.py    |  16 +--
 app/tests/test_migrations_hardening.py |   5 +-
 app/tests/test_order_recovery.py       | 110 +++++++++++++++
 app/tests/test_order_state.py          |  27 ++++
 app/tests/test_platform_tenants.py     |  61 ++++++++
 app/tests/test_postgres_reliability.py |  98 ++++++++++++-
 app/tests/test_principal_auth.py       | 141 ++++++++++++++++++
 app/tests/test_providers.py            |  86 +++++++++++
 app/tests/test_reliability_edges.py    |  11 +-
 app/tests/test_request_id.py           |  64 +++++++++
 app/tests/test_settings_hardening.py   |   2 +-
 app/tests/test_tenancy_migration.py    | 125 ++++++++++++++++
 app/tests/test_tenant_isolation.py     | 233 ++++++++++++++++++++++++++++++
 app/tests/test_tenant_models.py        |  24 ++++
 app/tests/test_worker_flow.py          |  31 +++-
 app/workers/outbox_relay.py            |  11 +-
 app/workers/tasks.py                   |  29 +---
 pyproject.toml                         |   1 +
 scripts/e2e_reliability.py             |  51 +++++--
 scripts/e2e_tenancy.py                 | 235 ++++++++++++++++++++++++++++++
 scripts/seed_demo.py                   |  46 +++++-
 scripts/smoke_hardening.py             |  10 +-
 72 files changed, 3353 insertions(+), 259 deletions(-)
```

```text
d49f85b feat(tenancy): integrate catalog recovery providers and tenant operations
277bab6 feat(auth): resolve tenant principals and enforce resource isolation
a06c1b0 feat(tenancy): add owned data models and guarded legacy backfill
7be17dc fix(middleware): validate incoming request identifiers
```

## B10. Deviations and assumptions

- Tasks3/4 are committed together so onboarding is never committed beside unscoped business endpoints. The final integration commit groups catalog/recovery/provider/admin/retention changes and their tests; this is larger than the requested small-commit preference. Every committed tree is self-contained and locally green; intentional TDD reds remain uncommitted until fixed.
- Warehouse tenant ownership is added because global warehouse codes and foreign IDs otherwise undermine tenant inventory isolation.
- One portable migration is used. Downgrade refuses more cases than the minimum requested, including unreconstructible catalog edits; this favors explicit refusal over data loss.
- Prefix/hash scheme is explicitly the first12/rest split described in A2. Constant-time digest comparison is not a claim of constant total request latency.
- Existing request fingerprints remain compatible by omitting absent optional fields and canonicalizing effective currency. Replays precede current catalog validation.
- SQL parameter hiding and Numeric total bounds are added because regression tests exposed PII logging and cross-database overflow failures.
- Transient injection moves from reservation to mock payment authorization. Tests preserve rollback of the failing step, previously committed reservation, eventual exactly-once effects and final compensation.
- New operational regression tests added after already-working paths are labeled positive controls rather than invented TDD failures. No TDD red is claimed for documentation-only edits.
- Boundary tests freeze a database-derived clock to test equality deterministically; production still reads database time. E12 obtains its token via API after a host/container-secret harness failure.
- All destructive pytest schema setup is confined to `_test` databases. PostgreSQL legacy migration uses a unique disposable schema, never the development database. Application writers were stopped before the live tenancy migration and restored afterward.
- No remote push, PR, hosted CI, deployment, new native enum labels or out-of-scope integration is performed.

Every pre-existing test/fixture whose AST changed is listed below; new tests are described in B3 and the diff. Import-only changes for explicit owned model construction are included in the file-level note when no function body changes. Existing assertions remain equally strict unless the requested behavior explicitly replaces the expectation.

| Existing test/fixture | Reason |
|---|---|
| app/tests/conftest.py::reset_database | Explicit default tenant/catalog fixtures, scoped users/orders/stock; SQL parameter hiding in test engines. |
| app/tests/conftest.py::auth_header | Explicit default tenant/catalog fixtures, scoped users/orders/stock; SQL parameter hiding in test engines. |
| app/tests/conftest.py::workflow | Explicit default tenant/catalog fixtures, scoped users/orders/stock; SQL parameter hiding in test engines. |
| app/tests/test_auth_and_orders.py::test_signup_login_and_me | Explicit tenant/principal ownership or required tenant_slug; existing assertions and test intent retained. |
| app/tests/test_auth_and_orders.py::test_create_order_is_idempotent | Explicit tenant/principal ownership or required tenant_slug; existing assertions and test intent retained. |
| app/tests/test_cancel_hardening.py::order_stock | Explicit ownership plus provider authorize keyword-compatible failure stub. |
| app/tests/test_cancel_hardening.py::test_payment_failure_clears_inventory_reservation | Explicit ownership plus provider authorize keyword-compatible failure stub. |
| app/tests/test_exception_hardening.py::test_unhandled_exception_logs_request_id_and_safe_body | Patch the new tenant-scoped list repository method; same safe500/log assertions. |
| app/tests/test_failure_scenarios.py::test_business_failure_compensates_without_retry | Move the failure-injection settings patch from worker to mock provider; preserve compensation/no-retry assertions. |
| app/tests/test_failure_scenarios.py::test_transient_injection_then_retry_has_once_only_effects | Inject at mock authorization; verify rollback of payment/marker, retained earlier reservation and one successful payment on retry. |
| app/tests/test_failure_scenarios.py::test_exhaustion_persists_full_event_compensates_and_replay_is_once | Require actor, fresh event and second-replay409; extend to successful fulfillment with conserved stock. |
| app/tests/test_failure_scenarios.py::test_signup_duplicate_insert_race_is_conflict | Add the required default tenant_slug; keep duplicate-insert race409 assertion. |
| app/tests/test_hardening.py::test_signup_email_normalizes_before_duplicate_check | Explicit tenant/principal ownership or required tenant_slug; existing assertions and test intent retained. |
| app/tests/test_hardening.py::test_signup_ignores_privileged_role | Explicit tenant/principal ownership or required tenant_slug; existing assertions and test intent retained. |
| app/tests/test_metrics_hardening.py::test_metrics_access | Metrics/workers now require platform identity or token in every environment. |
| app/tests/test_metrics_hardening.py::test_metrics_admin_jwt | Metrics/workers now require platform identity or token in every environment. |
| app/tests/test_migrations_hardening.py::test_legacy_migration_backfill_and_safe_downgrade | Post-migration writes need explicit tenant/principal ownership. |
| app/tests/test_order_state.py::test_status_assignments_only_in_state_machine | AST guard allows exactly transition/reopen_failed_order in the state module. |
| app/tests/test_postgres_reliability.py::test_postgres_50_reservations_against_stock_10 | Explicit tenant/principal ownership or required tenant_slug; existing assertions and test intent retained. |
| app/tests/test_reliability_edges.py::test_replay_reschedules_original_outbox_and_missing_job_rolls_back | Replay now requires an existing order and actor; original-identity checks retained. |
| app/tests/test_settings_hardening.py::test_production_accepts_strong_secret | Explicitly allow mock providers in the production-secret positive control. |
| app/tests/test_worker_flow.py::test_inventory_reservation_deducts_available_and_records_warehouse | Explicit tenant/principal ownership or required tenant_slug; existing assertions and test intent retained. |

## B11. Newly discovered issues not fixed

- Warehouse/inventory duplicate creation still uses inherited raw IntegrityError handling rather than a tailored409; tenant scoping prevents foreign existence disclosure, but owned conflicts can surface500.
- The low-stock endpoint retains an inherited fixed100-row cap; there is no cursor/exhaustive scan API.
- Provider refund result references are not separately journaled; payment provider_reference identifies authorization. Real provider reconciliation needs richer operation records in3B.
- Mock adapters cannot establish real provider idempotency, settlement or carrier guarantees. Transaction-held calls require the A6 follow-up before real networking.
- Public registration abuse protection, global email uniqueness and application-only isolation remain the explicit A2/A3 limitations.
- All inherited A11/Phase2 deferred limitations remain; no production-readiness claim follows from passing local tests.

## B12. NOT VERIFIED

- Hosted GitHub Actions, branch protection, installed pre-commit hooks, remote push/PR/merge.
- Production deployment/HA/backups, RabbitMQ filesystem/container-loss recovery, long-duration soak or network partitions.
- Real payment/carrier HTTP behavior, settlement, provider-side deduplication, credentials, inbound/outbound webhooks and other excluded phases.
- Exact instruction-level mid-transaction SIGKILL or relay kill between broker confirm and DB commit.
- Historical-data reconciliation, arbitrary production-data downgrade eligibility, exhaustive secrets/history/LFS/ZIP audit.
- Automatic shipment-event-to-order SHIPPED/DELIVERED integration, real worker monitoring and delayed-shipment implementation.
- Literal copy/paste execution of every README curl example and a production abuse-protection policy.

SQLite, dedicated real PostgreSQL, legacy upgrade/downgrade/refusal, local Docker E1–E14 and restored injection=false are verified by the recorded outputs and are not in that list.

## B13. Reviewer commands (PowerShell)

```powershell
git switch phase-3a-tenancy
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .
.venv\Scripts\mypy app
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
$env:COVERAGE_FILE='.coverage.sqlite'
.venv\Scripts\python -m pytest -q --cov=app --cov-report=term-missing --cov-report=json:.coverage-sqlite.json
docker compose up -d postgres rabbitmq redis
docker compose exec -T postgres psql -U orders -d postgres -tAc "SELECT datname FROM pg_database WHERE datname='orders_test'"
# Only if absent: docker compose exec -T postgres createdb -U orders orders_test
$env:TEST_DATABASE_URL='postgresql+psycopg://orders:orders@localhost:5432/orders_test'
$env:COVERAGE_FILE='.coverage.postgres'
.venv\Scripts\python -m pytest -q --cov=app --cov-report=term-missing --cov-report=json:.coverage-postgres.json
.venv\Scripts\python -m pytest -q -s app/tests/test_tenancy_migration.py::test_postgres_tenancy_legacy_roundtrip
Remove-Item Env:TEST_DATABASE_URL
docker compose up --build -d
.venv\Scripts\python scripts/e2e_reliability.py
.venv\Scripts\python scripts/e2e_tenancy.py
docker compose exec -T api python -c "from app.core.config import get_settings; print('failure_injection_enabled=',get_settings().failure_injection_enabled)"
docker compose ps -a
git diff --check main...HEAD
git diff --stat main...HEAD
git log --oneline main..HEAD
```

Use `API_PORT=18000` if port8000 conflicts; both scripts honor it. Keep schema-changing tests away from the application DB. E2E runners must run sequentially because they intentionally restart shared services. Timing, UUIDs and container ages vary. Coverage data/output files are ignored local artifacts; the raw evidence copied into this report is committed.
