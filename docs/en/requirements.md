# Take-home assignment: asynchronous payment processing service

**Language:** [Русский](../ru/requirements.md) · English · [README](../../README.en.md)

> This is an English translation of the original assignment, which was given in Russian. The Russian text is the source of truth.

> Thank you for your interest in this assignment! Please note that it is a skills check and is not paid.

## Description

Implement a microservice for asynchronous payment processing. The service accepts payment requests, processes them through an external payment gateway (an emulation) and notifies the client about the result by webhook.

## Entities

### Payment

- Payment ID — a unique identifier.
- Amount — decimal.
- Currency — RUB, USD, EUR.
- Description — a string.
- Metadata — a JSON field for extra information.
- Status — `pending`, `succeeded`, `failed`.
- Idempotency key — a unique key that protects against duplicates.
- Webhook URL — the URL for the result notification.
- Creation and processing dates.

## API functionality

### 1. Create a payment

`POST /api/v1/payments`

- Header: `Idempotency-Key` (required).
- Body: amount, currency, description, metadata, `webhook_url`.
- Response: `202 Accepted`, `payment_id`, status, `created_at`.

### 2. Get payment information

`GET /api/v1/payments/{payment_id}`

Response: detailed information about the payment.

## Technical requirements

### Message broker

- When a payment is created, an event is published to the `payments.new` queue.
- One consumer that:
  - receives the message from the queue;
  - emulates payment processing (2–5 seconds, 90% success, 10% failure);
  - updates the status in the database;
  - sends a webhook notification to the given URL;
  - retries when sending fails.

### Delivery guarantees

- The Outbox pattern for guaranteed event publishing.
- An idempotency key as protection against duplicates.
- A Dead Letter Queue for messages that were not processed after three attempts.

### Authentication

- A static API key in the `X-API-Key` header for all endpoints.

## Technology stack

- FastAPI + Pydantic v2.
- SQLAlchemy 2.0 (async mode).
- PostgreSQL.
- RabbitMQ (FastStream).
- Alembic (migrations).
- Docker + docker-compose.

## Deliverable requirements

1. Models and migrations: the `payments` and `outbox` tables.
2. API endpoints: create and get a payment.
3. Consumer: one handler that does everything.
4. Outbox pattern: guaranteed event delivery.
5. Retry: three attempts with exponential delay.
6. Dead Letter Queue: for messages that failed for good.
7. Docker: a Compose file with PostgreSQL, RabbitMQ, the API and the consumer.
8. Documentation: a README with startup instructions and examples.

## Evaluation criteria

- Architecture and code cleanliness.
- Correct implementation of the Outbox pattern.
- Work with RabbitMQ (queues, exchanges, DLQ).
- Idempotency.
- Error handling and retry logic.
- A working Docker environment.
