# Спецификация асинхронного сервиса платежей

## 1. Назначение

Сервис принимает платёж по HTTP, атомарно сохраняет его и событие в PostgreSQL, асинхронно публикует событие через Transactional Outbox, обрабатывает платёж через порт платёжного шлюза и отправляет клиенту webhook.

Реализация использует FastAPI, Pydantic v2, SQLAlchemy 2.0 async, PostgreSQL, RabbitMQ с FastStream, Alembic и Docker Compose.

## 2. HTTP-контракт

Все HTTP-endpoint'ы требуют заголовок `X-API-Key`. Неверный или отсутствующий ключ возвращает `401`.

### `POST /api/v1/payments`

Обязательный заголовок `Idempotency-Key`: непустая строка длиной до 255 символов.

Тело:

- `amount`: decimal, больше нуля, не более двух знаков после запятой;
- `currency`: `RUB`, `USD` или `EUR`;
- `description`: строка длиной 1–500;
- `metadata`: JSON-объект, по умолчанию `{}`;
- `webhook_url`: HTTP или HTTPS URL.

Успешный ответ — `202 Accepted` с `payment_id`, `status=pending` и `created_at`.

Повтор с тем же ключом и полностью совпадающим телом возвращает существующий платёж без новой записи в `payments` или `outbox`. Повтор ключа с другим телом возвращает `409 Conflict`.

### `GET /api/v1/payments/{payment_id}`

Возвращает полную информацию о платеже. Несуществующий UUID возвращает `404`.

### `GET /health`

Возвращает `{"status": "ok"}` и тоже защищён API-ключом.

## 3. Модель данных

Идентификаторы платежей и Outbox-событий — UUIDv7. Время хранится в UTC.

### `payments`

- `id`, UUID, primary key;
- `amount`, `Numeric(18, 2)`;
- `currency`, строка с CHECK-ограничением;
- `description`, строка до 500;
- `metadata`, JSONB;
- `status`: `pending`, `succeeded` или `failed`;
- `idempotency_key`, уникальная строка;
- `webhook_url`, строка;
- `created_at`, `processed_at`.

### `outbox`

- `id`, UUID, primary key;
- `payment_id`, внешний ключ на `payments.id`;
- `event_type`, например `payment.created`;
- `payload`, JSONB;
- `created_at`, `published_at`.

Частичный индекс по `created_at` для неопубликованных событий ускоряет polling.

## 4. Transactional Outbox и RabbitMQ

API не публикует в RabbitMQ напрямую. Платёж и Outbox-событие создаются в одной транзакции.

Outbox relay работает внутри процесса consumer:

1. Выбирает пакет неопубликованных событий с `FOR UPDATE SKIP LOCKED`.
2. Публикует persistent-сообщение с `mandatory=True` и ждёт подтверждения брокера.
3. Только после подтверждения выставляет `published_at` и фиксирует транзакцию.
4. При ошибке откатывает отметки и повторяет попытку на следующем проходе.

Топология:

- durable direct exchange `payments`;
- durable queue `payments.new`;
- dead-letter exchange `payments.dlx`;
- durable queue `payments.dead`.

Семантика — **at least once**. Падение после публикации, но до фиксации `published_at`, может создать повторную доставку. Consumer обязан её переносить, а получатель webhook — допускать повторные уведомления.

## 5. Порт платёжного шлюза

`PaymentGateway` — Protocol с асинхронной операцией `charge`. Обработчик зависит только от протокола.

Входная DTO `PaymentChargeCommand` содержит:

- `payment_id`;
- `amount`, `currency`;
- `idempotency_key`;
- `description`.

Результат `PaymentChargeResult` содержит внутренний статус, необязательный `provider_payment_id` и `failure_reason`.

`MockPaymentGateway` — отдельный адаптер: ждёт 2–5 секунд и возвращает `succeeded` в 90% случаев, `failed` в 10%. Composition root выбирает адаптер через `PAYMENT_GATEWAY_BACKEND=mock`.

Будущий `HttpPaymentGateway` должен инкапсулировать HTTP-клиента, provider-specific Pydantic DTO, mapping ошибок и идемпотентность провайдера. Проверка финального статуса в БД не защищает от окна «провайдер принял платёж, сервис упал до коммита», поэтому реальная интеграция требует идемпотентного списания или повторного поиска операции у провайдера.

## 6. Consumer и webhook

Consumer получает Pydantic-событие `PaymentCreatedEvent` из `payments.new`.

Для `pending` он вызывает порт шлюза и сохраняет результат и `processed_at`. Если платёж уже финальный, повторный вызов шлюза не выполняется. Это особенно важно при retry после ошибки webhook.

`PaymentWebhookPayload` — отдельная Pydantic DTO. `WebhookSender` принимает DTO, а не ORM-модель, и отправляет `model_dump(mode="json")` через HTTPX. Успешен только HTTP-ответ 2xx.

На исключения БД, шлюза или webhook выполняется максимум три попытки. Между ними задержки `RETRY_BASE_DELAY` и `2 * RETRY_BASE_DELAY`. Бизнес-результат `failed` — финальный статус платежа, а не инфраструктурная ошибка: его webhook отправляется штатно.

После третьей ошибки consumer делает `nack(requeue=False)`. RabbitMQ переносит сообщение через DLX в `payments.dead`. Автоматический replay из DLQ не выполняется.

## 7. Запуск и конфигурация

Docker Compose поднимает PostgreSQL, RabbitMQ, API и одного consumer. API применяет миграции перед запуском; consumer ждёт healthy API и RabbitMQ.

Makefile содержит единые команды `up`, `down`, `build`, `rebuild`, `ps`, `logs`, `queues`, `lint`, `format`, `test`, `ready`, `migrate`, `smoke` и локальный webhook-приёмник.

Порты и credentials по умолчанию предназначены только для локальной разработки. Порты можно переопределить через `.env`, чтобы не конфликтовать с другими проектами.

## 8. Проверки

### Unit

- создание и получение платежа;
- аутентификация и валидация;
- идемпотентный повтор и конфликт payload;
- атомарная запись платежа и Outbox;
- ошибка публикации оставляет событие неопубликованным;
- retry не вызывает шлюз повторно после финального статуса;
- ровно три попытки, задержки и финальный `NackMessage(requeue=False)`;
- webhook сериализуется из Pydantic DTO.

### Smoke

`make smoke` против запущенного Compose-стека:

- создаёт платёж с рабочим локальным webhook;
- проверяет финальный статус, `processed_at`, `published_at` и тело webhook;
- проверяет идемпотентный повтор и `409` на другой payload;
- создаёт платёж с недоступным webhook;
- проверяет сообщение с тем же `payment_id` в `payments.dead`.

### GitHub Actions

CI запускается на `push` и `pull_request`:

1. quality job: locked dependencies, форматирование, Ruff, unit-тесты;
2. integration job: сборка образов, Compose, smoke и гарантированная остановка стека.

## 9. Не входит в объём

- Реальный платёжный провайдер.
- Exactly-once доставка.
- Автоматический replay DLQ.
- Деплой в неопределённое окружение.
