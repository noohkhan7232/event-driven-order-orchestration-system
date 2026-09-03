# Demo Video Explanation

This document explains, step by step, what is shown in the demo video for the **Event-Driven Order Orchestration System**.

---

## 1. Starting the project

The video begins by starting the entire backend using a single command:

```bash
docker compose up --build
```

This one command starts everything the project needs to run — the API server, the background workers, the scheduler, the PostgreSQL database, Redis, and RabbitMQ — all together, in separate containers.

## 2. Setting up the database

Next, the database tables are created by running a migration command:

```bash
docker compose exec api alembic upgrade head
```

This builds all the required tables (users, orders, inventory, payments, shipments, etc.) inside PostgreSQL.

Then, some demo data (a sample user, sample products, and sample warehouse stock) is added using a seed script, so there is real data to test with:

```bash
docker compose exec api python -m scripts.seed_demo
```

## 3. Exploring the API (Swagger UI)

The video opens the interactive API documentation at:

```
http://localhost:8000/docs
```

This page lists every available API endpoint (login, signup, orders, inventory, payments, shipments) and lets you test them directly from the browser, without needing any separate tool like Postman.

## 4. Logging in

Using the demo account created by the seed script, the video logs in through the `/auth/login` endpoint. This returns a **JWT access token** — a secure key that proves who the user is.

That token is then pasted into Swagger's **Authorize** button, which unlocks all the protected endpoints (the ones that require a logged-in user).

## 5. Creating an order

The video then creates a new order using the `/orders` endpoint, sending:

- The product (SKU) and quantity being ordered
- A special header called `Idempotency-Key`

The response comes back immediately, showing:

- The order was created
- Payment was automatically authorized
- A shipment was automatically created with a tracking number

All of this happens without the user waiting — the heavy work runs in the background.

## 6. Proving duplicate orders can't happen (Idempotency)

The exact same request is sent a second time, using the exact same `Idempotency-Key`.

Instead of creating a second order, the system returns the **same order, same payment, and same shipment** as before. This proves that accidental duplicate requests (like a user double-clicking "Place Order," or a network retry) will never create duplicate orders or duplicate charges.

## 7. Seeing the background processing happen live

To prove that the order isn't processed instantly inside the API, but instead handled by a separate background system, the video shows two things side by side:

- **RabbitMQ dashboard** (`http://localhost:15672`) — showing the message queues (`orders`, `inventory`, `payments`, `shipments`) where events are sent
- **Celery worker logs** — showing, in real time, the background worker picking up the order and processing each step (reserving inventory, authorizing payment, creating the shipment)

This demonstrates the core idea of the project: the API and the actual order-processing work are two separate, independent systems connected through a message queue — not one single function doing everything at once.

---

## Summary

In short, the video shows:

1. The whole backend starting with one Docker command
2. The database being set up with real demo data
3. The full API being explored through Swagger
4. Logging in and getting a secure access token
5. Creating a real order and watching it get processed automatically
6. Proving duplicate requests are handled safely (idempotency)
7. Watching the background queue and worker process the order live
