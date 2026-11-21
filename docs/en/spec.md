# Asynchronous payment service specification

**Language:** [Русский](../ru/spec.md) · English · [README](../../README.en.md)

## 1. Purpose

The service accepts a payment over HTTP, atomically saves it and an event in PostgreSQL, publishes the event asynchronously through the Transactional Outbox, processes the payment through the payment gateway port and sends a webhook to the client.

The implementation uses FastAPI, Pydantic v2, SQLAlchemy 2.0 async, PostgreSQL, RabbitMQ with FastStream, Alembic and Docker Compose.

## 2. HTTP contract

All HTTP endpoints require the `X-API-Key` header. A wrong or missing key returns `401`.

### `POST /api/v1/payments`

The required header `Idempotency-Key`: a non-empty string up to 255 characters.

Body:

- `amount`: decimal, greater than zero, at most two digits after the decimal point;
- `currency`: `RUB`, `USD` or `EUR`;
- `description`: a string of 1–500 characters;
- `metadata`: a JSON object, `{}` by default;
- `webhook_url`: an HTTP or HTTPS URL.

A successful response is `202 Accepted` with `payment_id`, `status=pending` and `created_at`.

A repeat request with the same key and exactly the same body returns the existing payment, with no new record in `payments` or `outbox`. A repeated key with a different body returns `409 Conflict`.

### `GET /api/v1/payments/{payment_id}`

Returns full information about the payment. A UUID that does not exist returns `404`.

### `GET /health`

Returns `{"status": "ok"}` and is also protected by the API key.

## 3. Data model

Payment and Outbox event identifiers are UUIDv7. Time is stored in UTC.

### `payments`

- `id`, UUID, primary key;
- `amount`, `Numeric(18, 2)`;
- `currency`, a string with a CHECK constraint;
- `description`, a string up to 500;
- `metadata`, JSONB;
- `status`: `pending`, `succeeded` or `failed`;
- `idempotency_key`, a unique string;
- `webhook_url`, a string;
- `created_at`, `processed_at`.

### `outbox`

- `id`, UUID, primary key;
- `payment_id`, a foreign key to `payments.id`;
- `event_type`, for example `payment.created`;
- `payload`, JSONB;
- `created_at`, `published_at`.

A partial index on `created_at` for unpublished events speeds up polling.

## 4. Transactional Outbox and RabbitMQ

The API does not publish to RabbitMQ directly. The payment and the Outbox event are created in one transaction.

The outbox relay runs inside the consumer process:

1. It selects a batch of unpublished events with `FOR UPDATE SKIP LOCKED`.
2. It publishes a persistent message with `mandatory=True` and waits for the broker's confirmation.
3. Only after the confirmation it sets `published_at` and commits the transaction.
4. On an error it rolls the marks back and tries again on the next pass.

Topology:

- a durable direct exchange `payments`;
- a durable queue `payments.new`;
- a dead-letter exchange `payments.dlx`;
- a durable queue `payments.dead`.

The semantics are **at least once**. A crash after publishing but before `published_at` is committed can create a repeated delivery. The consumer must tolerate it, and the webhook receiver must accept repeated notifications.

## 5. Payment gateway port

`PaymentGateway` is a Protocol with the asynchronous operation `charge`. The handler depends only on the protocol.

The input DTO `PaymentChargeCommand` contains:

- `payment_id`;
- `amount`, `currency`;
- `idempotency_key`;
- `description`.

The result `PaymentChargeResult` contains the internal status, an optional `provider_payment_id` and `failure_reason`.

`MockPaymentGateway` is a separate adapter: it waits 2–5 seconds and returns `succeeded` in 90% of cases and `failed` in 10%. The composition root chooses the adapter through `PAYMENT_GATEWAY_BACKEND=mock`.

A future `HttpPaymentGateway` must encapsulate the HTTP client, provider-specific Pydantic DTOs, error mapping and the provider's idempotency. Checking the final status in the database does not protect against the window "the provider accepted the payment, the service crashed before the commit". So a real integration needs an idempotent charge or a repeated lookup of the operation at the provider.

## 6. Consumer and webhook

The consumer receives the Pydantic event `PaymentCreatedEvent` from `payments.new`.

For a `pending` payment it calls the gateway port and saves the result and `processed_at`. If the payment is already final, the gateway is not called again. This matters most for a retry after a webhook error.

`PaymentWebhookPayload` is a separate Pydantic DTO. `WebhookSender` takes the DTO, not the ORM model, and sends `model_dump(mode="json")` through HTTPX. Only an HTTP 2xx response counts as success.

On exceptions from the database, the gateway or the webhook, at most three attempts are made. The pauses between them are `RETRY_BASE_DELAY` and `2 * RETRY_BASE_DELAY`. The business result `failed` is a final payment status, not an infrastructure error: its webhook is sent as usual.

After the third failure the consumer does `nack(requeue=False)`. RabbitMQ moves the message through the DLX to `payments.dead`. There is no automatic replay from the DLQ.

## 7. Startup and configuration

Docker Compose starts PostgreSQL, RabbitMQ, the API and one consumer. The API applies migrations before it starts; the consumer waits for a healthy API and RabbitMQ.

The Makefile has common commands `up`, `down`, `build`, `rebuild`, `ps`, `logs`, `queues`, `lint`, `format`, `test`, `ready`, `migrate`, `smoke` and a local webhook receiver.

Default ports and credentials are meant for local development only. Ports can be overridden in `.env` to avoid conflicts with other projects.

## 8. Checks

### Unit

- creating and getting a payment;
- authentication and validation;
- an idempotent repeat and a payload conflict;
- atomic write of the payment and the Outbox record;
- a publish error leaves the event unpublished;
- a retry does not call the gateway again after the final status;
- exactly three attempts, the delays and the final `NackMessage(requeue=False)`;
- the webhook is serialized from a Pydantic DTO.

### Smoke

`make smoke` against a running Compose stack:

- creates a payment with a working local webhook;
- checks the final status, `processed_at`, `published_at` and the webhook body;
- checks an idempotent repeat and `409` for a different payload;
- creates a payment with an unreachable webhook;
- checks that a message with the same `payment_id` is in `payments.dead`.

### GitHub Actions

CI runs on `push` and `pull_request`:

1. quality job: locked dependencies, formatting, Ruff, unit tests;
2. integration job: image build, Compose, smoke and a guaranteed stack shutdown.

## 9. Out of scope

- A real payment provider.
- Exactly-once delivery.
- Automatic DLQ replay.
- Deployment to an unspecified environment.
