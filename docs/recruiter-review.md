# Recruiter Review — Event-Driven Order Orchestration System

*A hiring-manager style review of this project, written to help recruiters and interviewers scan its engineering signal quickly.*

## Strengths

- Combines the full backend surface area in one project: API design, relational data modeling, message queues, background workers, idempotency, compensation logic, and observability — most portfolio projects only cover one or two of these.
- Realistic, verifiable business workflow: order intake → inventory reservation → payment authorization → shipment creation → failure recovery, matching how order platforms actually operate in production.
- Idempotency is implemented and demonstrably correct, not just claimed: sending the same request twice with the same `Idempotency-Key` returns the identical order, payment, and shipment record — no duplicate writes, no double charge.
- Failure handling is a first-class concern, not an afterthought: retries with backoff, dead-letter queues, and a persisted `failed_jobs` table mean failures are diagnosable instead of silent.
- The API and the fulfillment logic are properly decoupled — the API publishes an event and returns immediately; a Celery worker does the actual multi-step work. This is a correct, defensible use of asynchronous processing, not queues added for their own sake.
- Strong alignment for Python Backend Engineer, Backend API Engineer, and Platform Engineer roles.
- Demonstrates system design vocabulary (idempotency, compensation, dead-lettering, decoupled services) accurately, without overclaiming senior-level production ownership.
- Fully reproducible: the entire stack (API, worker, scheduler, PostgreSQL, Redis, RabbitMQ) starts with a single `docker compose up --build`, which lowers the bar for a reviewer to actually run and verify it.

## Weaknesses

- Payment and shipment providers are simulated, not integrated with a real gateway or carrier API. This is a reasonable and common portfolio scope decision, but candidates should be precise about this in interviews rather than implying real integrations.
- The system is a modular monolith with background workers, not independently deployed microservices. That's an appropriate architecture at this scale — candidates should be able to explain *why* this boundary was chosen over splitting into separate services.
- Event publication currently happens alongside the database write rather than through a transactional outbox, so there's a small theoretical window where a DB commit and an event publish could disagree. Worth being able to explain this trade-off if asked.

## Improvements Needed

- Add a transactional outbox pattern (or equivalent) to guarantee the order write and the event publish succeed or fail together.
- Add a provider adapter interface for payments/shipping so a real gateway could be swapped in without changing the workflow logic.
- Add a couple of richer failure scenarios (e.g., payment succeeds but shipment creation fails) with visible compensation in the demo, to make the recovery path as obvious as the happy path.
- Add OpenAPI/Swagger screenshots, a RabbitMQ queue screenshot, and a short test-output screenshot to the README so recruiters can scan the evidence without cloning the repo.

## What the Demo Proves

The recorded demo shows, in order: the full stack starting from Docker, database migrations and seed data, the Swagger API surface, JWT login and authorization, a real order being created and fully processed (inventory → payment → shipment) in one response, the same request being safely retried via idempotency, and the RabbitMQ queue plus live Celery worker logs processing the event — giving a reviewer direct, visual evidence of the architecture working end to end, rather than just a claim in a README.

## Recruiter Impression Score

**9.2 / 10** — strong, realistic, and runnable; the kind of project that survives a "walk me through this" interview question.

## Backend Engineer Interview Readiness Score

**8.7 / 10** — candidate should be ready to speak precisely about the outbox trade-off, the monolith-vs-microservices decision, and where the simulated boundaries are, since those are the natural follow-up questions this project invites.