# Async Payment Service

Асинхронная обработка платежей на FastAPI, PostgreSQL и RabbitMQ. Проект реализует Transactional Outbox, идемпотентное создание платежа, заменяемый платёжный шлюз, webhook-уведомления, повторные попытки и Dead Letter Queue.

- [Исходные требования](docs/requirements.md)
- [Архитектурная спецификация](docs/spec.md)

## Стек

- Python 3.14, совместимость с Python 3.13+;
- FastAPI и Pydantic v2;
- PostgreSQL 17, SQLAlchemy 2.0 async и Alembic;
- RabbitMQ 4 и FastStream;
- Docker Compose, Make и uv;
- pytest, Ruff и GitHub Actions.

## Быстрый запуск

Нужны Docker Desktop и uv. Python устанавливать отдельно не обязательно: uv использует версию из `.python-version`.

```bash
make setup
make up
```

`make setup` создаёт `.env` из `.env.example` и устанавливает locked-зависимости. `make up` применяет миграции, запускает PostgreSQL, RabbitMQ, API и consumer, затем ждёт healthchecks.

Доступные интерфейсы:

- API: <http://localhost:18100>;
- OpenAPI: <http://localhost:18100/docs>;
- RabbitMQ Management: <http://localhost:15673>, локальные `guest / guest`.

Проверить весь контур одной командой:

```bash
make smoke
```

Smoke сам запускает временный локальный webhook-приёмник на свободном порту. Отдельно запускать `make webhook` для него не нужно.

## Как устроена обработка

1. `POST /api/v1/payments` создаёт `Payment` и `OutboxEvent` в одной транзакции PostgreSQL и возвращает `202 Accepted`.
2. Outbox relay выбирает неопубликованные записи с `FOR UPDATE SKIP LOCKED` и отправляет persistent-сообщения в RabbitMQ с publisher confirmation.
3. Только после подтверждения брокера relay выставляет `published_at` и фиксирует транзакцию.
4. Consumer получает `PaymentCreatedEvent`, вызывает порт `PaymentGateway` и сохраняет финальный статус и `processed_at`.
5. Consumer отправляет Pydantic-DTO `PaymentWebhookPayload` на `webhook_url`.
6. При инфраструктурной ошибке выполняются три попытки с паузами 1 и 2 секунды. После последней ошибки сообщение получает `nack(requeue=False)` и попадает в `payments.dead`.

Бизнес-отказ шлюза со статусом `failed` — финальное состояние, а не повод для retry. Его webhook отправляется так же, как webhook успешного платежа.

### Гарантии доставки

Семантика — **at least once**, не exactly-once. Падение после публикации в RabbitMQ, но до фиксации `published_at`, может привести к повторной доставке. Consumer не вызывает шлюз повторно, если финальный статус уже сохранён; получатель webhook также должен быть готов к дубликатам.

Автоматический replay из DLQ намеренно не реализован: сообщения требуют ручной диагностики.

## Структура и слои

```text
app/
├── api.py                    # HTTP router и response DTO
├── services/
│   ├── payments.py           # Создание, идемпотентность и транзакция
│   └── processing.py         # Асинхронная обработка платежа
├── repositories/
│   ├── payments.py           # Запросы и сохранение платежей
│   └── outbox.py             # Outbox-записи и выборка неопубликованных
├── gateways/
│   ├── contracts.py          # PaymentGateway Protocol и command/result DTO
│   └── mock.py               # Mock-адаптер с задержкой и 90/10 outcome
├── outbox.py                 # Relay и декларация RabbitMQ-топологии
├── consumer.py               # RabbitMQ consumer, retry и DLQ
├── webhooks.py               # HTTP-отправка webhook DTO
└── composition.py            # Выбор реализации PaymentGateway
```

`AsyncSession` выполняет роль Unit of Work. Отдельные generic repository и UnitOfWork-классы не добавлены: они не дадут здесь новой функциональности.

Pydantic используется на внешних границах: HTTP API, RabbitMQ event, webhook payload и контракт платёжного шлюза. ORM-модели остаются внутри persistence-слоя.

### Замена mock-шлюза

Обработчик зависит только от `PaymentGateway` Protocol:

- вход — `PaymentChargeCommand`, включая `idempotency_key`;
- результат — `PaymentChargeResult` с финальным статусом, `provider_payment_id` и `failure_reason`;
- адаптер выбирается в `app/composition.py` через `PAYMENT_GATEWAY_BACKEND=mock`.

Для реального провайдера добавляется отдельный `HttpPaymentGateway` с собственными provider-specific DTO, HTTP-клиентом и mapping ошибок. Провайдер обязан поддерживать идемпотентное списание или поиск предыдущей операции: иначе падение между успешным списанием и коммитом БД может привести к повторному списанию.

## HTTP API

Все endpoint, включая `/health`, требуют заголовок `X-API-Key`. Создание платежа дополнительно требует `Idempotency-Key`.

### Создать платёж

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

Ответ `202 Accepted`:

```json
{
  "payment_id": "UUIDv7",
  "status": "pending",
  "created_at": "2026-11-12T10:00:00Z"
}
```

Повтор с тем же ключом и полностью совпадающим телом возвращает исходный платёж без новой записи в Outbox. Тот же ключ с другим телом возвращает `409 Conflict`.

### Получить платёж

```bash
curl http://localhost:18100/api/v1/payments/<payment_id> \
  -H 'X-API-Key: dev-secret-key'
```

Возвращаются сумма, валюта, описание, metadata, текущий статус, ключ идемпотентности, `created_at` и `processed_at`.

## Локальный webhook и DLQ

Для ручных запросов можно запустить приёмник:

```bash
make webhook
```

Он слушает порт `9001` и печатает каждое полученное тело. Внутри Docker-контейнера адрес приёмника — `http://host.docker.internal:9001/webhook`.

Проверить очереди:

```bash
make queues
```

Основная очередь — `payments.new`, DLQ — `payments.dead`. Содержимое DLQ можно посмотреть через RabbitMQ Management.

## Тесты и качество

```bash
make ready       # formatting check + Ruff + все unit-тесты
make test        # pytest; make test ARGS="-k idempotent"
make lint        # проверки без изменения файлов
make format      # автоформатирование и безопасные autofix
make smoke       # реальный PostgreSQL/RabbitMQ/API/consumer/webhook/DLQ
```

Unit-тесты используют SQLite и проверяют API, идемпотентность, атомарность Outbox, ошибку публикации, контракты шлюза, webhook DTO, отсутствие повторного списания и финальный `nack` после трёх попыток.

Smoke проверяет реальный контур:

- успешное создание и финальный статус;
- идемпотентный повтор и `409` на изменённый payload;
- опубликованный Outbox event;
- доставленный webhook с совпадающим payload;
- недоступный webhook и сообщение с нужным `payment_id` в `payments.dead`.

## GitHub Actions

Workflow `.github/workflows/ci.yml` запускается на `push` в `main`, `pull_request` и вручную:

1. **Quality** — `uv sync --locked`, проверка форматирования, Ruff и unit-тесты на Python 3.14.
2. **Integration** — сборка образов, запуск Compose, `make smoke`, логи при ошибке и гарантированная очистка контейнеров.

CI использует только локальные dev-credentials и не требует внешних сервисов или секретов.

## Конфигурация

Основные переменные в `.env`:

| Переменная | Значение по умолчанию | Назначение |
|---|---|---|
| `API_KEY` | `dev-secret-key` | Ключ локального API |
| `PAYMENT_GATEWAY_BACKEND` | `mock` | Реализация шлюза |
| `OUTBOX_POLL_INTERVAL` | `0.5` | Пауза между проходами relay, сек. |
| `OUTBOX_BATCH_SIZE` | `100` | Размер batch Outbox |
| `GATEWAY_MIN_DELAY` / `GATEWAY_MAX_DELAY` | `2` / `5` | Задержка mock-шлюза, сек. |
| `WEBHOOK_TIMEOUT` | `5` | Timeout webhook, сек. |
| `RETRY_BASE_DELAY` | `1` | Базовая задержка retry, сек. |
| `MAX_ATTEMPTS` | `3` | Число попыток consumer |
| `API_PORT` | `18100` | Внешний порт API |
| `POSTGRES_PORT` | `15432` | Внешний порт PostgreSQL |
| `RABBITMQ_PORT` | `5673` | Внешний порт AMQP |
| `RABBITMQ_MANAGEMENT_PORT` | `15673` | Порт RabbitMQ Management |
| `WEBHOOK_PORT` | `9001` | Порт ручного webhook-приёмника |

Значения из `.env.example` предназначены только для локальной разработки.

## Полезные команды

```bash
make ps                    # статус контейнеров
make logs                  # все логи
make logs SERVICE=consumer # только consumer
make migrate               # применить миграции
make psql                  # psql внутри PostgreSQL-контейнера
make rebuild               # пересобрать и перезапустить стек
make down                  # остановить контейнеры, сохранить volumes
make clean                 # остановить контейнеры и удалить локальные volumes
```
