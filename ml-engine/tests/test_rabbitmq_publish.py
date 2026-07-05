"""
Script standalone para testar a publicação de uma previsão fictícia no
RabbitMQ, sem depender do Streamlit.

Útil para validar isoladamente o fluxo:
    Python (pika) -> RabbitMQ -> Core Backend (Java, @RabbitListener)

Uso:
    python test_rabbitmq_publish.py
    python test_rabbitmq_publish.py --host localhost --symbol AAPL
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import pika


def publish_test_message(host: str, port: int, user: str, password: str, symbol: str) -> None:
    payload = {
        "symbol": symbol,
        "currentPrice": 195.42,
        "predictedPrice": 197.10,
        "trend": "UP",
        "confidence": 0.65,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }

    credentials = pika.PlainCredentials(user, password)
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=host, port=port, credentials=credentials)
    )
    channel = connection.channel()

    channel.exchange_declare(
        exchange="market.predictions.exchange", exchange_type="topic", durable=True
    )

    routing_key = f"market.predictions.{symbol}"
    body = json.dumps(payload)

    channel.basic_publish(
        exchange="market.predictions.exchange",
        routing_key=routing_key,
        body=body,
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=pika.DeliveryMode.Persistent,
        ),
    )

    print(f"✅ Mensagem publicada em '{routing_key}':")
    print(json.dumps(payload, indent=2))

    connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publica uma previsão de teste no RabbitMQ.")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5672)
    parser.add_argument("--user", default="guest")
    parser.add_argument("--password", default="guest")
    parser.add_argument("--symbol", default="AAPL")
    args = parser.parse_args()

    publish_test_message(args.host, args.port, args.user, args.password, args.symbol)
