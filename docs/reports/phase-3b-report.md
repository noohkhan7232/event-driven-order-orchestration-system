# Phase 3B — partial implementation and verification

Date: 21 November 2026, Asia/Calcutta. Branch: `phase-3b-integrations`.
**This is not a completed Phase 3B release.** Only task 3B.1(d/e) is implemented.
The remaining work is explicitly recorded below. The requested full phase exceeds
this execution's available budget; the prompt permits a partial numbered-order delivery.

# PART A — Current architecture and operations

## A1. Async payment saga

NOT IMPLEMENTED. Payments remain the synchronous mock flow documented in
[Phase 3A](phase-3a-report.md). No async-payment/COD/expiry state diagram is claimed.

## A2. Provider architecture and credentials

Existing Phase 3A interfaces/mocks remain. No interface v2, Razorpay adapter,
credential encryption or per-tenant payment configuration is added. Existing mock
calls still occur within database transactions; the new phase's lock-free external
call architecture is not implemented or verified. No real external provider is called.

## A3. Refunds and reconciliation

Existing synchronous mock refunds remain. No durable refund journal, unknown-outcome
reconciliation or retry worker is implemented.

## A4. Shipping and COD

Existing shipment behavior remains; manual-provider lifecycle and COD collection
are not implemented.

## A5. Outbound webhooks

NOT IMPLEMENTED. There is no seller webhook delivery/signing/SSRF subsystem.

## A6. Notifications

NOT IMPLEMENTED. No SMTP, notification journal, templates or dev-tools services are added.

## A7. Public tracking and SSE

NOT IMPLEMENTED. No public tokens, tracking routes, timeline or streams are added.

## A8. Abuse controls and login hardening

Rate limiting and registration controls are NOT IMPLEMENTED. Login now verifies
the supplied password against a precomputed bcrypt dummy hash when no matching
user exists. The dummy hash is generated once per process from random unusable
material, using the same password hashing configuration as real users. Known users
still verify against their stored hash. Both failure paths retain the identical
existing HTTP403 body, `Invalid email or password`.

This removes the missing password-verification work; it does not promise equal
end-to-end request latency or address brute force without rate limiting.

## A9. Security model

| Threat | Current mitigation | Evidence / remaining limitation |
|---|---|---|
| Login enumeration through skipped hashing | Dummy verification for unknown email | Spy asserts one verification in both failure branches; identical bodies |
| Duplicate-resource database exceptions | Typed409 with rollback | API tests for both warehouse and inventory duplicates |
| Foreign-tenant IDs | Existing Phase3A scope enforcement | Existing isolation suite retained |
| Forged provider webhooks / SSRF / public tokens / SSE DoS | No new public surface introduced | Requested subsystems NOT IMPLEMENTED |
| Inventory lost updates / short key prefixes / unscoped internal access | Existing behavior remains | Remaining 3B.1(a–c), not resolved by this pass |

## A10. Data model and events

No tables, columns, migrations, enum labels or event types change. Existing Phase3A
schema/event catalog remains authoritative. No new ER diagram is needed for these
two fixes; the requested broader Phase3B schema is NOT IMPLEMENTED.

## A11. Configuration

No environment variables or dependencies are added. Existing password hashing
configuration supplies the dummy hash. No provider/SMTP/Redis integration settings
are introduced.

## A12. API changes

No routes or request/response schemas are added. The existing route registry and
isolation matrix remain in force. No exported OpenAPI artifact is added.

| Existing endpoint | Change |
|---|---|
| POST /api/v1/auth/login | Unknown email performs password verification; generic403 unchanged |
| POST /api/v1/inventory/warehouses | Duplicate owned code returns409 `warehouse_code_conflict` |
| POST /api/v1/inventory | Duplicate owned SKU/warehouse returns409 `inventory_conflict` |

Unique-constraint recognition uses PostgreSQL SQLSTATE23505 or SQLite extended
codes1555/2067. Both create handlers rollback before raising a domain conflict.
Other IntegrityErrors propagate, so foreign-key/check/storage failures are not
misreported as duplicates. Existing product/registration/idempotency conflicts
retain their established409 behavior.

## A13. Operations runbook

No migration or credential setup is required for these changes. Deploying the
existing API code enables the fixes. Do not configure a real Razorpay account on
the assumption that this partial branch supports it. Reproduction commands are B14.

## A14. Limitations

Tasks3B.1(a–c) and3B.2–13 remain outstanding. Phase3A limitations remain except
warehouse/inventory duplicate500 handling and unknown-email skipped password work.
No full-phase security, external-integration or production-readiness claim is made.

# PART B — Verification

## B1. Summary

- Main contained merged Phase3A at `bdab081`; pull reported already up to date.
- Work starts on the requested `phase-3b-integrations` branch with a clean tree.
- Task3B.1(d/e) is implemented; task3B.1 overall remains PARTIAL.
- Unknown-email and wrong-password branches both perform password verification.
- Duplicate warehouse/inventory inserts produce typed409 errors and rollback.
- Two new regression tests fail before implementation and pass afterward.
- No existing test is weakened, deleted or modified.
- No new public route, migration or external network integration is introduced.
- Full regression and coverage results follow; unrun layers remain NOT VERIFIED.
- Tasks3B.2–13 are SKIPPED for this partial delivery, not represented as complete.

## B2. Task status

| ID | Task | Status | Commit | Notes |
|---|---|---|---|---|
| 3B.1 | Carry-over fixes | PARTIAL | 6eb6b04 | Only(d/e); inventory locks/adjustments, access cleanup/keyset scans, prefix migration outstanding |
| 3B.2 | Rate limiting/abuse | SKIPPED | — | Execution budget; not implemented |
| 3B.3 | Timeline/status event | SKIPPED | — | Execution budget; not implemented |
| 3B.4 | Credentials/configuration | SKIPPED | — | Execution budget; not implemented |
| 3B.5 | Async payment/refunds/COD | SKIPPED | — | Execution budget; not implemented |
| 3B.6 | Shipping lifecycle | SKIPPED | — | Execution budget; not implemented |
| 3B.7 | Outbound webhooks | SKIPPED | — | Execution budget; not implemented |
| 3B.8 | Notifications | SKIPPED | — | Execution budget; not implemented |
| 3B.9 | Public tracking | SKIPPED | — | Execution budget; not implemented |
| 3B.10 | SSE | SKIPPED | — | Execution budget; not implemented |
| 3B.11 | Failed-jobs API | SKIPPED | — | Execution budget; not implemented |
| 3B.12 | Quality/docs hygiene | PARTIAL | 6eb6b04 | Regression gates measured; no Redis CI/OpenAPI export/new full-phase coverage |
| 3B.13 | Docker proof | SKIPPED | — | Not run for this partial pass |

## B3. TDD evidence

Before implementation:

```text
test_login_unknown_and_wrong_password_both_verify
assert verify.call_count == 1
E AssertionError: assert 0 == 1

test_duplicate_warehouse_and_inventory_are_typed_conflicts
E sqlite3.IntegrityError: UNIQUE constraint failed: warehouses.tenant_id, warehouses.code
2 failed in 5.25s
```

After implementation:

```text
.. [100%]
2 passed in 2.99s
```

Tests live in `app/tests/test_integration_carryover.py`. The login test verifies the
hash argument shape, call count, status and identical generic response. The duplicate
test creates a valid owned product/warehouse/stock and repeats each creation to
assert the exact error code; reuse of the client after a conflict exercises rollback.

## B4. Raw gates and production coverage

```text
All checks passed!
109 files already formatted
Success: no issues found in 90 source files
```

```text
........................................................................ [ 29%]
........................................................................ [ 58%]
...............................sssssss............................s..... [ 87%]
..........s....................                                          [100%]

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
app\api\v1\endpoints\inventory.py      87     15    83%   27, 44, 70, 98, 104-105, 116-124
app\api\v1\endpoints\orders.py         30      2    93%   47-49
app\api\v1\endpoints\products.py       27      2    93%   37-38
app\api\v1\endpoints\shipments.py      22      6    73%   29-34
app\api\v1\endpoints\tenancy.py        25      0   100%
app\api\v1\router.py                   11      0   100%
app\auth\dependencies.py               16      0   100%
app\auth\principal.py                  93      4    96%   59, 66, 83, 96
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
app\services\auth.py                   39      2    95%   28-30
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
TOTAL                                1873     65    97%
Coverage JSON written to file .coverage-3b-sqlite.json

238 passed, 9 skipped in 105.62s (0:01:45)
```

```text
........................................................................ [ 29%]
........................................................................ [ 58%]
........................................................................ [ 87%]
...............................                                          [100%]

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
app\api\v1\endpoints\inventory.py      87     15    83%   27, 44, 70, 98, 104-105, 116-124
app\api\v1\endpoints\orders.py         30      2    93%   47-49
app\api\v1\endpoints\products.py       27      2    93%   37-38
app\api\v1\endpoints\shipments.py      22      6    73%   29-34
app\api\v1\endpoints\tenancy.py        25      0   100%
app\api\v1\router.py                   11      0   100%
app\auth\dependencies.py               16      0   100%
app\auth\principal.py                  93      4    96%   59, 66, 83, 96
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
app\services\auth.py                   39      2    95%   28-30
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
TOTAL                                1873     67    96%
Coverage JSON written to file .coverage-3b-postgres.json

247 passed in 165.77s (0:02:45)
```

Production-only line coverage: SQLite 96.53%; PostgreSQL 96.42%. Per-module numbers are in the raw tables above.

Redis tests are NOT VERIFIED: no new Redis backend/tests/CI service is implemented.
These are regression results for the current partial tree, not full Phase3B acceptance.

## B5. Migrations

No migration is added. Legacy0007→new-head→0007 checks for Phase3B are NOT VERIFIED;
there is no new migration head in this pass. Existing migration tests remain in
the full regression collection.

## B6. Docker E1–E30

NOT VERIFIED in this pass. No Docker experiment or runtime-setting change is claimed.
Previous Phase3A E1–E14 results are historical evidence in that phase's report.

## B7. Requested architecture proofs

NOT VERIFIED: lock-free provider calls, webhook concurrency/deduplication, refund
exactly-once, SSRF matrix and new-route isolation. Those features are not implemented.
The existing route registry/isolation tests remain unchanged and run in the suite.

## B8. Razorpay verification ledger

No Razorpay API, field, header or event name is used by new code. Official docs
verification and live test-mode execution are NOT VERIFIED; no adapter is implemented
and no external provider network calls are made by the added tests.

## B9. Phase3A carry-over

| Item | Status | Reason |
|---|---|---|
| Duplicate warehouse/inventory500 | RESOLVED | Typed409 + transaction rollback |
| Login missing-user hash shortcut | RESOLVED | Precomputed dummy verification |
| Inventory PATCH race/adjustment API | DEFERRED | Remaining3B.1(a) |
| Unscoped repository helpers/global low-stock cap | DEFERRED | Remaining3B.1(b) |
| Short key prefix | DEFERRED | Remaining3B.1(c), migration not added |
| Provider calls under locks / refund journal | DEFERRED | Async architecture not implemented |
| Rate limits, notification/tracking/SSE/admin public surface | DEFERRED | Remaining tasks |
| Historical reservations/jobs/VALIDATED reconciliation | DEFERRED | Not implemented |
| Shipment→order lifecycle / monitoring stubs | DEFERRED | Not implemented |
| DB outage failure storage, RabbitMQ HA/volume | DEFERRED | No storage/deployment changes |
| RLS/global email uniqueness | DEFERRED | Existing limitations retained |
| Hosted CI/production/README copy-paste verification | DEFERRED | Not executed |

## B10. Files and commits

Snapshot before the report-only commit:

```text
app/api/v1/endpoints/inventory.py       | 23 +++++++++++++++++++---
 app/core/exceptions.py                  |  8 ++++++++
 app/services/auth.py                    |  7 ++++++-
 app/tests/test_integration_carryover.py | 35 +++++++++++++++++++++++++++++++++
 4 files changed, 69 insertions(+), 4 deletions(-)
```

```text
6eb6b04 fix(security): harden login verification and duplicate inventory errors
```

## B11. Deviations and assumptions

- Partial delivery is permitted by working rule7 when budget is short. Only(d/e)
  of the first task is implemented; no claim is made that all numbered work ran.
- Targeted files and carry-over sections are read; the exhaustive all-source
  preflight audit is not completed and remains outstanding before broader changes.
- No pre-existing tests are modified. Added tests retain the existing generic403
  login status; this task requests equal error text and hashing work, not a status change.
- The dummy hash is precomputed per process, using existing bcrypt settings and
  randomly generated unusable material. It is not a usable account credential.
- No README integration instructions are added for unimplemented features.
- One implementation commit addresses the worked portion of task1; the report is
  committed separately as requested. No push/PR/hosted CI is performed.

## B12. Newly discovered issues

No additional production issue is established by these two regressions. Remaining
known Phase3A issues and unimplemented Phase3B work are listed above.

## B13. NOT VERIFIED

- Completion of3B.1(a–c),3B.2–13 and their acceptance tests.
- Redis backend/CI/tests, full Phase3B new-module coverage targets, OpenAPI export.
- Docker E1–E30 reruns and restored-runtime-settings proof for this pass.
- New migration roundtrip/refusal from0007.
- Official Razorpay details and live test mode; real provider/SMTP/webhook behavior.
- Lock-free external-call architecture, SSRF, refund reliability, tracking/SSE proofs.
- Exhaustive requested preflight source audit, README integration examples.
- Hosted CI, push/PR/merge, production/HA/soak and historical reconciliation.

## B14. Reviewer commands (PowerShell)

```powershell
git switch phase-3b-integrations
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .
.venv\Scripts\mypy app
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
$env:COVERAGE_FILE='.coverage.3b.sqlite'
.venv\Scripts\python -m pytest -q --cov=app --cov-report=term-missing
# Start existing PostgreSQL infrastructure first. Only use the disposable test DB.
$env:TEST_DATABASE_URL='postgresql+psycopg://orders:orders@localhost:5432/orders_test'
$env:COVERAGE_FILE='.coverage.3b.postgres'
.venv\Scripts\python -m pytest -q --cov=app --cov-report=term-missing
Remove-Item Env:TEST_DATABASE_URL
git diff --check main...HEAD
git diff --stat main...HEAD
git log --oneline main..HEAD
```
