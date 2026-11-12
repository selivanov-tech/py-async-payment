"""Tiny local webhook receiver: python examples/webhook_server.py [port]."""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"received":true}')

    def log_message(self, format: str, *args) -> None:
        return


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9001
    print(f"Webhook receiver: http://localhost:{port}/webhook", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), WebhookHandler).serve_forever()
