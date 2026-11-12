from app.config import Settings
from app.gateways.contracts import PaymentGateway
from app.gateways.mock import MockPaymentGateway


def create_payment_gateway(settings: Settings) -> PaymentGateway:
    match settings.payment_gateway_backend:
        case "mock":
            return MockPaymentGateway(settings.gateway_min_delay, settings.gateway_max_delay)
        case backend:
            raise ValueError(f"Unsupported payment gateway backend: {backend}")
