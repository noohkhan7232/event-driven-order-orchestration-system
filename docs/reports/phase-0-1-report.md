## 1. **Summary**

Completed Phase 0 tooling and Phase 1 hardening on `phase-0-1-hardening`.  
Signup always creates customers; email duplicate checks normalize before lookup.  
Order idempotency is customer-scoped, checks canonical payload hashes, and recovers insert races.  
Cancellation compensates inventory/payment in one transaction and workers skip cancelled orders.  
Production secret validation, protected metrics, route-template labels and exception logging are implemented.  
Final checks passed: Ruff, mypy, 48 tests, and 92% reported coverage (including test modules).  
SQLite migrations and real local HTTP smoke passed; PostgreSQL, RabbitMQ delivery and hosted CI remain NOT VERIFIED.

## 2. **Task status table**

| ID | Task | Status (DONE / PARTIAL / SKIPPED) | Commit hash(es) | Notes |
|---|---|---|---|---|
| 0.1 | Repo hygiene | DONE | 4e988fc, dc54df4 | Untracked bytecode without deleting source; no tracked DBs existed; ignore local secrets/artifacts and Docker build artifacts. No real credentials found in inspected source/config; history not exhaustively scanned. |
| 0.2 | Demo videos | DONE | 3bdbddf | Removed requested MP4s and links; pending notes added. Git held LFS pointers, but checkout held downloaded videos. |
| 0.3 | Honest docs | DONE | 3bdbddf, 4516f02, dc54df4 | Replaced scored review, corrected async response, login/OpenAPI paths, static worker and incomplete DLQ/cache claims. |
| 0.4 | Tooling config | DONE | f6611f7, dc54df4 | Runtime/dev split, Ruff, lenient mypy, pre-commit and Make targets; Dockerfile still installs runtime requirements only. |
| 0.5 | CI | DONE | f7134e5 | Python 3.12 push/PR workflow and badge; actual hosted execution NOT VERIFIED. |
| 0.6 | Isolated formatting | DONE | d053d70 | Separate `style: apply ruff formatting` commit. |
| 1.1 | Signup role escalation | DONE | 13f81cb | Removed signup role; explicit customer assignment; privileged test users inserted directly; seed now includes vendor. |
| 1.2 | Scoped idempotency | DONE | 4516f02 | Composite uniqueness, canonical SHA256, conflict response, rollback/re-fetch, nullable legacy hashes; unused TTL removed. |
| 1.3 | Email normalization | DONE | 8b93577 | Lowercase before duplicate lookup and persistence. |
| 1.4 | Pagination total | DONE | a386eeb | Customer-filtered count independent of limit/offset. |
| 1.5 | Production secret guard | DONE | aee032e | Non-local weak defaults and secrets shorter than 32 rejected. |
| 1.6 | Metrics protection/cardinality/context | DONE | d206671 | Local open; production disabled without token; token or admin JWT when configured; bounded labels and finally cleanup. |
| 1.7 | Cancellation correctness | DONE | b343a83 | Reservation flag, synchronous audited compensation, terminal checks, repeated-cancel no-op and cancelled-worker guard. Sequential safety; concurrency limitations remain out of scope. |
| 1.8 | Unhandled exception logging | DONE | 6167ab1 | Exception trace and request ID logged; standard safe 500 body retained. |

## 3. **Test evidence**

Regression tests were written and run before their corresponding implementation changes. Below are brief excerpts of actual failing outputs; all named tests pass in the final full run.

- **1.1:** `test_signup_ignores_privileged_role` checks customer role and 403 on the admin/vendor inventory endpoint.
  Before:
  ```text
  E       AssertionError: assert 'admin' == 'customer'
  1 failed in 0.73s
  ```
  After: full suite `5 passed in 2.40s`.
- **1.2:** `test_idempotency_is_customer_scoped`, `test_idempotency_payload_conflict_and_replay`, `test_idempotency_insert_race`.
  Before:
  ```text
  E   AssertionError: assert '34de8532-df1d-4aac-9d64-b43a7625a605' != '34de8532-df1d-4aac-9d64-b43a7625a605'
  E   assert 202 == 409
  E   sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) UNIQUE constraint failed: orders.idempotency_key
  3 failed, 1 deselected in 3.45s
  ```
  After: `8 passed in 4.89s`. Replay asserts one order row, one publication and the same ID; currency casing is normalized. Race is simulated by one lookup miss over an existing committed order.
- **1.3:** `test_signup_email_normalizes_before_duplicate_check`.
  Before:
  ```text
  E   sqlite3.IntegrityError: UNIQUE constraint failed: users.email
  1 failed, 4 deselected in 1.35s
  ```
  After: `9 passed in 5.12s`; case-variant signup returns 409.
- **1.4:** `test_customer_pagination_counts_all_matching_orders` includes another customer's order and offset/limit.
  Before:
  ```text
  E   assert 2 == 3
  1 failed, 5 deselected in 1.55s
  ```
  After: `10 passed in 6.26s`.
- **1.5:** `test_nonlocal_rejects_weak_secret` covers three non-local environments and three weak secrets.
  Before:
  ```text
  E   Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>
  9 failed, 4 passed in 1.86s
  ```
  `test_local_accepts_demo_secret` and `test_production_accepts_strong_secret` already passed before the fix and remain positive controls. After: `23 passed in 7.88s`.
- **1.6:** `test_metrics_access`, `test_metrics_admin_jwt`, `test_metrics_order_ids_share_route_template`, `test_request_context_cleared_on_exception`.
  Before:
  ```text
  E   AssertionError: assert 200 == 404
  E   AssertionError: assert 200 == 403
  E   AssertionError: assert 2 == 1
  E   AssertionError: assert {'request_id'...} == {}
  8 failed, 5 passed in 4.22s
  ```
  Allowed-access cases were positive controls and already passed. After: `36 passed in 11.25s`.
- **1.7:** `test_cancel_before_reserve_publishes_nothing`, `test_cancel_after_reserve_restores_once_and_double_cancel_is_noop`, `test_cancel_refunds_authorized_payment_once`, `test_cancel_terminal_order_conflicts`, `test_worker_skips_cancelled_order`, `test_payment_failure_clears_inventory_reservation`.
  Before:
  ```text
  E   AssertionError: assert [{'args': [{'... 'inventory'}] == []
  E   assert 8 == 10
  E   AssertionError: assert <PaymentStatu... 'authorized'> == <PaymentStatu...D: 'refunded'>
  E   Failed: DID NOT RAISE <class 'app.core.exceptions.ConflictError'>
  E   assert [True] == []
  E   AttributeError: 'Order' object has no attribute 'inventory_reserved'
  8 failed, 2 passed in 1.96s
  ```
  Shipped/delivered guards already passed. Compound tests failed at the first unsafe behavior; later repeated-cancel/release and audit assertions were exercised after the fix. After: `46 passed in 13.70s`. The payment-failure helper already invoked release; the new release implementation now clears the flag.
- **1.8:** `test_unhandled_exception_logs_request_id_and_safe_body`.
  Before:
  ```text
  E   assert 'unhandled_exception' in output
  1 failed in 0.84s
  ```
  After: `47 passed in 14.30s`. Response excludes the injected private failure and log contains the request ID.

Additional verification: `test_legacy_migration_backfill_and_safe_downgrade` exercises existing rows, reservation inference, complete downgrade/upgrade, and refusal of incompatible global uniqueness without deleting orders. Its first run exposed a fixture binding issue (SQLite reflects the original PostgreSQL UUID type as NUMERIC); fixture UUID binding was corrected without changing production migration 0001 behavior. Final suite: 48 passing tests.

## 4. **Command outputs**

Commands used the Python 3.12.6 virtual environment in `.venv`. Binary/module invocation is equivalent to the requested commands.

`ruff check .`
```text
All checks passed!
```

`ruff format --check .`
```text
61 files already formatted
```

`mypy app`
```text
Success: no issues found in 52 source files
```

`python -m pytest -q --cov=app --cov-report=term-missing`
```text
................................................                         [100%]

---------- coverage: platform win32, python 3.12.6-final-0 -----------
Name                                     Stmts   Miss  Cover   Missing
----------------------------------------------------------------------
app\__init__.py                              0      0   100%
app\api\__init__.py                          0      0   100%
app\api\v1\__init__.py                       0      0   100%
app\api\v1\endpoints\__init__.py             0      0   100%
app\api\v1\endpoints\auth.py                17      0   100%
app\api\v1\endpoints\health.py              35      4    89%   25-27, 51
app\api\v1\endpoints\inventory.py           32     14    56%   21-25, 34-38, 48-49, 57-58
app\api\v1\endpoints\orders.py              29      2    93%   45, 56
app\api\v1\endpoints\shipments.py           19      6    68%   24-29
app\api\v1\router.py                         8      0   100%
app\auth\dependencies.py                    29      1    97%   30
app\auth\security.py                        21      0   100%
app\core\config.py                          29      0   100%
app\core\exceptions.py                      20      0   100%
app\core\logging.py                         10      0   100%
app\db\base.py                              11      0   100%
app\db\session.py                           12      4    67%   14-18
app\events\contracts.py                     19      0   100%
app\events\publisher.py                      8      0   100%
app\main.py                                 21      0   100%
app\middleware\request_context.py           23      0   100%
app\models\__init__.py                       2      0   100%
app\models\entities.py                     100      0   100%
app\models\enums.py                         35      0   100%
app\observability\metrics.py                 5      0   100%
app\repositories\base.py                    19      3    84%   20-22
app\repositories\inventory.py                7      0   100%
app\repositories\orders.py                  15      0   100%
app\repositories\users.py                    7      0   100%
app\schemas\auth.py                         20      0   100%
app\schemas\common.py                       15      0   100%
app\schemas\inventory.py                    21      0   100%
app\schemas\orders.py                       45      0   100%
app\schemas\shipments.py                     6      0   100%
app\services\auth.py                        24      1    96%   33
app\services\inventory.py                   41      8    80%   18, 31, 41-42, 45-48
app\services\orders.py                      72      2    97%   62, 96
app\services\payments.py                    23      9    61%   15-31
app\services\shipping.py                    19     10    47%   12, 15-25, 28-30
app\tests\conftest.py                       47      0   100%
app\tests\test_auth_and_orders.py           28      0   100%
app\tests\test_cancel_hardening.py          86      0   100%
app\tests\test_exception_hardening.py       17      0   100%
app\tests\test_hardening.py                 61      0   100%
app\tests\test_health.py                     4      0   100%
app\tests\test_metrics_hardening.py         40      0   100%
app\tests\test_migrations_hardening.py      49      0   100%
app\tests\test_settings_hardening.py        13      0   100%
app\tests\test_worker_flow.py               19      0   100%
app\workers\celery_app.py                    8      0   100%
app\workers\tasks.py                        84     39    54%   28-40, 50-51, 61-69, 73-91, 98-100, 119-122, 130-133, 138-139
----------------------------------------------------------------------
TOTAL                                     1275    103    92%

48 passed in 16.71s
```

Coverage is the requested `--cov=app` result; it includes `app/tests` and is not a production-code-only percentage.

`docker compose up -d postgres` failed:
```text
unable to get image 'postgres:16-alpine': failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
```

SQLite commands:
```powershell
$env:DATABASE_URL='sqlite:///./phase_hardening_migration_check.db'
alembic upgrade head
alembic downgrade -1
alembic upgrade head
alembic downgrade 0001_initial
alembic upgrade head
```

Raw output (all five commands exited 0):
```text
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial, initial order platform schema
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_idempotency, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0003_reservation -> 0002_idempotency, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade 0003_reservation -> 0002_idempotency, Track outstanding inventory reservations for safe sequential compensation.
INFO  [alembic.runtime.migration] Running downgrade 0002_idempotency -> 0001_initial, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_idempotency, Scope idempotency by customer and store validated request fingerprints.
INFO  [alembic.runtime.migration] Running upgrade 0002_idempotency -> 0003_reservation, Track outstanding inventory reservations for safe sequential compensation.
```

Real local HTTP smoke: `python scripts/smoke_hardening.py`, exit 0. The script starts Uvicorn on loopback, migrates an isolated SQLite DB and sets `CELERY_BROKER_URL=memory://` with no worker. Authorization uses the login JWT and order requests use `Idempotency-Key: smoke-key`. Below are actual request/response lines, omitting routine server logs; the ephemeral JWT is redacted:
```text
POST /api/v1/auth/signup {"email": "smoke@example.com", "password": "smoke-password", "full_name": "Smoke User", "role": "admin"}
201 {"email": "smoke@example.com", "full_name": "Smoke User", "id": "745618a0-4761-43c8-9b04-1885c3190365", "is_active": true, "role": "customer"}
POST /api/v1/auth/login {"email": "smoke@example.com", "password": "smoke-password"}
200 {"access_token": "<redacted>", "token_type": "bearer"}
POST /api/v1/orders {"currency": "USD", "items": [{"sku": "SKU", "quantity": 1}]}
202 {"currency": "USD", "failure_reason": null, "id": "a79e3c98-4284-4607-b182-2f9b1a5fd516", "items": [{"id": "64dd2bad-b5ce-46ec-9386-83ff318b278b", "product_name": "Pending catalog lookup", "quantity": 1, "sku": "SKU", "unit_price": "49.99", "warehouse_id": null}], "payments": [], "shipments": [], "status": "created", "total_amount": "49.99"}
POST /api/v1/orders {"currency": "USD", "items": [{"sku": "SKU", "quantity": 1}]}
202 {"currency": "USD", "failure_reason": null, "id": "a79e3c98-4284-4607-b182-2f9b1a5fd516", "items": [{"id": "64dd2bad-b5ce-46ec-9386-83ff318b278b", "product_name": "Pending catalog lookup", "quantity": 1, "sku": "SKU", "unit_price": "49.99", "warehouse_id": null}], "payments": [], "shipments": [], "status": "created", "total_amount": "49.99"}
POST /api/v1/orders {"currency": "USD", "items": [{"sku": "SKU", "quantity": 2}]}
409 {"code": "idempotency_key_conflict", "details": {}, "message": "This idempotency key was already used with a different payload"}
POST /api/v1/orders/a79e3c98-4284-4607-b182-2f9b1a5fd516/cancel null
200 {"currency": "USD", "failure_reason": null, "id": "a79e3c98-4284-4607-b182-2f9b1a5fd516", "items": [{"id": "64dd2bad-b5ce-46ec-9386-83ff318b278b", "product_name": "Pending catalog lookup", "quantity": 1, "sku": "SKU", "unit_price": "49.99", "warehouse_id": null}], "payments": [], "shipments": [], "status": "cancelled", "total_amount": "49.99"}
POST /api/v1/orders/a79e3c98-4284-4607-b182-2f9b1a5fd516/cancel null
200 {"currency": "USD", "failure_reason": null, "id": "a79e3c98-4284-4607-b182-2f9b1a5fd516", "items": [{"id": "64dd2bad-b5ce-46ec-9386-83ff318b278b", "product_name": "Pending catalog lookup", "quantity": 1, "sku": "SKU", "unit_price": "49.99", "warehouse_id": null}], "payments": [], "shipments": [], "status": "cancelled", "total_amount": "49.99"}
SMOKE PASSED (in-memory broker, no worker; JWT redacted)
```

## 5. **Migrations**

- `alembic/versions/0002_customer_idempotency.py`: drops global `uq_orders_idempotency_key`, creates `uq_orders_customer_idempotency_key(customer_id, idempotency_key)`, adds nullable `request_hash`. Historical request bodies were not retained, so legacy rows keep NULL; replay reconstructs a hash from stored currency/SKU/quantity. New orders always write a hash. Canonical JSON sorts keys, uppercases currency and sorts items by SKU then quantity.
- Downgrade 0002 checks for duplicate keys across customers before changing the constraint. It raises a clear error if global uniqueness cannot be restored, preserving data. Otherwise it restores the old constraint and removes the hash.
- `alembic/versions/0003_inventory_reservation.py`: adds non-null `inventory_reserved` default false. Backfills true for warehouse-assigned orders in inventory-reserved/payment-pending/paid/fulfillment-requested/shipped/delivered states, matching the old workflow's retained reservations. Ambiguous historical cancelled/failed rows need reconciliation; no reliable per-order reservation ledger exists.
- Downgrade 0003 removes only the reservation flag; it does not alter stock.
- Both new migrations use batch alteration for SQLite compatibility. Migration 0001 changed only through lint/format tooling.
- Stop old writers/workers while migrating so old global-key lookups and reservation behavior are not mixed with the new schema. No deployment automation was changed.

## 6. **Files changed**

Implementation snapshot at `dc54df4`, before the report-only commit, to avoid a self-referential commit hash. Exact output of `git diff --stat main...HEAD` followed by `git log --oneline main..HEAD`:
```text
 .dockerignore                                      |  15 +++
 .env.example                                       |   1 -
 .github/workflows/ci.yml                           |  16 +++
 .gitignore                                         |  19 ++++
 .pre-commit-config.yaml                            |  13 +++
 Makefile                                           |  18 +++-
 README.md                                          |  46 ++++----
 alembic/env.py                                     |   2 +-
 alembic/versions/0001_initial_order_platform.py    |  43 ++++++--
 alembic/versions/0002_customer_idempotency.py      |  34 ++++++
 alembic/versions/0003_inventory_reservation.py     |  31 ++++++
 app/__pycache__/__init__.cpython-312.pyc           | Bin 112 -> 0 bytes
 app/__pycache__/main.cpython-312.pyc               | Bin 2102 -> 0 bytes
 app/api/__pycache__/__init__.cpython-312.pyc       | Bin 116 -> 0 bytes
 app/api/v1/__pycache__/__init__.cpython-312.pyc    | Bin 119 -> 0 bytes
 app/api/v1/__pycache__/router.cpython-312.pyc      | Bin 938 -> 0 bytes
 .../endpoints/__pycache__/__init__.cpython-312.pyc | Bin 129 -> 0 bytes
 .../v1/endpoints/__pycache__/auth.cpython-312.pyc  | Bin 1689 -> 0 bytes
 .../endpoints/__pycache__/health.cpython-312.pyc   | Bin 1702 -> 0 bytes
 .../__pycache__/inventory.cpython-312.pyc          | Bin 3531 -> 0 bytes
 .../endpoints/__pycache__/orders.cpython-312.pyc   | Bin 3180 -> 0 bytes
 .../__pycache__/shipments.cpython-312.pyc          | Bin 1628 -> 0 bytes
 app/api/v1/endpoints/health.py                     |  28 ++++-
 app/api/v1/endpoints/inventory.py                  |   2 +-
 app/api/v1/endpoints/orders.py                     |   2 +-
 app/auth/__pycache__/dependencies.cpython-312.pyc  | Bin 2128 -> 0 bytes
 app/auth/__pycache__/security.cpython-312.pyc      | Bin 2090 -> 0 bytes
 app/auth/dependencies.py                           |   2 +-
 app/core/__pycache__/config.cpython-312.pyc        | Bin 1816 -> 0 bytes
 app/core/__pycache__/exceptions.cpython-312.pyc    | Bin 1493 -> 0 bytes
 app/core/__pycache__/logging.cpython-312.pyc       | Bin 1827 -> 0 bytes
 app/core/config.py                                 |  13 ++-
 app/core/exceptions.py                             |   4 +
 app/db/__pycache__/base.cpython-312.pyc            | Bin 1474 -> 0 bytes
 app/db/__pycache__/session.cpython-312.pyc         | Bin 897 -> 0 bytes
 app/db/base.py                                     |   7 +-
 app/events/__pycache__/contracts.cpython-312.pyc   | Bin 1467 -> 0 bytes
 app/events/__pycache__/publisher.cpython-312.pyc   | Bin 1140 -> 0 bytes
 app/main.py                                        |  10 +-
 .../__pycache__/request_context.cpython-312.pyc    | Bin 2391 -> 0 bytes
 app/middleware/request_context.py                  |  34 +++---
 app/models/__pycache__/__init__.cpython-312.pyc    | Bin 382 -> 0 bytes
 app/models/__pycache__/entities.cpython-312.pyc    | Bin 11105 -> 0 bytes
 app/models/__pycache__/enums.cpython-312.pyc       | Bin 1791 -> 0 bytes
 app/models/entities.py                             |   8 +-
 .../__pycache__/metrics.cpython-312.pyc            | Bin 641 -> 0 bytes
 app/repositories/__pycache__/audit.cpython-312.pyc | Bin 664 -> 0 bytes
 app/repositories/__pycache__/base.cpython-312.pyc  | Bin 2185 -> 0 bytes
 .../__pycache__/inventory.cpython-312.pyc          | Bin 1190 -> 0 bytes
 .../__pycache__/orders.cpython-312.pyc             | Bin 2951 -> 0 bytes
 app/repositories/__pycache__/users.cpython-312.pyc | Bin 897 -> 0 bytes
 app/repositories/orders.py                         |   9 +-
 app/schemas/__pycache__/auth.cpython-312.pyc       | Bin 1553 -> 0 bytes
 app/schemas/__pycache__/common.cpython-312.pyc     | Bin 1221 -> 0 bytes
 app/schemas/__pycache__/inventory.cpython-312.pyc  | Bin 1529 -> 0 bytes
 app/schemas/__pycache__/orders.cpython-312.pyc     | Bin 2957 -> 0 bytes
 app/schemas/__pycache__/shipments.cpython-312.pyc  | Bin 555 -> 0 bytes
 app/schemas/auth.py                                |   8 +-
 app/services/__pycache__/auth.cpython-312.pyc      | Bin 2385 -> 0 bytes
 app/services/__pycache__/inventory.cpython-312.pyc | Bin 4252 -> 0 bytes
 app/services/__pycache__/orders.cpython-312.pyc    | Bin 5276 -> 0 bytes
 app/services/__pycache__/payments.cpython-312.pyc  | Bin 2506 -> 0 bytes
 app/services/__pycache__/shipping.cpython-312.pyc  | Bin 2714 -> 0 bytes
 app/services/auth.py                               |  10 +-
 app/services/inventory.py                          |   4 +
 app/services/orders.py                             |  76 ++++++++++---
 app/services/payments.py                           |   4 +-
 app/services/shipping.py                           |   4 +-
 app/tests/__pycache__/conftest.cpython-312.pyc     | Bin 3282 -> 0 bytes
 .../test_auth_and_orders.cpython-312.pyc           | Bin 2922 -> 0 bytes
 app/tests/__pycache__/test_health.cpython-312.pyc  | Bin 484 -> 0 bytes
 .../__pycache__/test_worker_flow.cpython-312.pyc   | Bin 2228 -> 0 bytes
 app/tests/conftest.py                              |  12 ++-
 app/tests/test_auth_and_orders.py                  |  11 +-
 app/tests/test_cancel_hardening.py                 | 106 +++++++++++++++++++
 app/tests/test_exception_hardening.py              |  26 +++++
 app/tests/test_hardening.py                        |  91 ++++++++++++++++
 app/tests/test_metrics_hardening.py                |  66 ++++++++++++
 app/tests/test_migrations_hardening.py             | 117 +++++++++++++++++++++
 app/tests/test_settings_hardening.py               |  20 ++++
 app/workers/__pycache__/celery_app.cpython-312.pyc | Bin 1571 -> 0 bytes
 app/workers/__pycache__/tasks.cpython-312.pyc      | Bin 8273 -> 0 bytes
 app/workers/tasks.py                               |  20 +++-
 docs/assets/part1.mp4                              |   3 -
 docs/assets/part2.mp4                              |   3 -
 docs/demo-video-explanation.md                     |   2 +
 docs/project-review.md                             |  26 +++++
 docs/recruiter-review.md                           |  39 -------
 pyproject.toml                                     |  20 ++++
 requirements-dev.txt                               |   8 ++
 requirements.txt                                   |   4 -
 scripts/seed_demo.py                               |   9 ++
 scripts/smoke_hardening.py                         |  92 ++++++++++++++++
 93 files changed, 988 insertions(+), 150 deletions(-)
dc54df4 test: verify legacy migrations and real HTTP hardening flows
6167ab1 fix(logging): record unhandled exceptions with request context
b343a83 fix(orders): compensate cancellation once and stop cancelled workflows
d206671 fix(metrics): protect production scraping and bound route labels
aee032e fix(config): reject weak secrets outside local environments
a386eeb fix(orders): count all customer orders for pagination
8b93577 fix(auth): normalize email before duplicate lookup
4516f02 fix(orders): scope idempotency and reject mismatched request replays
13f81cb fix(auth): ignore client supplied signup roles
f7134e5 ci: run Python quality checks and coverage on pushes and pull requests
d053d70 style: apply ruff formatting
f6611f7 chore: configure linting typing and development dependencies
3bdbddf updation
4e988fc chore: ignore generated Python and local environment files
```

The report itself is `docs/reports/phase-0-1-report.md`; its later documentation commit is intentionally outside the snapshot above.

## 7. **Deviations & assumptions**

- The first hygiene commit was made while tests were blocked by missing dependencies. This deviated from the requested green-before-every-commit order. After dependencies installed and the sandbox issue was identified, the baseline passed (4 tests). Later task suites passed; the secret-guard suite was still running when its commit command was issued and subsequently completed successfully with 23 tests.
- Windows sandbox networking hung TestClient in `socket._fallback_socketpair`. Tests and HTTP smoke were rerun with approved execution outside that sandbox; no application workaround was added.
- An external commit `3bdbddf` (`updation`) appeared during work and captured the documentation changes for 0.2/0.3. It was preserved; it is the non-Conventional-Commit exception in the history. No push was performed by this implementation.
- Git stores the MP4s as LFS pointers; the local checkout had downloaded files of 96,729,316 and 92,877,884 bytes. They were removed as explicitly requested, rather than claiming the local files were 133-byte placeholders or verified corrupt.
- No tracked database files existed. Generated local DBs remain ignored.
- Current source/config was searched for secret/password/token patterns. Only documented demo/default credentials were found; this is not an exhaustive secret audit of Git history.
- Removed unused TTL instead of implementing expiration; removed its setting, README entry and `.env.example` line. Local demo secret values are unchanged.
- The seed script had an admin and customer but no vendor. Added a vendor directly in the DB; no privileged signup path was retained. Test `auth_header` now creates privileged accounts directly and logs in normally.
- Existing test assertions were not weakened or removed. Existing `test_auth_and_orders.py` changes are import sorting/formatting only; fixture behavior changed as requested.
- Cancellation now compensates synchronously and emits no cancellation event. This keeps release/refund/audit/status in one transaction and avoids a redundant release event. Legacy `order.cancelled` events still call the guarded release helper.
- No row locks, outbox, consumer deduplication or retry/DLQ redesign were added. “Once” compensation is sequential; concurrent workers/cancellations still need later-phase controls.
- Preserved the existing `details: null` generic 500 body. The logging regression's initial expected `details: {}` was corrected before changing production code, then it failed on the missing log as intended.
- The first smoke run passed HTTP assertions but exited with a Windows temporary-file cleanup error. The script was corrected to run the server in a joined thread and dispose its engine; subsequent runs exited 0.
- Added Docker build exclusions and corrected `make seed` to module invocation, avoiding local environment artifacts in the image and ensuring repository imports resolve.
- Ruff exceptions are limited to framework dependency defaults, existing Generic syntax, assertions/demo credentials in tests/scripts and protocol/local-default false positives. Mypy is intentionally lenient for untyped functions, per the requested starting point; no blanket type ignores were introduced.
- The migration integration test and smoke script are additional verification, not claims of pre-fix failures for unchanged behavior.
- The Git-stat/log snapshot excludes this report's own commit so its contents remain reproducible.

## 8. **Newly discovered issues**

Not fixed because they require later-phase work:

- Database commit and event publication are not atomic; a publish failure can leave a created order without an event, and replay does not republish it.
- Event consumers lack deduplication; duplicate reservations/payments/shipments remain possible.
- Inventory and cancellation operations lack row locking; concurrent allocation, release and worker transitions can race. The cancelled-order guard observes state at event lookup and cannot stop a worker that already read an earlier state.
- Existing inventory reservation failure handling can commit partial item reservations before failing the order; the new boolean cannot represent partial reservations.
- Historical cancelled/failed orders can have ambiguous stock state; migration inference cannot safely reconstruct all old release outcomes.
- Exhausted task failures are persisted but automatic DLQ routing is incomplete. Worker monitoring is static; delayed shipment detection remains a stub.
- Original migration 0001 uses PostgreSQL UUID types, which SQLite reflects with NUMERIC affinity. Verification fixture binding handles this, but broader cross-dialect/schema-type cleanup remains separate.
- Signup duplicate checks remain susceptible to concurrent email inserts; the requested normalization fix does not redesign that race.
- Simulated providers, no multi-tenancy/frontend and public demo credentials remain documented project limitations.

## 9. **NOT VERIFIED list**

- PostgreSQL migrations/runtime behavior: Docker daemon was unavailable.
- Docker image build and full Compose startup.
- Real RabbitMQ/Redis transport, live Celery workers/scheduler and end-to-end fulfillment.
- Hosted GitHub Actions execution, branch protection and pre-commit installation/execution.
- Real simultaneous database races/load behavior: the requested insert-race test uses a deterministic lookup miss.
- Exhaustive secret absence across Git history, LFS objects and unrelated attached ZIP contents.
- Media playback/corruption of the removed locally downloaded videos.
- Ambiguous historical reservation reconciliation and production deployment.

## 10. **How I (the reviewer) can verify**

From the repository root on Windows PowerShell:
```powershell
git switch phase-0-1-hardening
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
mypy app
pytest -q --cov=app --cov-report=term-missing
python scripts/smoke_hardening.py
git diff --check main...HEAD
git diff --stat main...HEAD
git log --oneline main..HEAD
```

Fresh disposable SQLite DB:
```powershell
$env:DATABASE_URL='sqlite:///./review_migrations_fresh.db'
alembic upgrade head
alembic downgrade -1
alembic upgrade head
alembic downgrade 0001_initial
alembic upgrade head
Remove-Item Env:DATABASE_URL
```

When Docker is running, use the local port rather than the Compose-only hostname:
```powershell
docker compose up -d postgres
$env:DATABASE_URL='postgresql+psycopg://orders:orders@localhost:5432/orders'
alembic upgrade head
alembic downgrade -1
alembic upgrade head
Remove-Item Env:DATABASE_URL
```

Use a disposable database for downgrade checks. If multiple customers already reused a key, downgrade 0002 intentionally refuses until the conflicting data has been reconciled.

