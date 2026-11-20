# Engineering self-review

## Strengths

- FastAPI schemas separate API contracts from SQLAlchemy persistence.
- Modular services demonstrate inventory, simulated payment and shipment orchestration.
- JWT role checks, audit records, migrations and automated tests provide reviewable examples.
- Order request idempotency is scoped to the customer: matching payloads replay the current order, while changed payloads return 409. Currency is uppercased and items are sorted for request hashing; keys do not expire.
- Compose describes an API, Celery workers, scheduler, PostgreSQL, Redis and RabbitMQ stack.

## Known limitations

- Payment and shipping providers and pricing are simulated.
- No transactional outbox: a database commit can succeed while event publication fails.
- Event consumers are not yet idempotent; retries can duplicate side effects.
- Inventory allocation has no row locking and can race across workers.
- This is a modular monolith, not independently deployed microservices.
- It is not multi-tenant and has no frontend.
- The workers endpoint reports static configuration, not live worker health.
- Delayed shipment detection is a placeholder. Exhausted retries are recorded, but automatic DLQ routing is incomplete.
- Demo video is pending. Deployment and operational claims require independent verification.

## Next steps

Consider an outbox, consumer deduplication, concurrency controls and real provider adapters in later phases.
See [the hardening report](reports/phase-0-1-report.md) for implemented changes and verification limits.
