# Phase 3B — Part 1 cumulative report

21 November 2026, Asia/Calcutta. Branch `phase-3b-integrations`.
**Task 3B.1 is complete, including the previously committed(d/e). Tasks3B.2–5
remain unimplemented. This is not a completed async-payments release.** This pass
stops at a clean task boundary under working rule5 rather than committing an
unfinished payment or abuse-control subsystem. Part2 remains out of scope.

# PART A — Implemented architecture and operations

## A1. Async payment saga

Not yet implemented. The synchronous mock flow remains as documented in Phase3A.
No async payment, COD, expiry, late-payment or refund sequence diagram is claimed.
Task3B.5 must replace that architecture before connecting real providers.

## A2. Provider architecture

Existing Phase3A interfaces and mocks remain; no v2/Razorpay adapter, encryption,
tenant payment configuration or provider resolution change is added. Existing mock
calls run inside transactions. There are no new external HTTP calls; the requested
lock-free external-call architecture/proof is not implemented. No live provider is called.

## A3. Refunds and reconciliation

Not yet implemented. Existing synchronous mock refunds remain; no durable journal,
unknown-outcome reconciliation or retry worker is added.

## A4. Shipping and COD

Part 2 — not yet implemented. Existing shipment behavior remains.

## A5. Outbound webhooks

Part 2 — not yet implemented. No SSRF or seller delivery subsystem is added.

## A6. Notifications

Part 2 — not yet implemented. No notification journal, templates or SMTP services.

## A7. Public tracking and SSE

Part 2 — not yet implemented. No public tracking or streaming surface is added.

## A8. Authentication and abuse controls

Rate limiting/registration controls (3B.2) are not yet implemented. Unknown-email
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
| New webhook/DoS/credential risks | No new public/provider surface introduced | Remaining tasks not implemented |

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

No table or event type is introduced. Revision0008_key_prefix widens one column.
No native PostgreSQL enum labels/types change. Adjustment audits contain no key
plaintext. Order timeline and status_changed events (3B.3) are not yet implemented.

## A11. Configuration

No new environment variables/dependencies are introduced by task1. Existing auth,
broker, database and mock-provider settings remain. Rate-limit, encryption and
async-payment settings requested in later tasks are not yet available.

## A12. API reference changes so far

| Method/path | Auth | Ownership/policy | Behavior |
|---|---|---|---|
| PATCH /api/v1/inventory/{id} | ADMIN/VENDOR JWT or inventory:write key | Tenant-first404; existing authenticated policy, no limiter yet | Lock/refresh before changing fields |
| POST /api/v1/inventory/{id}/adjustments | Same | Tenant-first404; customer/insufficient scope403 | `{delta:nonzero integer,reason:1..200 chars}` → InventoryRead; negative422; audit |
| POST /api/v1/api-keys | Tenant ADMIN JWT only | Existing key-management scope | New17-character prefix; full key once |
| POST /api/v1/auth/login | Public | Rate limiting not yet implemented | Same generic invalid403, hash work in both branches |
| POST /api/v1/inventory/warehouses | Staff/key inventory:write | Tenant-owned uniqueness | Duplicate409 warehouse_code_conflict |
| POST /api/v1/inventory | Staff/key inventory:write | Tenant-owned uniqueness | Duplicate409 inventory_conflict |

All other endpoints remain as in Phase3A. No PUBLIC_WEBHOOK/PUBLIC_TRACKING route
exists yet. The registry adds the adjustment route to TENANT_SCOPED; the existing
public-route policy requirement will be implemented with3B.2.

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
below. Connecting Razorpay, investigating async payments/refunds, or rotating encrypted
credentials is not supported yet. Reproduction commands are B14.

## A14. Limitations

Tasks3B.2–5 and3B.P1 acceptance experiments remain outstanding. Part2 is not implemented.
Existing provider calls/refunds remain mock/synchronous; application-only tenancy,
global email uniqueness, historical-data reconciliation and other Phase3A limitations
remain. No full Phase3B or production-readiness claim is made.

# PART B — Verification

## B1. Summary

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

## B2. Task status

| ID | Task | Status | Commits | Notes |
|---|---|---|---|---|
| 3B.1 | Carry-over fixes(a–e) | DONE | 6eb6b04, 4ef2ff7 | This pass completes a/b/c |
| 3B.2 | Rate limiting/abuse | SKIPPED | — | Budget boundary; not implemented |
| 3B.3 | Timeline/status event | SKIPPED | — | Not started |
| 3B.4 | Credentials/configuration | SKIPPED | — | Not started |
| 3B.5 | Async payments/refunds/COD | SKIPPED | — | Not started |
| 3B.P1 | Quality/Docker proof | PARTIAL | 4ef2ff7 | Task1 gates/PG migration verified; acceptance E2E and remaining modules not implemented |
| 3B.6 | Shipping lifecycle | Part 2 | — | Out of scope |
| 3B.7 | Outbound webhooks | Part 2 | — | Out of scope |
| 3B.8 | Notifications | Part 2 | — | Out of scope |
| 3B.9 | Public tracking | Part 2 | — | Out of scope |
| 3B.10 | SSE | Part 2 | — | Out of scope |
| 3B.11 | Failed-jobs API | Part 2 | — | Out of scope |
| 3B.12 | OpenAPI/final quality/docs | Part 2 | — | Part1 gates recorded separately |
| 3B.13 | Full-phase Docker proof | Part 2 | — | Part1 subset remains outstanding |

## B3. TDD evidence

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

## B4. Raw outputs and production coverage

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
introduced (migration coverage is demonstrated separately). Redis backend/tests/CI
changes and coverage targets for unimplemented Part1 modules are NOT VERIFIED.

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

## B6. Docker acceptance and restored runtime

E1–E18/E20/E28/E30 are NOT VERIFIED in this pass; no async/rate-limit runner exists
yet. Historical Phase3A E1–E14 results are not relabeled as new evidence. Existing
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

## B7. Proofs and isolation

The PG mixed race applies twenty signed adjustments alongside thirty reservations,
checking available=20+accepted deltas−reserved and reserved_quantity=reserved orders.
Raw output is B5. API tests verify audit actor/reason/counts and patch FOR UPDATE SQL.
The isolation matrix now covers20 tenant method/path combinations ×4 principals
(80 combinations), including foreign404 and own-role/key adjustment behavior. The
unclassified-route guard includes the new endpoint. Both run in the full suite.
Lock-free providers, webhook concurrency and refund exactly-once proofs are NOT VERIFIED.

## B8. Razorpay verification ledger

No new Razorpay API/field/header/event is used. Official provider-doc verification
and live test mode remain NOT VERIFIED because no adapter is implemented. Added
tests perform no real external network calls.

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
| Provider-under-lock/refund journal | DEFERRED | 3B.5 not implemented |
| Abuse controls | DEFERRED | 3B.2 not implemented |
| Shipping lifecycle, notifications, tracking, SSE, failed-jobs API | DEFERRED | Part2 |
| Historical data/VALIDATED states, RLS/email uniqueness, failure-store outage | DEFERRED | Existing limitations retained |
| RabbitMQ HA/volume, real monitoring, hosted CI/deployment | DEFERRED | No operational redesign |

## B10. Changed files and commits

Snapshot before this report-only commit; it does not include its own diff/hash.

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

## B11. Deviations and modified existing tests

- Working rule5 permits stopping at a clean completed-task commit when budget is
  short. This delivery completes3B.1, not all of Part1. No task2–5 code is committed.
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
| test_tenant_isolation::test_route_registry_has_no_unclassified_routes | Add adjustment method/path to tenant set; no category bypass |

No original test is deleted or weakened. New PG/SQLite migration, collision and
adjustment tests are additions; prior d/e tests remain unchanged.

## B12. Newly discovered issues

No additional production issue is established beyond the fixed missing API-key
audit identity. HTTP low-stock pagination and other deferred Phase3A limitations
remain explicit. No broad codebase/security audit is claimed.

## B13. NOT VERIFIED

- Tasks3B.2–5 and their acceptance tests, Redis backend/tests/CI integration.
- Part1 E1–E18/E20/E28/E30 reruns; only runtime/schema restoration is verified here.
- Async money movement, official/live Razorpay behavior, encryption configuration.
- Lock-free external calls, webhook concurrency and durable refund reliability.
- Part2 features and production/HA/soak/hosted CI/push/PR/merge.
- Historical-data reconciliation or arbitrary production downgrade eligibility.

## B14. Reviewer commands (PowerShell)

```powershell
git switch phase-3b-integrations
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .
.venv\Scripts\mypy app
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
$env:COVERAGE_FILE='.coverage.p1.sqlite'
.venv\Scripts\python -m pytest -q --cov=app --cov-report=term-missing
$env:TEST_DATABASE_URL='postgresql+psycopg://orders:orders@localhost:5432/orders_test'
$env:COVERAGE_FILE='.coverage.p1.postgres'
.venv\Scripts\python -m pytest -q --cov=app --cov-report=term-missing
.venv\Scripts\python -m pytest -q -s app/tests/test_prefix_migration.py::test_postgres_prefix_migration app/tests/test_postgres_reliability.py::test_postgres_adjustments_and_thirty_reservations_conserve_stock
Remove-Item Env:TEST_DATABASE_URL
docker compose ps -a
git diff --check main...HEAD
git diff --stat main...HEAD
git log --oneline main..HEAD
```

Only use the disposable `_test` database for pytest. Migration fixtures use unique
schemas. Stop writers before operational schema changes. No report output includes
key plaintext; ignored local coverage files supply the committed raw evidence.
