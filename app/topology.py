from faststream.rabbit import RabbitExchange, RabbitQueue
from faststream.rabbit.schemas.constants import ExchangeType

PAYMENTS_EXCHANGE_NAME = "payments"
PAYMENTS_ROUTING_KEY = "payments.new"
DEAD_LETTER_EXCHANGE_NAME = "payments.dlx"
DEAD_LETTER_ROUTING_KEY = "payments.dead"

PAYMENTS_EXCHANGE = RabbitExchange(
    PAYMENTS_EXCHANGE_NAME,
    type=ExchangeType.DIRECT,
    durable=True,
)
PAYMENTS_QUEUE = RabbitQueue(
    PAYMENTS_ROUTING_KEY,
    durable=True,
    routing_key=PAYMENTS_ROUTING_KEY,
    arguments={
        "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE_NAME,
        "x-dead-letter-routing-key": DEAD_LETTER_ROUTING_KEY,
    },
)
DEAD_LETTER_EXCHANGE = RabbitExchange(
    DEAD_LETTER_EXCHANGE_NAME,
    type=ExchangeType.DIRECT,
    durable=True,
)
DEAD_LETTER_QUEUE = RabbitQueue(
    DEAD_LETTER_ROUTING_KEY,
    durable=True,
    routing_key=DEAD_LETTER_ROUTING_KEY,
)
