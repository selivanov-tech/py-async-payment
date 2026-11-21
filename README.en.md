# Async Payment Service

**Language:** [Русский](README.ru.md) · English · [Language picker](README.md)

Asynchronous payment processing with FastAPI, PostgreSQL and RabbitMQ. The project implements the Transactional Outbox, idempotent payment creation, a replaceable payment gateway, webhook notifications, retries and a Dead Letter Queue.

- [Original requirements](docs/en/requirements.md)
- [Architecture specification](docs/en/spec.md)

## Stack

- Python 3.14;
- FastAPI and Pydantic v2;
- PostgreSQL 17, SQLAlchemy 2.0 async and Alembic;
- RabbitMQ 4 and FastStream;
- Docker Compose, Make and uv;
- pytest, Ruff and GitHub Actions.

## Quick start

You need Docker Desktop and uv. You do not have to install Python separately: uv uses the version from `.python-version`.

```bash
make setup
make up
```

`make setup` creates `.env` from `.env.example` and installs the locked dependencies. `make up` applies migrations, starts PostgreSQL, RabbitMQ, the API and the consumer, then waits for the healthchecks.

Available interfaces:

- API: <http://localhost:18100>;
- OpenAPI: <http://localhost:18100/docs>;
- RabbitMQ Management: <http://localhost:15673>, local credentials `guest / guest`.

Check the whole flow with one command:

```bash
make smoke
```

The smoke test starts a temporary local webhook receiver on a free port by itself. You do not need to run `make webhook` for it.

## How processing works

1. `POST /api/v1/payments` creates a `Payment` and an `OutboxEvent` in **one** PostgreSQL transaction and returns `202 Accepted`.
2. The outbox relay selects unpublished records with `FOR UPDATE SKIP LOCKED` and sends persistent messages to RabbitMQ with publisher confirmation.
3. Only after the broker confirms, the relay sets `published_at` and commits the transaction.
4. The consumer receives `PaymentCreatedEvent`, calls the `PaymentGateway` port and saves the final status and `processed_at`.
5. The consumer sends the Pydantic DTO `PaymentWebhookPayload` to `webhook_url`.
6. On an infrastructure error the consumer makes three attempts, with pauses of 1 and 2 seconds. After the last failure the message gets `nack(requeue=False)` and goes to `payments.dead`.

A business refusal from the gateway with status `failed` is a final state, not a reason to retry. Its webhook is sent in the same way as the webhook of a successful payment.

### Delivery guarantees

The semantics are **at least once**, not exactly-once. A crash after publishing to RabbitMQ but before `published_at` is committed can cause a repeated delivery. The consumer does not call the gateway again if the final status is already saved; the webhook receiver must also be ready for duplicates.

Automatic replay from the DLQ is intentionally not implemented: these messages need manual diagnosis.

## Structure and layers

```text
app/
├── api.py                    # HTTP router and response DTOs
├── services/
│   ├── payments.py           # Creation, idempotency and the transaction
│   └── processing.py         # Asynchronous payment processing
├── repositories/
│   ├── payments.py           # Payment queries and persistence
│   └── outbox.py             # Outbox records and selecting unpublished ones
├── gateways/
│   ├── contracts.py          # PaymentGateway Protocol and command/result DTOs
│   └── mock.py               # Mock adapter with a delay and a 90/10 outcome
├── outbox.py                 # Relay and RabbitMQ topology declaration
├── consumer.py               # RabbitMQ consumer, retry and DLQ
├── webhooks.py               # HTTP delivery of the webhook DTO
└── composition.py            # Choice of the PaymentGateway implementation
```

`AsyncSession` plays the role of the Unit of Work. Separate generic repository and UnitOfWork classes are not added: they would give no new functionality here.

Pydantic is used at the external boundaries: the HTTP API, the RabbitMQ event, the webhook payload and the payment gateway contract. ORM models stay inside the persistence layer.

### Replacing the mock gateway

The handler depends only on the `PaymentGateway` Protocol:

- input is `PaymentChargeCommand`, which includes `idempotency_key`;
- the result is `PaymentChargeResult` with the final status, `provider_payment_id` and `failure_reason`;
- the adapter is chosen in `app/composition.py` through `PAYMENT_GATEWAY_BACKEND=mock`.

For a real provider you add a separate `HttpPaymentGateway` with its own provider-specific DTOs, an HTTP client and error mapping. The provider must support an idempotent charge or a lookup of the previous operation. Otherwise a crash between a successful charge and the database commit can cause a double charge.

## HTTP API

All endpoints, including `/health`, require the `X-API-Key` header. Creating a payment also requires `Idempotency-Key`.

### Create a payment

```bash
curl -X POST http://localhost:18100/api/v1/payments \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: dev-secret-key' \
  -H 'Idempotency-Key: order-42' \
  -d '{
    "amount": "1499.90",
    "currency": "RUB",
    "description": "Order 42",
    "metadata": {"order_id": 42},
    "webhook_url": "http://host.docker.internal:9001/webhook"
  }'
```

Response `202 Accepted`:

```json
{
  "payment_id": "UUIDv7",
  "status": "pending",
  "created_at": "2026-11-12T10:00:00Z"
}
```

A repeat request with the same key and exactly the same body returns the original payment and creates no new Outbox record. The same key with a different body returns `409 Conflict`.

### Get a payment

```bash
curl http://localhost:18100/api/v1/payments/<payment_id> \
  -H 'X-API-Key: dev-secret-key'
```

The response contains the amount, currency, description, metadata, current status, idempotency key, `created_at` and `processed_at`.

## Local webhook and DLQ

For manual requests you can start a receiver:

```bash
make webhook
```

It listens on port `9001` and prints every body it receives. Inside a Docker container the receiver address is `http://host.docker.internal:9001/webhook`.

Check the queues:

```bash
make queues
```

The main queue is `payments.new`, the DLQ is `payments.dead`. You can look at the DLQ content in RabbitMQ Management.

## Tests and quality

```bash
make ready       # formatting check + Ruff + all unit tests
make test        # pytest; make test ARGS="-k idempotent"
make lint        # checks without changing files
make format      # auto-formatting and safe autofixes
make smoke       # real PostgreSQL/RabbitMQ/API/consumer/webhook/DLQ
```

The unit tests use SQLite and cover the API, idempotency, Outbox atomicity, a publish failure, gateway contracts, the webhook DTO, no repeated charge, and the final `nack` after three attempts.

The smoke test checks the real flow:

- successful creation and the final status;
- an idempotent repeat and `409` for a changed payload;
- a published Outbox event;
- a delivered webhook with a matching payload;
- an unreachable webhook, and a message with the right `payment_id` in `payments.dead`.

## GitHub Actions

The workflow `.github/workflows/ci.yml` runs on `push` to `main`, on `pull_request` and manually:

1. **Quality** — `uv sync --locked`, formatting check, Ruff and unit tests on Python 3.14.
2. **Integration** — image build, Compose start, `make smoke`, logs on failure and guaranteed container cleanup.

CI uses only local dev credentials and needs no external services or secrets.

## Configuration

Main variables in `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `API_KEY` | `dev-secret-key` | Local API key |
| `PAYMENT_GATEWAY_BACKEND` | `mock` | Gateway implementation |
| `OUTBOX_POLL_INTERVAL` | `0.5` | Pause between relay passes, sec. |
| `OUTBOX_BATCH_SIZE` | `100` | Outbox batch size |
| `GATEWAY_MIN_DELAY` / `GATEWAY_MAX_DELAY` | `2` / `5` | Mock gateway delay, sec. |
| `WEBHOOK_TIMEOUT` | `5` | Webhook timeout, sec. |
| `RETRY_BASE_DELAY` | `1` | Base retry delay, sec. |
| `MAX_ATTEMPTS` | `3` | Number of consumer attempts |
| `API_PORT` | `18100` | External API port |
| `POSTGRES_PORT` | `15432` | External PostgreSQL port |
| `RABBITMQ_PORT` | `5673` | External AMQP port |
| `RABBITMQ_MANAGEMENT_PORT` | `15673` | RabbitMQ Management port |
| `WEBHOOK_PORT` | `9001` | Port of the manual webhook receiver |

The values from `.env.example` are meant for local development only.

## Useful commands

```bash
make ps                    # container status
make logs                  # all logs
make logs SERVICE=consumer # consumer only
make migrate               # apply migrations
make psql                  # psql inside the PostgreSQL container
make rebuild               # rebuild and restart the stack
make down                  # stop containers, keep volumes
make clean                 # stop containers and delete local volumes
```
