# Phase 2 — Reliability Core

Branch: `phase-2-reliability`
Verification date: 20 November 2026
Implementation snapshot: `e7d9e43` (10 implementation commits; report commit excluded from the snapshot).

# PART A — Architecture & Operations Documentation

## A1. Reliability architecture overview

The API commits an order, its items, audit records and the initial event intent atomically. It returns HTTP 202 without contacting RabbitMQ. The relay claims due intents and publishes confirmed persistent messages. Workers commit their effects, processed-event marker, transition audit and next event intents in one database transaction.

```mermaid
flowchart LR
    Client --> API
    API --> TX["PostgreSQL transaction: Order + Items + Audit + Outbox"]
    TX --> Relay
    Relay -->|publisher confirms| RabbitMQ
    RabbitMQ --> Worker
    Worker --> WTX["PostgreSQL transaction: locks + processed marker + effects + next Outbox"]
    WTX --> Relay
    API -->|202 accepted| Client
```

The guarantee is **at-least-once delivery with idempotent database effects**, not an exactly-once transport. A relay crash after confirmed publication but before marking the row published can produce another delivery. The consumer marker and order-state guard make that safe for the database-backed simulated providers.

## A2. Order lifecycle state machine

`app/domain/order_state.py::transition` is the only writer of an existing order's status. It validates the edge and adds an `order_transition` audit containing from/to/reason and optional actor. Order creation initializes CREATED; repeated cancellation returns the existing CANCELLED order without another transition or event.

```mermaid
stateDiagram-v2
    [*] --> CREATED
    CREATED --> INVENTORY_RESERVED
    CREATED --> FAILED
    CREATED --> CANCELLED
    INVENTORY_RESERVED --> PAYMENT_PENDING
    INVENTORY_RESERVED --> FAILED
    INVENTORY_RESERVED --> CANCELLED
    PAYMENT_PENDING --> PAID
    PAYMENT_PENDING --> FAILED
    PAYMENT_PENDING --> CANCELLED
    PAID --> FULFILLMENT_REQUESTED
    PAID --> FAILED
    PAID --> CANCELLED
    FULFILLMENT_REQUESTED --> SHIPPED
    FULFILLMENT_REQUESTED --> CANCELLED
    SHIPPED --> DELIVERED
    FAILED --> [*]
    CANCELLED --> [*]
    DELIVERED --> [*]
```

| From | Allowed destinations |
|---|---|
| CREATED | INVENTORY_RESERVED, FAILED, CANCELLED |
| INVENTORY_RESERVED | PAYMENT_PENDING, FAILED, CANCELLED |
| PAYMENT_PENDING | PAID, FAILED, CANCELLED |
| PAID | FULFILLMENT_REQUESTED, FAILED, CANCELLED |
| FULFILLMENT_REQUESTED | SHIPPED, CANCELLED |
| SHIPPED | DELIVERED |
| DELIVERED, CANCELLED, FAILED | None |
| VALIDATED | None; unused legacy enum member, outside the active workflow |

No new OrderStatus enum value is introduced. The current automated fulfillment flow ends at FULFILLMENT_REQUESTED. The existing shipment-event API still updates shipment records; it does not automatically advance order SHIPPED/DELIVERED states. Future order commands must use the transition function.

Invalid edges raise `InvalidTransitionError`, a non-retryable BusinessError with HTTP 409 semantics. A failed saga remains terminal even when its original event is replayed.

## A3. Transactional outbox

`outbox_events` stores UUID `id`, unique UUID `event_id`, UUID `aggregate_id`, `event_type`, `queue`, the full serialized DomainEvent JSON in `payload`, `correlation_id`, `status`, `attempts`, `available_at`, `created_at`, `published_at` and `last_error`. An index covers `(status, available_at)`. Status values are pending, published and failed.

`OutboxRepository.add(event, queue)` adds an intent to its caller's transaction and does not publish or commit. Idempotent HTTP order replays add no second initial intent.

`relay_once(db, batch_size)`:

1. Reads the database clock and selects due pending rows ordered by created_at and ID.
2. On PostgreSQL, claims rows with `FOR UPDATE SKIP LOCKED`. SQLite omits this clause and is suitable only for single-relay development.
3. Sends the full event through EventPublisher with Celery task ID equal to event_id, delivery mode 2 and send-time retry disabled.
4. Marks a row published only after send success, then commits. `broker_transport_options={"confirm_publish": True}` requests AMQP publisher confirms, so success means broker acceptance rather than just a local socket write.
5. On failure, increments attempts, retains the error and schedules a delay of `min(2**attempts, 3600)` seconds against the database clock. At OUTBOX_MAX_ATTEMPTS it retains the row as failed and logs `outbox_delivery_exhausted`.
6. Rolls back the relay batch if its own database transaction fails. Messages already accepted before that rollback can be delivered again.

The loop polls at OUTBOX_POLL_INTERVAL_SECONDS, handles SIGTERM/SIGINT with a stop event, and periodically deletes only published rows older than OUTBOX_RETENTION_DAYS. Pending and failed rows are never retention-deleted. Relay functions own their session transaction; callers should supply a fresh session.

A request ID is captured from structlog context when an order intent is created. Workers copy the incoming correlation_id onto every follow-up event. Cancellation starts a separate request correlation chain.

Failure modes include broker outage (retained pending intent), exhausted relay delivery (retained failed intent requiring operator action), crash between send/commit (duplicate delivery), and database outage (no atomic acceptance until the database returns). Broker-storage deletion is a separate operational risk; publisher confirms do not replace persistent broker storage.

## A4. Idempotent consumers and transactional handler pattern

`processed_events` has composite primary key `(consumer, event_id)` and a processed_at timestamp. The workflow consumer name is `order_workflow`.

The handler locks the order first, inserts its marker inside a savepoint, checks expected state, performs one step, adds follow-up intents and commits once. A duplicate-key marker rolls back only the savepoint and logs `duplicate_event_skipped`; the Prometheus counter `duplicate_events_skipped_total` increments.

```mermaid
sequenceDiagram
    participant MQ as RabbitMQ
    participant W as Worker
    participant DB as PostgreSQL
    MQ->>W: DomainEvent
    W->>DB: SELECT order FOR UPDATE
    W->>DB: SAVEPOINT; insert processed marker
    alt Marker already exists
        DB-->>W: Unique violation
        W->>DB: Roll back savepoint; commit no effects
        W-->>MQ: Acknowledge duplicate
    else New marker
        W->>W: Validate expected order state
        alt Expected state
            W->>DB: Lock inventory; effects; transition audit; next outbox
        else Stale or cancelled
            W->>W: Log skipped state
        end
        W->>DB: Commit effects and marker together
        W-->>MQ: Acknowledge
    end
    Note over W,DB: Technical error rolls back the whole transaction, marker included
```

Forward guards are order.created→CREATED, inventory.reserved→INVENTORY_RESERVED and payment.authorized→PAID. CANCELLED orders log `event_skipped_order_cancelled`; other mismatches log `event_skipped_unexpected_state`. Stale messages are marked processed and acknowledged.

Informational events, including shipment.created, inventory.failed, payment.failed, shipment.failed, order.cancelled and order.completed, are acknowledged without effects. Unknown valid event types currently follow that no-op path. Missing aggregates are logged and acknowledged without a marker.

SQLite's legacy sqlite3 transaction mode does not begin a transaction for SELECT. The handler explicitly begins the outer SQLite transaction before the savepoint when necessary, preventing the marker from surviving a subsequent rollback.

Late acknowledgements and `task_reject_on_worker_lost=True` preserve redelivery behavior after worker loss. Those transport settings rely on marker/state guards for database-effect safety.

## A5. Concurrency control and all-or-nothing inventory

The lock ordering rule is **ORDER row first, then all candidate INVENTORY rows sorted by SKU and warehouse_id**. Workers and API cancellation use the same order. Inventory rows are refreshed after lock acquisition to avoid stale quantities after waiting.

Each reservation constructs a complete allocation plan using remaining capacity per warehouse row. Only after every item fits does it deduct available stock, increment reserved stock and assign item warehouses. A missing item leaves every quantity and assignment untouched. Duplicate SKU lines share the plan's remaining-capacity accounting.

This deterministic inventory lock order prevents competing orders from locking the same SKU/warehouse set in opposite orders. Serializing operations on the parent order also prevents cancellation from racing past a reservation or payment update.

Release is a no-op unless inventory_reserved is true; a successful release clears it. Cancellation releases inventory, refunds authorized payments, records shipment cancellation events/audits, transitions the order, and writes one informational order.cancelled outbox intent in the same transaction. Repeated cancellation adds nothing.

The original database CHECK constraints `ck_inventory_available_non_negative` and `ck_inventory_reserved_non_negative` remain active. No redundant constraint migration is added; PostgreSQL tests verify both inspection and rejected negative writes. These constraints are a safety net, not a replacement for locking.

SQLite does not implement equivalent row-lock concurrency guarantees. Real concurrency verification uses PostgreSQL and separate sessions/threads.

## A6. Error taxonomy and retry policy

| Category | Examples | Retry? | Compensation | Final order state | Record |
|---|---|---|---|---|---|
| BusinessError | InsufficientInventoryError, InvalidTransitionError | No | Business handlers apply required compensation | FAILED for stock/payment/shipment business failures; invalid API transition leaves state unchanged | Transition and compensation audits; normal informational outbox events |
| Payment decline | Injected deterministic decline | No | Release reserved inventory | FAILED / PAYMENT_DECLINED | Failed Payment, audit, payment.failed |
| Shipment business failure | Injected failure after authorization | No | Refund authorized payment and release inventory | FAILED / SHIPMENT_FAILED | Compensation audits and shipment.failed |
| TransientError / OperationalError / connectivity | Technical mid-step failure or database operation error | Yes, exponential backoff with jitter, bounded by TASK_RETRY_BACKOFF_MAX and TASK_MAX_RETRIES | Whole transaction rolls back before retry | Unchanged until success or final exhaustion | Retry logs; marker absent until commit |
| Unknown exception / malformed message | Unexpected code failure or invalid DomainEvent | Retryable by default | Whole transaction rolls back | As above | `unexpected_error`; then failed_jobs on exhaustion |
| Exhausted / poison | Final failed attempt | No automatic attempts after limit | Lock order, release/refund if the active state can fail | FAILED / RETRIES_EXHAUSTED for eligible active orders | Full original JSON, event_id, aggregate_id, error, retry_count, status in failed_jobs |
| Failure-handler storage/compensation error | Failure while processing final failure | Exception-safe fallback | Best effort; cannot guarantee compensation while DB is unavailable | May require reconciliation | Independent persistence fallback; if that also fails, structured log with original event |

Business failure events use normal inventory/payments/shipments queues. They are not misrouted to the legacy dead_letter queue.

`replay_failed_job(db, job_id)` locks the failed-job row and schedules its original event identity through the outbox once, marking the job REPLAYED. An existing original intent is reset to pending rather than duplicated. Repeated replay calls do not reset it again. Consumer markers and terminal guards remain effective: replay does **not** revive a FAILED saga. A new business attempt requires a new order or a later explicit reconciliation workflow.

## A7. Failure scenario matrix

| Scenario | Trigger and behavior | Compensation | Final state | Test names | Real experiment |
|---|---|---|---|---|---|
| S1 | Valid stock and simulated providers | None | FULFILLMENT_REQUESTED | test_duplicate_redelivery_has_one_effect_per_step | E1 |
| S2 | Any item cannot fit; no partial reservation | Nothing reserved | FAILED / INSUFFICIENT_INVENTORY | test_partial_inventory_reservation_is_never_left_behind; test_postgres_50_reservations_against_stock_10 | E2, E8 |
| S3 | FAIL-PAYMENT-* with injection enabled | Inventory released | FAILED / PAYMENT_DECLINED | test_business_failure_compensates_without_retry | E3 |
| S4 | FAIL-SHIPMENT-* after authorization | Refund + inventory release, both audited | FAILED / SHIPMENT_FAILED | test_business_failure_compensates_without_retry | E4 |
| S5 | FAIL-TRANSIENT-* after reservation effects, before commit; first N attempts fail | Whole transaction rollback | Eventually FULFILLMENT_REQUESTED | test_transient_injection_then_retry_has_once_only_effects; test_technical_error_rolls_back_marker_stock_and_next_event | Supplement S5 |
| S6 | FAIL-TRANSIENT-EXHAUST-* exceeds retry budget | Rollback plus final-failure compensation | FAILED / RETRIES_EXHAUSTED | test_exhaustion_persists_full_event_compensates_and_replay_is_once; test_failure_fallback_keeps_valid_event_identifiers | Supplement S6 |
| S7 | Same event ID delivered repeatedly | No additional effect | Unchanged after first commit | test_duplicate_redelivery_has_one_effect_per_step; test_postgres_concurrent_duplicate_delivery | E5 explicit broker redelivery |
| S8 | Worker killed before queued order consumption, restarted; original event re-delivered | Transaction/marker guard | FULFILLMENT_REQUESTED with one effect per step | test_technical_error_rolls_back_marker_stock_and_next_event; duplicate-delivery tests | E5; actual SIGKILL inside a DB transaction is not exercised |
| S9 | RabbitMQ stopped while five orders are accepted | Pending outbox retained | All complete after recovery | test_broker_down_accepts_order_and_relay_publishes_once | E6 |
| S10 | Cancel CREATED, INVENTORY_RESERVED, PAID or FULFILLMENT_REQUESTED; repeat | Appropriate release/refund and shipment cancellation | CANCELLED | test_cancel_before_reserve_publishes_nothing; test_cancel_after_reserve_restores_once_and_double_cancel_is_noop; test_cancel_refunds_authorized_payment_once; test_fulfillment_cancel_compensates_shipment_payment_inventory_atomically; test_failed_cancel_rolls_back_every_compensation; test_postgres_cancel_vs_reserve_conserves_stock | Supplement S10 |

The injection prefixes operate only when FAILURE_INJECTION_ENABLED is true. Transient injection currently targets the order.created reservation step after effects but before commit. Ordinary transient demo SKUs fail for FAILURE_TRANSIENT_ATTEMPTS attempts; EXHAUST demo SKUs exceed TASK_MAX_RETRIES. Demo seeding adds matching SKUs only in enabled local/development/test environments.

## A8. Data model additions and event catalog

```mermaid
erDiagram
    ORDERS ||--o{ OUTBOX_EVENTS : "logical aggregate reference"
    OUTBOX_EVENTS ||--o{ PROCESSED_EVENTS : "logical event identity"
    ORDERS ||--o{ FAILED_JOBS : "logical aggregate reference"
    ORDERS ||--o{ SHIPMENTS : contains
    SHIPMENTS ||--o{ SHIPMENT_EVENTS : records
    OUTBOX_EVENTS {
        uuid id PK
        uuid event_id UK
        uuid aggregate_id
        string event_type
        string queue
        json payload
        string correlation_id
        string status
        int attempts
        datetime available_at
        datetime published_at
    }
    PROCESSED_EVENTS {
        string consumer PK
        uuid event_id PK
        datetime processed_at
    }
    FAILED_JOBS {
        uuid id PK
        uuid event_id
        uuid aggregate_id
        json payload
        string status
        int retry_count
        string error_message
    }
```

Aggregate/event links above are logical associations, not new foreign keys; poison messages and external event identities must remain persistable.

Every DomainEvent contains event_id, event_type, aggregate_id, occurred_at, payload and nullable correlation_id.

| Event | Producer | Workflow consumer action | Queue | Payload fields |
|---|---|---|---|---|
| order.created | OrderService | Reserve stock | orders | order_id |
| inventory.reserved | Reservation step | Authorize payment | payments | order_id, reason |
| payment.authorized | Payment step | Create shipment | shipments | order_id, reason |
| inventory.failed | Reservation business failure | Informational no-op | inventory | order_id, reason |
| payment.failed | Decline step | Informational no-op | payments | order_id, reason |
| shipment.failed | Shipment business failure | Informational no-op | shipments | order_id, reason |
| shipment.created | Shipment step | Informational no-op | shipments | order_id, reason |
| order.cancelled | Cancellation API service | Informational no-op | orders | order_id |
| order.completed, shipment.delayed | Reserved constants; no current producer | No forward action | Not emitted | Not currently specified |

Failed jobs add nullable event_id/aggregate_id columns for legacy compatibility. New valid-event failures populate both. Legacy nested args/kwargs payloads require manual normalization before service replay.

## A9. Configuration reference

| Variable | Default | Meaning | Production note |
|---|---|---|---|
| OUTBOX_MAX_ATTEMPTS | 10 | Relay delivery attempts before retained failed state | Alert and inspect failed intents; never silently drop |
| OUTBOX_POLL_INTERVAL_SECONDS | 0.5 | Relay polling/shutdown wait interval | Positive value required |
| OUTBOX_RETENTION_DAYS | 7 | Published-intent retention | Pending/failed rows excluded; processed markers have separate lifetime |
| TASK_MAX_RETRIES | 3 | Celery technical retry budget | Applied at worker startup |
| TASK_RETRY_BACKOFF_MAX | 60 | Maximum technical retry backoff seconds | Backoff includes jitter |
| FAILURE_INJECTION_ENABLED | false | Enable deterministic local SKU failures | Validation forbids true outside local/development/test |
| FAILURE_TRANSIENT_ATTEMPTS | 2 | Initial transient failures per injected reservation event | Only relevant when injection is enabled |
| API_PORT | 8000 | Compose host API port; container remains 8000 | Development convenience; verification uses 18000 |
| TEST_DATABASE_URL | sqlite:///./test_order_processing.db | Test engine selection | PostgreSQL name must end in _test; never use development orders DB |
| E2E_DATABASE_URL | postgresql+psycopg://orders:orders@localhost:5432/orders | E2E script inspection/seeding DB | Local demo only; align with actual API DB |
| E2E_BASE_URL | http://127.0.0.1:$API_PORT | E2E API address | Optional harness override |
| COVERAGE_FILE | .coverage | Coverage data file | Separate files prevent concurrent backend runs from overwriting evidence |

The last three variables are script/test tooling variables, not application Settings. Broker confirms, persistent task messages, late ACKs and reject-on-worker-lost are Celery configuration, not new environment switches.

## A10. Operations runbook

Inspect retained intents and exhausted jobs:

```sql
SELECT status, COUNT(*) FROM outbox_events GROUP BY status;
SELECT id, event_id, aggregate_id, event_type, attempts, available_at, last_error
FROM outbox_events WHERE status IN ('pending', 'failed')
ORDER BY created_at;
SELECT id, event_id, aggregate_id, retry_count, status, error_message, payload
FROM failed_jobs ORDER BY created_at DESC;
```

During a broker outage, keep the database and API available. Confirm new requests have pending intents, restore RabbitMQ, verify broker health and watch the backlog drain. After correcting a persistent relay failure, an operator can deliberately reset an inspected row:

```sql
UPDATE outbox_events
SET status = 'pending', attempts = 0, available_at = CURRENT_TIMESTAMP, last_error = NULL
WHERE id = '<inspected-outbox-uuid>' AND status = 'failed';
```

Replay a valid exhausted event through the service, not an HTTP endpoint:

```python
from uuid import UUID
from app.db.session import SessionLocal
from app.services.failed_jobs import replay_failed_job

with SessionLocal() as db:
    replay_failed_job(db, UUID("<failed-job-uuid>"))
```

The service commits the scheduling transaction. Inspect the original payload first. Replay retains the same event ID; terminal orders remain terminal. Malformed or legacy payloads need explicit repair/reconciliation before replay.

Compose's one-shot migrate service runs `alembic upgrade head`. API, worker, scheduler and relay wait for its successful completion and healthy PostgreSQL/RabbitMQ. Stop old writers during incompatible migration/downgrade work. Use a disposable database for downgrade testing. Downgrade 0006 refuses REPLAYED rows; downgrade 0002 refuses cross-customer key collisions.

Queues/exchanges are durable and messages persistent. Existing RabbitMQ queues may need `docker compose down -v` if queue arguments ever change; this command destroys volumes/data and is not part of ordinary recovery or this verification. RabbitMQ currently has no named data volume, so container deletion/recreation can lose accepted broker data.

Local PowerShell:

```powershell
$env:API_PORT='18000'
$env:FAILURE_INJECTION_ENABLED='true'
docker compose up --build -d
python scripts/e2e_reliability.py
$env:FAILURE_INJECTION_ENABLED='false'
docker compose up -d --no-build
```

The E2E runner kills this project's worker and stops/restarts RabbitMQ and the relay. Use it only on the local demo stack. It seeds uniquely named rows and leaves them for review; it does not delete existing data. It assumes images are already built, restores stopped services, and exits nonzero if any experiment fails. The caller restores injection to false after experiments.

PostgreSQL tests require a separate database:

```powershell
docker compose exec -T postgres createdb -U orders orders_test
$env:TEST_DATABASE_URL='postgresql+psycopg://orders:orders@localhost:5432/orders_test'
pytest -q --cov=app --cov-report=term-missing
Remove-Item Env:TEST_DATABASE_URL
```

Run createdb only if orders_test does not already exist. Test fixtures drop/recreate tables in the selected test database; they refuse a PostgreSQL database name without the _test suffix. CI keeps the SQLite job and adds a PostgreSQL service job running the entire suite, including postgres-marked tests.

## A11. Known limitations and deferred work

- Providers/pricing remain simulations; database idempotency is not a claim about external payment/carrier side effects.
- Terminal replay is safe acknowledgement/republication, not automatic saga resurrection or business recovery.
- Processed markers have no retention policy; failed outbox rows require operator inspection/reset.
- Historical cancelled/failed stock ambiguity and legacy failed-job payloads need reconciliation.
- SQLite is not a substitute for PostgreSQL concurrency guarantees.
- Failure persistence and compensation cannot be guaranteed while PostgreSQL itself is unavailable; structured fallback logs remain essential.
- RabbitMQ broker-data-volume durability, deployment hardening, monitoring and alerting belong to later operational work.
- The worker-monitoring endpoint remains static and delayed-shipment detection remains a stub.
- Multi-tenancy, real providers/adapters, outbound webhooks, replay HTTP endpoints, rate limiting, frontend, OpenTelemetry and Kafka remain out of scope.

# PART B — Verification Report

## B1. Summary

Phase 0–1 is confirmed merged in main at 0861a35; migrations 0002/0003 are present.
The branch is phase-2-reliability, based on an up-to-date main with a clean initial tree.
Order transitions, transactional outbox, confirmed relay, marker deduplication and deterministic locks are implemented.
Business compensation, transient retries, exhausted-event persistence, safe replay and local failure injection are implemented.
Shipment cancellation at FULFILLMENT_REQUESTED is atomic with refund, stock release, audit and cancellation intent.
Ruff lint/format and mypy pass; SQLite has 176 passing tests and five deliberate PostgreSQL skips.
The full real PostgreSQL suite has 181 passing tests; production coverage is 96% rounded on both backends.
All 11 real Docker experiments pass, including E1–E8 plus transient, exhaustion/replay and cancellation supplements.
The stack remains running on host port 18000 with failure injection restored to false.
Hosted CI, an actual OS kill inside a DB transaction, historical reconciliation and production durability remain outside the verified claims.

## B2. Task and scenario status table

| ID | Task | Status (DONE/PARTIAL/SKIPPED) | Commit hash(es) | Notes |
|---|---|---|---|---|
| 2.1 | Order state machine | DONE | df31b28 | All 100 enum pairs plus AST writer check; no new OrderStatus values. |
| 2.2 | Transactional outbox and relay | DONE | 8d12e78, 0e24ee5 | Atomic intents, confirms, SKIP LOCKED, retries/retention, DB-clock correction. |
| 2.3 | Idempotent transactional consumers | DONE | 25cc18c, 2ec8ae2 | Marker savepoint, order lock, state guards, full rollback; malformed message retry boundary. |
| 2.4 | Inventory and cancel concurrency | DONE | 5023841, e3b3297, e7d9e43 | All-or-nothing planning, deterministic locks, shipment compensation; existing CHECKs verified. |
| 2.5 | Taxonomy/retries/failure replay | DONE | 4929304, 2ec8ae2, 0e24ee5 | Full event persistence and fallback IDs; replay keeps terminal states terminal. |
| 2.6 | Failure injection and S1–S10 | DONE | 4929304, e7d9e43 | Production guard, demo SKUs, signup race fix; 11 real-stack experiments. |
| 2.7 | PostgreSQL layer, CI, coverage | DONE | e3b3297, e7d9e43 | Separate *_test DB, five real PostgreSQL tests, full CI job; hosted execution not verified. |
| 2.8 | Compose reliability gates | DONE | 7de1714, e7d9e43 | Real build/start succeeded; migrate exits 0; default-port override added for Windows. |
| 2.9 | Docker E2E runner and proof | DONE | e7d9e43 | E1–E8 and S5/S6/S10 supplements pass. |
| S1 | Happy path | DONE | 25cc18c, e7d9e43 | Unit/integration flow and E1. |
| S2 | Insufficient stock | DONE | 5023841, e3b3297, e7d9e43 | Partial-allocation regression, E2/E8, 50-vs-10. |
| S3 | Payment decline | DONE | 4929304, e7d9e43 | No retry; release; E3. |
| S4 | Shipment failure | DONE | 4929304, e7d9e43 | Refund + release audits; E4. |
| S5 | Transient mid-step rollback/retry | DONE | 25cc18c, 4929304, e7d9e43 | Marker/effects rollback and real retry supplement. |
| S6 | Retry exhaustion and replay | DONE | 4929304, 0e24ee5, e7d9e43 | Full event, compensation, terminal-safe replay; real supplement. |
| S7 | Duplicate delivery | DONE | 25cc18c, e3b3297, e7d9e43 | Sequential and concurrent duplicates; actual broker re-delivery in E5. |
| S8 | Worker crash/redelivery | DONE | 25cc18c, e7d9e43 | Permitted before-consumption kill + restart and explicit redelivery; mid-transaction OS kill not claimed. |
| S9 | Broker unavailable at intake | DONE | 8d12e78, e7d9e43 | Five HTTP 202, pending backlog 5, eventual completion; E6. |
| S10 | Cancel through fulfillment request | DONE | 5023841, e7d9e43 | CREATED/INVENTORY_RESERVED/PAID/FULFILLMENT_REQUESTED; repeated cancel; atomic rollback and shipment audit/event. |

## B3. TDD evidence per task

The following excerpts are actual observed failures before the relevant implementation or correction. Feature-availability tests initially failed on missing modules/models; this is reported as such, not as proof that each later assertion was separately executed against old code.

### 2.1 State machine

Tests: test_all_order_transitions (100 ordered pairs over every current enum member), test_status_assignments_only_in_state_machine.

```text
E   ImportError: cannot import name 'InvalidTransitionError' from 'app.core.exceptions'
1 failed in 0.44s
```

After implementation: all 149 then-current tests passed in 40.79s. Current full suites also pass.

### 2.2 Outbox and relay

Tests: test_order_outbox_atomicity, test_broker_down_accepts_order_and_relay_publishes_once, test_relay_exhaustion_and_retention.

```text
E   ImportError: cannot import name 'OutboxEvent' from 'app.models.entities'
3 failed in 0.66s
```

After implementation: 152 passed in 60.14s. Atomicity forces an exception after outbox add and asserts no order or intent survives. Broker-down acceptance/replay tests assert a single intent and one eventual send.

A later full PostgreSQL run exposed a real due-selection failure:

```text
FAILED app/tests/test_outbox.py::test_relay_exhaustion_and_retention
E       AssertionError: assert ('pending' == 'failed'
1 failed, 177 passed in 96.88s (0:01:36)
```

The added test_relay_due_selection_uses_database_clock first failed with a deliberately skewed host clock:

```text
E   AssertionError: assert 0 == 1
1 failed, 3 deselected in 1.40s
```

The code now uses the database clock, rather than changing the failing test's expectation. All four outbox tests then passed against PostgreSQL (4 passed in 4.11s), and the final full PostgreSQL run passes.

### 2.3 Consumer transactions

Tests: test_duplicate_redelivery_has_one_effect_per_step, test_stale_events_are_recorded_without_effects, test_technical_error_rolls_back_marker_stock_and_next_event.

```text
E   ImportError: cannot import name 'ProcessedEvent' from 'app.models.entities'
3 failed in 2.11s
```

The first implementation also failed the actual SQLite rollback assertion:

```text
E   AssertionError: assert 1 == 0
FAILED app/tests/test_consumers.py::test_technical_error_rolls_back_marker_stock_and_next_event
1 failed, 2 passed in 3.32s
```

Explicit outer SQLite BEGIN fixed savepoint marker leakage. After: 155 passed in 29.63s. PostgreSQL duplicates also run concurrently with separate sessions.

### 2.4 Inventory and shipment cancellation

Tests: test_partial_inventory_reservation_is_never_left_behind, test_fulfillment_cancel_compensates_shipment_payment_inventory_atomically, test_failed_cancel_rolls_back_every_compensation.

```text
E   assert 9 == 10
E   AssertionError: assert <ShipmentStat...ED: 'created'> == <ShipmentStat...: 'cancelled'>
E   Failed: DID NOT RAISE <class 'RuntimeError'>
3 failed in 1.64s
```

After: 158 passed in 19.29s. Cancellation rollback asserts unchanged authorized payment, created shipment, reserved stock, order status and absence of refund audit after forced intent-write failure. Real PostgreSQL constraints and concurrency are additional positive verification.

### 2.5–2.6 Retry taxonomy, injection, failures and signup race

Tests: test_failure_injection_forbidden_in_production, test_business_failure_compensates_without_retry (payment/shipment), test_transient_injection_then_retry_has_once_only_effects, test_exhaustion_persists_full_event_compensates_and_replay_is_once, test_signup_duplicate_insert_race_is_conflict.

```text
E   Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>
E   AssertionError: assert (<OrderStatus.PAID: 'paid'> == <OrderStatus.FAILED: 'failed'>
E   AssertionError: assert <PaymentStatu... 'authorized'> == <PaymentStatu...D: 'refunded'>
E   Failed: DID NOT RAISE <class 'app.core.exceptions.TransientError'>
E   ModuleNotFoundError: No module named 'app.services.failed_jobs'
E   sqlite3.IntegrityError: UNIQUE constraint failed: users.email
6 failed in 2.69s
```

After: 164 passed in 28.73s. The transient unit test advances the Celery retry count explicitly; Docker supplements verify actual Celery retries and exhaustion.

Further boundary regressions:

```text
FAILED app/tests/test_reliability_edges.py::test_malformed_event_uses_retryable_taxonomy
E   pydantic_core._pydantic_core.ValidationError: 3 validation errors for DomainEvent
1 failed, 6 deselected in 0.83s

FAILED app/tests/test_reliability_edges.py::test_failure_fallback_keeps_valid_event_identifiers
E   AssertionError: assert (None == UUID('24401f11-6a89-42d2-bb95-3f2f7ed40740'))
1 failed, 7 deselected in 1.18s
```

Malformed events now enter the retryable taxonomy, and failure fallback preserves IDs for valid original events. Poison payloads remain persistable even without valid IDs.

### 2.7 Real PostgreSQL fixture and concurrency

The first prototype concurrency run accidentally used the old hardcoded SQLite fixture despite TEST_DATABASE_URL. Two tests happened to pass there; those are **not** PostgreSQL evidence. An explicit backend assertion then demonstrated the routing defect:

```text
E   AssertionError: TEST_DATABASE_URL must select real PostgreSQL
E   assert 'sqlite' == 'postgresql'
1 warning, 1 error in 0.75s
```

After fixture routing and marker registration, all four initial real PostgreSQL concurrency tests passed in 5.26s. The final suite includes a fifth test verifying existing negative-quantity CHECK enforcement. These are real-database verification tests, not claims that the inherited CHECKs were missing.

Tests: test_postgres_50_reservations_against_stock_10, test_postgres_concurrent_duplicate_delivery, test_postgres_cancel_vs_reserve_conserves_stock, test_postgres_two_relays_claim_disjoint_rows, test_postgres_inventory_check_constraints_are_enforced.

### 2.8 Compose

Tests: test_compose_gates_applications_on_migrations_and_healthy_dependencies, test_compose_api_port_is_overridable.

```text
E   KeyError: 'migrate'
1 failed in 0.53s

E   AssertionError: assert '${API_PORT:-8000}' in '8000:8000'
1 failed, 1 deselected in 0.62s
```

The first real build completed, but host port 8000 failed:

```text
Error response from daemon: ports are not available: exposing port TCP 0.0.0.0:8000 -> 127.0.0.1:0: listen tcp 0.0.0.0:8000: bind: An attempt was made to access a socket in a way forbidden by its access permissions.
```

API_PORT keeps 8000 as default and permits the verified 18000 host mapping. Compose config validates and the real migration gate exits 0.

### 2.9 Runner

Test: test_e2e_runner_reports_failures_without_hiding_them.

```text
E   ModuleNotFoundError: No module named 'scripts.e2e_reliability'
1 failed in 0.87s
```

The runner records PASS/FAIL per experiment and exits nonzero if any action fails. Both complete real-stack experiment runs passed 11/11; the final transcript follows in B6.

Positive controls: existing shipped/delivered cancellation guards, permitted metrics access, scheduler helper behavior and the delayed-shipment stub remain unchanged. Relay-loop shutdown/error-path and replay-rescheduling tests cover implemented operational paths; their already-working assertions are not represented as pre-fix failures.

## B4. Raw command outputs and production-only coverage

Commands use Python 3.12.6 in the local virtual environment. Full SQLite and PostgreSQL runs use separate COVERAGE_FILE values; the earlier exploratory concurrent coverage run is not the final evidence.

`ruff check .`
```text
All checks passed!
```

`ruff format --check .`
```text
81 files already formatted
```

`mypy app`
```text
Success: no issues found in 66 source files
```

SQLite:
```powershell
$env:COVERAGE_FILE='.coverage.sqlite'
python -m pytest -q --cov=app --cov-report=term-missing --cov-report=json:.coverage-sqlite.json
```
```text
........................................................................ [ 39%]
........................................................................ [ 79%]
..........sssss......................                                    [100%]

---------- coverage: platform win32, python 3.12.6-final-0 -----------
Name                                Stmts   Miss  Cover   Missing
-----------------------------------------------------------------
app\__init__.py                         0      0   100%
app\api\__init__.py                     0      0   100%
app\api\v1\__init__.py                  0      0   100%
app\api\v1\endpoints\__init__.py        0      0   100%
app\api\v1\endpoints\auth.py           17      0   100%
app\api\v1\endpoints\health.py         35      4    89%   25-27, 51
app\api\v1\endpoints\inventory.py      32     14    56%   21-25, 34-38, 48-49, 57-58
app\api\v1\endpoints\orders.py         29      2    93%   45, 56
app\api\v1\endpoints\shipments.py      19      6    68%   24-29
app\api\v1\router.py                    8      0   100%
app\auth\dependencies.py               29      1    97%   30
app\auth\security.py                   21      0   100%
app\core\config.py                     38      0   100%
app\core\exceptions.py                 24      0   100%
app\core\logging.py                    10      0   100%
app\db\base.py                         11      0   100%
app\db\session.py                      12      4    67%   14-18
app\domain\__init__.py                  0      0   100%
app\domain\order_state.py              14      0   100%
app\events\contracts.py                19      0   100%
app\events\publisher.py                 8      0   100%
app\main.py                            21      0   100%
app\middleware\request_context.py      23      0   100%
app\models\__init__.py                  2      0   100%
app\models\entities.py                122      0   100%
app\models\enums.py                    36      0   100%
app\observability\metrics.py            6      0   100%
app\repositories\base.py               19      0   100%
app\repositories\inventory.py           9      1    89%   11
app\repositories\orders.py             18      0   100%
app\repositories\outbox.py             10      0   100%
app\repositories\users.py               7      0   100%
app\schemas\auth.py                    20      0   100%
app\schemas\common.py                  15      0   100%
app\schemas\inventory.py               21      0   100%
app\schemas\orders.py                  45      0   100%
app\schemas\shipments.py                6      0   100%
app\services\auth.py                   29      1    97%   38
app\services\failed_jobs.py            45      0   100%
app\services\inventory.py              46      5    89%   38, 53-56
app\services\orders.py                 84      2    98%   74, 105
app\services\payments.py               23      0   100%
app\services\shipping.py               23      0   100%
app\workers\celery_app.py               8      0   100%
app\workers\outbox_relay.py            62      2    97%   30, 81
app\workers\tasks.py                  136      0   100%
-----------------------------------------------------------------
TOTAL                                1162     42    96%
Coverage JSON written to file .coverage-sqlite.json

176 passed, 5 skipped in 50.54s
```

Real PostgreSQL:
```powershell
$env:COVERAGE_FILE='.coverage.postgres'
$env:TEST_DATABASE_URL='postgresql+psycopg://orders:orders@localhost:5432/orders_test'
python -m pytest -q --cov=app --cov-report=term-missing --cov-report=json:.coverage-postgres.json
```
```text
........................................................................ [ 39%]
........................................................................ [ 79%]
.....................................                                    [100%]

---------- coverage: platform win32, python 3.12.6-final-0 -----------
Name                                Stmts   Miss  Cover   Missing
-----------------------------------------------------------------
app\__init__.py                         0      0   100%
app\api\__init__.py                     0      0   100%
app\api\v1\__init__.py                  0      0   100%
app\api\v1\endpoints\__init__.py        0      0   100%
app\api\v1\endpoints\auth.py           17      0   100%
app\api\v1\endpoints\health.py         35      4    89%   25-27, 51
app\api\v1\endpoints\inventory.py      32     14    56%   21-25, 34-38, 48-49, 57-58
app\api\v1\endpoints\orders.py         29      2    93%   45, 56
app\api\v1\endpoints\shipments.py      19      6    68%   24-29
app\api\v1\router.py                    8      0   100%
app\auth\dependencies.py               29      1    97%   30
app\auth\security.py                   21      0   100%
app\core\config.py                     38      0   100%
app\core\exceptions.py                 24      0   100%
app\core\logging.py                    10      0   100%
app\db\base.py                         11      0   100%
app\db\session.py                      12      4    67%   14-18
app\domain\__init__.py                  0      0   100%
app\domain\order_state.py              14      0   100%
app\events\contracts.py                19      0   100%
app\events\publisher.py                 8      0   100%
app\main.py                            21      0   100%
app\middleware\request_context.py      23      0   100%
app\models\__init__.py                  2      0   100%
app\models\entities.py                122      0   100%
app\models\enums.py                    36      0   100%
app\observability\metrics.py            6      0   100%
app\repositories\base.py               19      0   100%
app\repositories\inventory.py           9      1    89%   11
app\repositories\orders.py             18      0   100%
app\repositories\outbox.py             10      0   100%
app\repositories\users.py               7      0   100%
app\schemas\auth.py                    20      0   100%
app\schemas\common.py                  15      0   100%
app\schemas\inventory.py               21      0   100%
app\schemas\orders.py                  45      0   100%
app\schemas\shipments.py                6      0   100%
app\services\auth.py                   29      1    97%   38
app\services\failed_jobs.py            45      0   100%
app\services\inventory.py              46      5    89%   38, 53-56
app\services\orders.py                 84      2    98%   74, 105
app\services\payments.py               23      0   100%
app\services\shipping.py               23      0   100%
app\workers\celery_app.py               8      0   100%
app\workers\outbox_relay.py            62      1    98%   81
app\workers\tasks.py                  136      3    98%   85-87
-----------------------------------------------------------------
TOTAL                                1162     44    96%
Coverage JSON written to file .coverage-postgres.json

181 passed in 100.92s (0:01:40)
```

Coverage omits app/tests. Exact overall values and requested module percentages extracted from the generated coverage JSON:
```text
sqlite 96.3855421686747
app\domain\order_state.py 100.0
app\repositories\outbox.py 100.0
app\services\auth.py 96.55
app\services\failed_jobs.py 100.0
app\services\inventory.py 89.13
app\services\orders.py 97.62
app\services\payments.py 100.0
app\services\shipping.py 100.0
app\workers\celery_app.py 100.0
app\workers\outbox_relay.py 96.77
app\workers\tasks.py 100.0
postgres 96.21342512908778
app\domain\order_state.py 100.0
app\repositories\outbox.py 100.0
app\services\auth.py 96.55
app\services\failed_jobs.py 100.0
app\services\inventory.py 89.13
app\services\orders.py 97.62
app\services\payments.py 100.0
app\services\shipping.py 100.0
app\workers\celery_app.py 100.0
app\workers\outbox_relay.py 98.39
app\workers\tasks.py 97.79
```

All requested >=85% targets are met. Lower API endpoint percentages remain visible rather than being omitted.

## B5. Migrations and real PostgreSQL round-trip

- 0004_transactional_outbox.py / revision 0004_outbox: creates outbox intents and status/availability index, using a batch operation for the index. No speculative backfill of old events. Downgrade drops this new table.
- 0005_processed_events.py / revision 0005_consumers: creates the composite consumer/event marker table. New events populate markers; historical effects are protected by current-state guards. Downgrade drops markers, so writers must be stopped.
- 0006_failed_job_replay.py / revision 0006_replay: adds nullable failed-job event_id/aggregate_id for legacy compatibility and converts native PostgreSQL failed_jobs.status to VARCHAR(20), supporting REPLAYED portably. The original enum type is retained for downgrade. No OrderStatus enum alteration is needed.
- Existing nested args/kwargs payloads and historical ambiguous stock are not guessed/backfilled. Downgrade 0006 refuses REPLAYED rows rather than silently changing their meaning; otherwise it restores the original native enum on PostgreSQL and removes added IDs.
- The pre-existing 0002 downgrade still refuses cross-customer duplicate keys; the isolated round-trip database has none.
- SQLite legacy-data up/down verification runs in the full suite. PostgreSQL round-trip uses separate orders_migration_test, not development orders or destructive test orders_test.

Executed sequence:
```powershell
$env:DATABASE_URL='postgresql+psycopg://orders:orders@localhost:5432/orders_migration_test'
alembic upgrade head
alembic downgrade -1
alembic upgrade head
alembic downgrade 0001_initial
alembic upgrade head
```

Raw output; all five commands exit 0:
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
INFO  [alembic.runtime.migration] Running downgrade 0006_replay -> 0005_consumers, Store full failed-event identifiers and portable replay status.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0005_consumers -> 0006_replay, Store full failed-event identifiers and portable replay status.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0006_replay -> 0005_consumers, Store full failed-event identifiers and portable replay status.
INFO  [alembic.runtime.migration] Running downgrade 0005_consumers -> 0004_outbox, Consumer deduplication markers are committed with handler effects.
INFO  [alembic.runtime.migration] Running downgrade 0004_outbox -> 0003_reservation, Transactional event intents; new table has no historical backfill.
INFO  [alembic.runtime.migration] Running downgrade 0003_reservation -> 0002_idempotency, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Running downgrade 0002_idempotency -> 0001_initial, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_idempotency, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Running upgrade 0003_reservation -> 0004_outbox, Transactional event intents; new table has no historical backfill.
INFO  [alembic.runtime.migration] Running upgrade 0004_outbox -> 0005_consumers, Consumer deduplication markers are committed with handler effects.
INFO  [alembic.runtime.migration] Running upgrade 0005_consumers -> 0006_replay, Store full failed-event identifiers and portable replay status.
```

## B6. Real Docker end-to-end proof

Executed with API_PORT=18000 and FAILURE_INJECTION_ENABLED=true. Images were built successfully; host port 8000 was the only initial startup failure and is documented above. Worker and relay were restarted after the final code corrections before the final experiment run.

`python scripts/e2e_reliability.py`, final exit 0:
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
PASS S6 exhausted retry and replay: retries exhausted=3; full failed event persisted; compensated; terminal-safe replay
PASS S10 cancellation at every requested state: CREATED/INVENTORY_RESERVED/PAID/FULFILLMENT_REQUESTED; repeated cancel; stock/refund/shipment correct
RESULT: 11/11 experiments passed
NAME                                                     IMAGE                                                  COMMAND                  SERVICE        CREATED          STATUS                    PORTS
event-driven-order-orchestration-system-api-1            event-driven-order-orchestration-system-api            "uvicorn app.main:ap…"   api            14 minutes ago   Up 14 minutes             0.0.0.0:18000->8000/tcp, [::]:18000->8000/tcp
event-driven-order-orchestration-system-outbox-relay-1   event-driven-order-orchestration-system-outbox-relay   "python -m app.worke…"   outbox-relay   15 minutes ago   Up 13 seconds             8000/tcp
event-driven-order-orchestration-system-postgres-1       postgres:16-alpine                                     "docker-entrypoint.s…"   postgres       2 months ago     Up 41 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
event-driven-order-orchestration-system-rabbitmq-1       rabbitmq:3.13-management-alpine                        "docker-entrypoint.s…"   rabbitmq       2 months ago     Up 2 minutes (healthy)    0.0.0.0:5672->5672/tcp, [::]:5672->5672/tcp, 0.0.0.0:15672->15672/tcp, [::]:15672->15672/tcp
event-driven-order-orchestration-system-redis-1          redis:7-alpine                                         "docker-entrypoint.s…"   redis          2 months ago     Up 41 minutes             0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
event-driven-order-orchestration-system-scheduler-1      event-driven-order-orchestration-system-scheduler      "celery -A app.worke…"   scheduler      15 minutes ago   Up 14 minutes             8000/tcp
event-driven-order-orchestration-system-worker-1         event-driven-order-orchestration-system-worker         "celery -A app.worke…"   worker         15 minutes ago   Up 3 minutes              8000/tcp
```

After experiments, injection is restored to false with the stack still running. Raw restoration check and `docker compose ps -a`:
```text
after restore: injection=False
NAME                                                     IMAGE                                                  COMMAND                  SERVICE        CREATED              STATUS                          PORTS
event-driven-order-orchestration-system-api-1            event-driven-order-orchestration-system-api            "uvicorn app.main:ap…"   api            About a minute ago   Up About a minute               0.0.0.0:18000->8000/tcp, [::]:18000->8000/tcp
event-driven-order-orchestration-system-migrate-1        event-driven-order-orchestration-system-migrate        "alembic upgrade head"   migrate        About a minute ago   Exited (0) About a minute ago
event-driven-order-orchestration-system-outbox-relay-1   event-driven-order-orchestration-system-outbox-relay   "python -m app.worke…"   outbox-relay   About a minute ago   Up About a minute               8000/tcp
event-driven-order-orchestration-system-postgres-1       postgres:16-alpine                                     "docker-entrypoint.s…"   postgres       2 months ago         Up 43 minutes (healthy)         0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
event-driven-order-orchestration-system-rabbitmq-1       rabbitmq:3.13-management-alpine                        "docker-entrypoint.s…"   rabbitmq       2 months ago         Up 4 minutes (healthy)          0.0.0.0:5672->5672/tcp, [::]:5672->5672/tcp, 0.0.0.0:15672->15672/tcp, [::]:15672->15672/tcp
event-driven-order-orchestration-system-redis-1          redis:7-alpine                                         "docker-entrypoint.s…"   redis          2 months ago         Up 43 minutes                   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
event-driven-order-orchestration-system-scheduler-1      event-driven-order-orchestration-system-scheduler      "celery -A app.worke…"   scheduler      About a minute ago   Up About a minute               8000/tcp
event-driven-order-orchestration-system-worker-1         event-driven-order-orchestration-system-worker         "celery -A app.worke…"   worker         About a minute ago   Up About a minute               8000/tcp
```

No `docker compose down -v` or data deletion is performed. Experiment rows remain uniquely named for review.

## B7. Concurrency evidence

The real PostgreSQL 50-thread reservation test uses a barrier and a separate transaction/session per handler. Its observed count output:
```text
50-vs-10: reserved=10 failed=40 available=0 reserved_quantity=10 conserved=10
4 passed in 5.26s
```

The final full PostgreSQL suite repeats the reservation, duplicate-delivery, cancel-versus-reserve and two-relay tests successfully, and adds real negative-quantity constraint enforcement.

The real API/worker/relay experiment submits 30 concurrent HTTP requests against its own stock of 10:
```text
PASS E8 concurrent orders: 30-vs-10: fulfilled=10 failed=20 available=0 reserved=10 conserved=10
```

Both experiments assert conserved total stock and exact successful/failure counts. Concurrent duplicate events leave one payment/shipment, and two relay transactions publish 12 distinct claimed event IDs without overlap in the non-crash test.

## B8. Phase 0–1 carry-over

“Later scoped phase” means the supplied plan does not assign a phase number; no delivery schedule is invented.

| Phase 0–1 item | Status | Mapping / remaining scope |
|---|---|---|
| DB commit and event publication gap | RESOLVED | New writes use the atomic outbox; historical lost intents are not reconstructed. |
| Consumers lack deduplication | RESOLVED | Processed markers, state guards and concurrent duplicate tests. |
| Inventory/cancel/worker races | RESOLVED | Order-first/sorted inventory locks; real PostgreSQL races. |
| Partial reservations can leak | RESOLVED | Complete allocation plan before mutation; rollback and stock-failure tests. |
| Historical cancelled/failed stock ambiguity | DEFERRED | Manual data reconciliation, Phase 3 operations work; no trustworthy old reservation ledger. |
| Exhausted failures lack full replay context / DLQ misuse | RESOLVED | Full event IDs/JSON, service replay, business events on normal queues. |
| Worker monitoring static / delayed detection stub | DEFERRED | Explicitly excluded; later scoped monitoring phase. |
| Original SQLite UUID NUMERIC reflection | DEFERRED | Existing migration remains compatible with explicit fixture binding; schema cleanup is a later scoped migration task. |
| Signup duplicate-email race | RESOLVED | Insert IntegrityError rollback maps to standard 409. |
| Simulated providers, no multi-tenancy/frontend, demo credentials | DEFERRED | Explicitly excluded; later scoped product/provider phases; local demo credentials remain public examples. |
| PostgreSQL migrations/runtime not verified | RESOLVED | Full real PostgreSQL suite and isolated up/down/up/down/up. |
| Docker build and Compose not verified | RESOLVED | Real image build, migration gate, running services; host override handles Windows. |
| RabbitMQ/Redis/workers/scheduler runtime not verified | RESOLVED | Real broker/worker/relay E2E and running scheduler; not a claim that placeholder delayed detection works. |
| Hosted CI, branch protection, pre-commit execution | DEFERRED | Workflow is committed, not pushed/executed here; hosted/repository controls need a later authorized workflow run. |
| Real simultaneous races/load not verified | RESOLVED | 50-vs-10 PostgreSQL, concurrent duplicate/cancel/relay tests and real 30-vs-10 API experiment. |
| Exhaustive secret audit across history/LFS/ZIP | DEFERRED | Separate security audit, outside Phase 2. |
| Removed-video playback/corruption | DEFERRED | Files were removed as requested in Phase 0; no media verification needed for reliability. |
| Historical reconciliation and production deployment | DEFERRED | Phase 3 operational reconciliation; Phase 7 deployment/hosting remains excluded. |

## B9. Files changed and commit history

Exact `git diff --stat main...HEAD` and `git log --oneline main..HEAD` snapshot at e7d9e43, **before the report commit**:
```text
 .dockerignore                                 |   2 +
 .env.example                                  |   8 +
 .github/workflows/ci.yml                      |  27 +++
 .gitignore                                    |   2 +
 README.md                                     |   7 +-
 alembic/versions/0004_transactional_outbox.py |  35 +++
 alembic/versions/0005_processed_events.py     |  23 ++
 alembic/versions/0006_failed_job_replay.py    |  49 ++++
 app/core/config.py                            |   9 +
 app/core/exceptions.py                        |  14 +-
 app/domain/__init__.py                        |   0
 app/domain/order_state.py                     |  38 ++++
 app/events/publisher.py                       |   9 +-
 app/models/entities.py                        |  33 ++-
 app/models/enums.py                           |   1 +
 app/observability/metrics.py                  |   3 +
 app/repositories/inventory.py                 |  11 +
 app/repositories/orders.py                    |   7 +-
 app/repositories/outbox.py                    |  21 ++
 app/services/auth.py                          |   9 +-
 app/services/failed_jobs.py                   |  66 ++++++
 app/services/inventory.py                     |  22 +-
 app/services/orders.py                        |  73 ++++--
 app/services/shipping.py                      |   5 +
 app/tests/conftest.py                         |  48 +++-
 app/tests/test_auth_and_orders.py             |   8 +-
 app/tests/test_cancel_hardening.py            |  13 +-
 app/tests/test_compose_reliability.py         |  19 ++
 app/tests/test_consumers.py                   |  61 +++++
 app/tests/test_e2e_runner.py                  |   7 +
 app/tests/test_failure_scenarios.py           | 108 +++++++++
 app/tests/test_hardening.py                   |  14 +-
 app/tests/test_inventory_atomic.py            |  57 +++++
 app/tests/test_order_state.py                 |  52 +++++
 app/tests/test_outbox.py                      | 106 +++++++++
 app/tests/test_postgres_reliability.py        | 140 ++++++++++++
 app/tests/test_reliability_edges.py           |  98 ++++++++
 app/workers/celery_app.py                     |   2 +
 app/workers/outbox_relay.py                   |  81 +++++++
 app/workers/tasks.py                          | 242 ++++++++++++--------
 docker-compose.yml                            |  59 +++--
 docs/project-review.md                        |  10 +-
 pyproject.toml                                |   4 +
 pytest.ini                                    |   3 +
 scripts/e2e_reliability.py                    | 316 ++++++++++++++++++++++++++
 scripts/seed_demo.py                          |  16 +-
 46 files changed, 1753 insertions(+), 185 deletions(-)
e7d9e43 test(e2e): prove reliability against the real Docker stack
0e24ee5 fix(reliability): use database delivery clocks and preserve failure identifiers
7de1714 feat(compose): gate services on migrations and run the outbox relay
2ec8ae2 fix(worker): retry malformed messages and verify recovery edges
e3b3297 test(postgres): verify locking concurrency and add full database CI job
4929304 feat(reliability): compensate business failures and persist exhausted retries
5023841 fix(inventory): reserve atomically and compensate locked cancellations
25cc18c feat(worker): deduplicate deliveries in one locked transaction
8d12e78 feat(outbox): persist event intents and relay confirmed messages
df31b28 feat(saga): validate and audit every order status transition
```

The final report is committed separately as requested. The snapshot excludes that self-referential documentation commit.

## B10. Deviations & assumptions

- git pull uses --ff-only to avoid an unintended merge. main already contained the merged Phase 0–1 work; no stop condition was triggered.
- Windows sandbox networking cannot run TestClient/host PostgreSQL access reliably. Required tests and Docker actions use approved execution outside that sandbox.
- TDD feature-availability reds are explicitly identified in B3; runtime regression failures, including SQLite savepoint leakage, clock selection and fallback IDs, are separately recorded. Already-working controls are not claimed as pre-fix failures.
- The old inventory CHECK constraints already exist in migration 0001 and the model. Rather than adding duplicates, a real PostgreSQL test verifies their presence and rejected negative writes.
- Failed-job REPLAYED support converts its native status enum column to VARCHAR rather than adding an enum label in an autocommit block. No new OrderStatus value is introduced. Real downgrade/upgrade proves the retained old enum can be restored when data permits.
- Replay preserves the original event ID and schedules once; it deliberately does not violate the terminal FAILED rule. Automatic business resumption/reconciliation belongs to Phase 3.
- Transient injection targets reservation after effects but before commit. EXHAUST SKUs use the retry budget plus one attempted failure; normal SKUs use FAILURE_TRANSIENT_ATTEMPTS.
- E5 uses the explicitly permitted kill-before-consumption variant and then deliberate broker redelivery. An OS kill at a precise instruction inside an open transaction is not claimed.
- Host port 8000 failed despite a successful image build. API_PORT keeps the default and enables verified port 18000. The initial error and corrected startup are recorded.
- Tests use a stricter PostgreSQL safety guard: database names must end in _test. orders_test and orders_migration_test were created separately; development data is never used by destructive pytest setup.
- Initial concurrent coverage exploration shared a default coverage filename. Final evidence uses separate .coverage.sqlite/.coverage.postgres files and JSON outputs; only those independent runs are reported in B4.
- Operational tests cover relay shutdown, poison fallback, replay rescheduling and inherited scheduler controls to meet requested production-module targets without changing the delayed-shipment stub.
- The real E2E script assumes images have already been built and uses compose up --no-build; the documented setup includes the actual build command. It adds S5/S6/S10 supplements beyond E1–E8.
- E2E leaves uniquely named demo rows and restores stopped services. Failure injection was explicitly reset to false afterward. Celery Beat's generated schedule files are ignored by Git and Docker.
- README/project-review wording is updated so it no longer claims the outbox/deduplication/locking work is future work. The Phase 0–1 report remains historical.
- No push or hosted CI invocation is performed. All implementation commits are focused Conventional Commits.

### Every modified pre-existing test / fixture and why

| Existing test or fixture | Minimal change and reason |
|---|---|
| test_auth_and_orders.py::test_create_order_is_idempotent | Replaces send_task spy/count with exactly one outbox_events row; retains status and same-ID checks. |
| test_hardening.py::test_customer_pagination_counts_all_matching_orders | Removes obsolete send_task stub; pagination assertions unchanged because intake no longer publishes directly. |
| test_hardening.py::test_idempotency_is_customer_scoped | Removes obsolete send_task stub; distinct IDs and two-order isolation assertions unchanged. |
| test_hardening.py::test_idempotency_payload_conflict_and_replay | Replaces publisher list length with exactly one durable outbox row, preserving one-order and 409 assertions. |
| test_hardening.py::test_idempotency_insert_race | Adds db fixture and counts one outbox row instead of one broker send; same lookup-miss race and same-ID checks remain. |
| test_cancel_hardening.py::order_stock fixture | Removes direct-send spy; its fourth placeholder is retained to minimize fixture-consumer churn. |
| test_cancel_hardening.py::test_cancel_before_reserve_publishes_nothing | Still asserts unchanged stock; now asserts one informational cancellation intent, as Phase 2 explicitly requires. No direct broker publish occurs. |
| test_cancel_hardening.py::test_cancel_after_reserve_restores_once_and_double_cancel_is_noop | Replaces empty broker-send list with exactly one cancellation outbox row across both cancels; stock/audit strictness retained. |
| test_cancel_hardening.py::test_cancel_terminal_order_conflicts | Replaces empty broker-send list with zero outbox rows; terminal conflict assertions retained. |
| test_cancel_hardening.py::test_payment_failure_clears_inventory_reservation | Initializes INVENTORY_RESERVED before directly invoking the payment helper, satisfying the new validated graph; existing release/failure checks retained. |
| test_cancel_hardening.py::test_cancel_refunds_authorized_payment_once and test_worker_skips_cancelled_order cases | Affected only by the shared fixture's removed spy; their assertions remain unchanged. |
| conftest.py::reset_database/db/client/auth_header | Engine/session factory now follows TEST_DATABASE_URL; safety suffix and marker skip added. Existing API fixture behavior remains; state-machine Mock-only tests skip needless schema recreation. New workflow fixture centralizes new Phase 2 test setup. |

No original assertions are deleted or relaxed to hide a production failure. Updated expectations reflect the explicitly requested move from direct publication to durable intents and the new informational cancellation event.

## B11. Newly discovered issues (not fixed)

- RabbitMQ has no named data volume in the inherited minimal Compose stack. Persistent messages/durable queues survive the tested stop/start, not deletion of its filesystem/container.
- No processed-marker retention policy or operator UI exists; retained markers grow over time.
- A terminal-safe replay does not restart a business saga. Reconciliation/new-order policy is future Phase 3 work.
- Existing pre-Phase-2 failed-job args/kwargs payloads and ambiguous historical reservations need manual normalization/reconciliation.
- The unused VALIDATED enum member has no active transitions. Legacy rows in that state need explicit reconciliation.
- Shipment-event recording does not automatically advance order SHIPPED/DELIVERED state; future order commands must use the validated graph.
- A database outage can defeat both final-failure persistence attempts. The original event is then logged, but there is no separate durable failure store.
- Raw request IDs are not length-normalized before insertion into the new 255-character correlation_id field; unusually long client IDs can be rejected by PostgreSQL.
- Broker confirms guarantee acceptance, not a backup/durable-storage/HA policy. Those operational guarantees remain deployment work.
- Static worker monitoring and delayed shipment detection remain intentionally unchanged.

## B12. NOT VERIFIED list

- Hosted GitHub Actions execution, branch-protection policy and installed pre-commit hook execution.
- An actual SIGKILL at a precise mid-transaction instruction; verified variant is kill-before-consumption plus real broker redelivery and transactional rollback tests.
- Relay process kill in the narrow broker-confirm/database-commit gap; its at-least-once failure mode is documented and duplicate delivery is tested.
- Production deployment, RabbitMQ filesystem-loss/HA/backup recovery, long-duration soak tests and network-partition behavior.
- Historical-data reconciliation, exhaustive secret/history/LFS/ZIP audit and removed-video playback.
- Real external payment/carrier behavior, multi-tenancy, outbound webhooks, frontend and other explicitly excluded phases.
- Automatic shipment-event-to-order SHIPPED/DELIVERED integration and implementation of the delayed-shipment stub.

SQLite, the full real PostgreSQL suite, migrations, Docker build/start, E1–E8 and all three supplements are VERIFIED and are not included in this list.

## B13. Reviewer commands (PowerShell)

Use the repo root and Python 3.12. Git switch assumes any unrelated reviewer changes are already handled.

```powershell
git switch phase-2-reliability
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt

ruff check .
ruff format --check .
mypy app

Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
$env:COVERAGE_FILE='.coverage.sqlite'
pytest -q --cov=app --cov-report=term-missing --cov-report=json:.coverage-sqlite.json
```

Start infrastructure and inspect database names before creating them:
```powershell
docker compose up -d postgres rabbitmq redis
docker compose exec -T postgres psql -U orders -d postgres -tAc "SELECT datname FROM pg_database WHERE datname IN ('orders_test','orders_migration_test')"
# Run each createdb only if that database is absent.
docker compose exec -T postgres createdb -U orders orders_test
docker compose exec -T postgres createdb -U orders orders_migration_test

$env:TEST_DATABASE_URL='postgresql+psycopg://orders:orders@localhost:5432/orders_test'
$env:COVERAGE_FILE='.coverage.postgres'
pytest -q --cov=app --cov-report=term-missing --cov-report=json:.coverage-postgres.json
pytest -q -s app/tests/test_postgres_reliability.py
Remove-Item Env:TEST_DATABASE_URL
```

Disposable PostgreSQL migration round-trip:
```powershell
$env:DATABASE_URL='postgresql+psycopg://orders:orders@localhost:5432/orders_migration_test'
alembic upgrade head
alembic downgrade -1
alembic upgrade head
alembic downgrade 0001_initial
alembic upgrade head
Remove-Item Env:DATABASE_URL
```

The round-trip DB must contain no REPLAYED jobs or conflicting old global idempotency keys. Do not downgrade a running application's database.

Build, run real experiments, then restore local injection:
```powershell
$env:API_PORT='18000'
$env:FAILURE_INJECTION_ENABLED='true'
docker compose config --quiet
docker compose up --build -d
python scripts/e2e_reliability.py
docker compose ps -a

$env:FAILURE_INJECTION_ENABLED='false'
docker compose up -d --no-build
docker compose exec -T api python -c "from app.core.config import get_settings; print(get_settings().failure_injection_enabled)"
docker compose ps -a

git diff --check main...HEAD
git diff --stat main...HEAD
git log --oneline main..HEAD
```

Expected final results: 176 SQLite passes plus five skips, 181 PostgreSQL passes, approximately 96% production coverage, 11/11 real experiments, successful migrations and injection false after restoration. Timing/UUID/container-age fields vary by run.
