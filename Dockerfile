FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /service

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir . \
    && addgroup --system app \
    && adduser --system --ingroup app app

COPY alembic.ini ./
COPY alembic ./alembic

USER app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
