# Engineering self-review

## Strengths

- FastAPI schemas separate API contracts from SQLAlchemy persistence.
- Modular services demonstrate inventory, simulated payment and shipment orchestration.
- JWT role checks, audit records, migrations and automated tests provide reviewable examples.
- Order request idempotency is scoped to the customer: matching payloads replay the current order, while changed payloads return 409. Currency is uppercased and items are sorted for request hashing; keys do not expire.
- Compose describes an API, Celery workers, scheduler, PostgreSQL, Redis and RabbitMQ stack.

## Known limitations

- Payment and shipping providers and pricing are simulated.
- The transactional outbox retains delivery intents through broker outages. Delivery is at-least-once; processed-event markers and state guards protect database effects.
- Processed-event markers have no retention policy yet, and manual replay does not resurrect terminal failed sagas.
- PostgreSQL order-first and sorted inventory row locks protect allocation; SQLite does not provide equivalent concurrent-writer guarantees.
- This is a modular monolith, not independently deployed microservices.
- It is not multi-tenant and has no frontend.
- The workers endpoint reports static configuration, not live worker health.
- Delayed shipment detection is a placeholder. Exhausted retries are recorded, but automatic DLQ routing is incomplete.
- Demo video is pending. Deployment and operational claims require independent verification.

## Next steps

Consider operational replay workflows, marker retention and real provider adapters in later phases.
See [the reliability architecture and verification report](reports/phase-2-report.md) for the current design and evidence. The [Phase 0-1 report](reports/phase-0-1-report.md) remains historical.
