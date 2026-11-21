# Async Payment Service

**Language / Язык:** [English](README.en.md) · [Русский](README.ru.md)

Asynchronous payment processing with FastAPI, PostgreSQL, RabbitMQ and the Transactional Outbox. A take-home assignment for a company, done as an example of my Python work.

Асинхронная обработка платежей на FastAPI, PostgreSQL и RabbitMQ с Transactional Outbox. Тестовое задание от компании, сделанное как пример моей работы с Python.

| | English | Русский |
|---|---|---|
| **README** (quick start, API, tests, config) | [README.en.md](README.en.md) | [README.ru.md](README.ru.md) |
| **Original requirements** | [docs/en/requirements.md](docs/en/requirements.md) | [docs/ru/requirements.md](docs/ru/requirements.md) |
| **Architecture specification** | [docs/en/spec.md](docs/en/spec.md) | [docs/ru/spec.md](docs/ru/spec.md) |

## Quick start / Быстрый запуск

```bash
make setup
make up
make smoke
```

Needs Docker Desktop and [uv](https://docs.astral.sh/uv/). / Нужны Docker Desktop и [uv](https://docs.astral.sh/uv/).
