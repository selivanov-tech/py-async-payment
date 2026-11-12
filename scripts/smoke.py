"""Run an end-to-end check against the running Docker Compose stack."""

import json
import os
import queue
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx


def new_id() -> uuid.UUID:
    return uuid.uuid7()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def create_payment(
    client: httpx.Client,
    body: dict[str, Any],
    idempotency_key: str,
) -> httpx.Response:
    return client.post(
        "/api/v1/payments",
        json=body,
        headers={"Idempotency-Key": idempotency_key},
    )


def wait_for_final_payment(client: httpx.Client, payment_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/payments/{payment_id}")
        response.raise_for_status()
        payment = response.json()
        if payment["status"] in {"succeeded", "failed"} and payment["processed_at"]:
            return payment
        time.sleep(0.5)
    raise TimeoutError(f"Payment {payment_id} was not processed in time")


def check_outbox_published(payment_id: str) -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "payments",
            "-d",
            "payments",
            "-At",
            "-c",
            f"SELECT count(*) FROM outbox WHERE payment_id = '{payment_id}' "
            "AND published_at IS NOT NULL",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    check(result.stdout.strip() == "1", f"Outbox event for {payment_id} was not published")


def wait_for_dlq_message(client: httpx.Client, payment_id: str) -> None:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        response = client.post(
            "/api/queues/%2F/payments.dead/get",
            json={"count": 100, "ackmode": "ack_requeue_true", "encoding": "auto"},
        )
        response.raise_for_status()
        for message in response.json():
            payload = message["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if payload.get("payment_id") == payment_id:
                return
        time.sleep(0.5)
    raise TimeoutError(f"Payment {payment_id} did not reach payments.dead")


def main() -> None:
    received: queue.Queue[dict[str, Any]] = queue.Queue()

    class WebhookHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            received.put(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"received":true}')

        def log_message(self, format: str, *args: Any) -> None:
            return

    receiver = ThreadingHTTPServer(("0.0.0.0", 0), WebhookHandler)
    receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    receiver_thread.start()
    webhook_url = f"http://host.docker.internal:{receiver.server_port}/webhook"

    api_url = os.getenv("API_URL", f"http://localhost:{os.getenv('API_PORT', '18100')}")
    management_url = os.getenv(
        "RABBITMQ_MANAGEMENT_URL",
        f"http://localhost:{os.getenv('RABBITMQ_MANAGEMENT_PORT', '15673')}",
    )
    api_key = os.getenv("API_KEY", "dev-secret-key")

    try:
        with httpx.Client(
            base_url=api_url,
            headers={"X-API-Key": api_key},
            timeout=10,
        ) as client:
            health = client.get("/health")
            health.raise_for_status()

            key = f"smoke-{new_id()}"
            body = {
                "amount": "1499.90",
                "currency": "RUB",
                "description": "Compose smoke test",
                "metadata": {"source": "smoke"},
                "webhook_url": webhook_url,
            }
            created = create_payment(client, body, key)
            check(created.status_code == 202, created.text)
            first = created.json()
            payment_id = first["payment_id"]
            check(uuid.UUID(payment_id).version == 7, "Payment ID is not UUIDv7")

            replay = create_payment(client, body, key)
            check(replay.status_code == 202, replay.text)
            check(replay.json() == first, "Idempotent replay returned another payment")

            conflict = create_payment(client, {**body, "amount": "1500.00"}, key)
            check(conflict.status_code == 409, conflict.text)

            final = wait_for_final_payment(client, payment_id)
            check_outbox_published(payment_id)
            webhook = received.get(timeout=10)
            check(webhook["payment_id"] == payment_id, "Webhook contains another payment")
            check(webhook["status"] == final["status"], "Webhook status differs from the API")
            check(webhook["amount"] == "1499.90", "Webhook amount differs from the request")
            check(webhook["metadata"] == {"source": "smoke"}, "Webhook metadata was changed")
            print(f"Success flow: payment {payment_id}, status {final['status']}")

            failed_key = f"smoke-dlq-{new_id()}"
            failed_body = {
                **body,
                "description": "DLQ smoke test",
                "webhook_url": "http://host.docker.internal:65534/webhook",
            }
            failed_response = create_payment(client, failed_body, failed_key)
            check(failed_response.status_code == 202, failed_response.text)
            failed_id = failed_response.json()["payment_id"]
            wait_for_final_payment(client, failed_id)
            check_outbox_published(failed_id)

            with httpx.Client(
                base_url=management_url,
                auth=httpx.BasicAuth("guest", "guest"),
                timeout=10,
            ) as rabbit:
                wait_for_dlq_message(rabbit, failed_id)
            print(f"DLQ flow: payment {failed_id} reached payments.dead")
    finally:
        receiver.shutdown()
        receiver.server_close()
        receiver_thread.join(timeout=5)

    print("Smoke checks passed.")


if __name__ == "__main__":
    main()
