"""
TrendPulse AI - Dashboard
==========================

Ponto de entrada do frontend visual (Streamlit). Responsável por:
    1. Descarregar dados históricos de mercado via `yfinance`.
    2. Calcular indicadores técnicos (SMA 20 / SMA 50) com `pandas-ta`.
    3. Renderizar um gráfico de velas interativo (dark mode) com `plotly`.
    4. Simular a publicação do resultado processado numa fila RabbitMQ,
       para consumo posterior pelo `core-backend` (Java/Spring Boot).

Nota: o envio real para o RabbitMQ está implementado como um "mock"
(ver `publish_to_rabbitmq`). Basta ligar a biblioteca `pika` a um broker
real para tornar o fluxo ponta-a-ponta funcional.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trendpulse.dashboard")

# --------------------------------------------------------------------------- #
# Configuração da página
# --------------------------------------------------------------------------- #

st.set_page_config(
    page_title="TrendPulse AI",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

DARK_BG = "#0E1117"
PANEL_BG = "#161A25"
ACCENT_UP = "#00D68F"
ACCENT_DOWN = "#FF4B5C"
SMA_20_COLOR = "#4CC9F0"
SMA_50_COLOR = "#F72585"
TEXT_COLOR = "#E6E6E6"

CUSTOM_CSS = f"""
<style>
    .stApp {{
        background-color: {DARK_BG};
        color: {TEXT_COLOR};
    }}
    section[data-testid="stSidebar"] {{
        background-color: {PANEL_BG};
    }}
    div[data-testid="stMetric"] {{
        background-color: {PANEL_BG};
        border-radius: 8px;
        padding: 12px;
        border: 1px solid #232838;
    }}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Camada de dados / ML
# --------------------------------------------------------------------------- #

@dataclass
class PredictionPayload:
    """Estrutura de mensagem publicada na fila `market.predictions.queue`.

    O nome dos campos espelha o DTO Java `PredictionMessage`, garantindo
    compatibilidade direta na (de)serialização JSON entre os dois serviços.
    """

    symbol: str
    current_price: float
    predicted_price: float
    trend: str
    confidence: float
    generated_at: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "symbol": self.symbol,
                "currentPrice": self.current_price,
                "predictedPrice": self.predicted_price,
                "trend": self.trend,
                "confidence": self.confidence,
                "generatedAt": self.generated_at,
            }
        )


@st.cache_data(ttl=300, show_spinner=False)
def fetch_market_data(ticker: str, period: str = "6mo") -> pd.DataFrame:
    """Descarrega o histórico de preços (OHLCV) de um ativo via yfinance.

    Args:
        ticker: símbolo do ativo (ex: "AAPL", "BTC-USD").
        period: janela histórica a obter (default: 6 meses).

    Returns:
        DataFrame indexado por data, com colunas Open/High/Low/Close/Volume.
    """
    logger.info("A descarregar dados históricos para %s (período=%s)", ticker, period)
    data = yf.download(ticker, period=period, interval="1d", progress=False)

    if data.empty:
        raise ValueError(f"Não foram encontrados dados para o símbolo '{ticker}'.")

    # yfinance pode devolver colunas MultiIndex quando se pedem vários tickers;
    # normalizamos para o caso de um único ticker.
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    return data


def calculate_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula as Médias Móveis Simples (SMA) de 20 e 50 dias.

    Args:
        df: DataFrame com a coluna "Close".

    Returns:
        O mesmo DataFrame, enriquecido com as colunas "SMA_20" e "SMA_50".
    """
    enriched = df.copy()
    enriched["SMA_20"] = ta.sma(enriched["Close"], length=20)
    enriched["SMA_50"] = ta.sma(enriched["Close"], length=50)
    return enriched


def build_prediction_payload(ticker: str, df: pd.DataFrame) -> PredictionPayload:
    """Gera um payload de previsão simplificado a partir dos dados processados.

    Nesta fase inicial do projeto, a "previsão" é heurística (cruzamento de
    médias móveis), servindo de placeholder até à integração do modelo
    scikit-learn treinado no `ml-engine`.
    """
    last_row = df.iloc[-1]
    current_price = float(last_row["Close"])
    sma_20 = float(last_row["SMA_20"]) if not pd.isna(last_row["SMA_20"]) else current_price
    sma_50 = float(last_row["SMA_50"]) if not pd.isna(last_row["SMA_50"]) else current_price

    if sma_20 > sma_50:
        trend = "UP"
        predicted_price = current_price * 1.01
        confidence = 0.62
    elif sma_20 < sma_50:
        trend = "DOWN"
        predicted_price = current_price * 0.99
        confidence = 0.58
    else:
        trend = "NEUTRAL"
        predicted_price = current_price
        confidence = 0.50

    return PredictionPayload(
        symbol=ticker,
        current_price=round(current_price, 2),
        predicted_price=round(predicted_price, 2),
        trend=trend,
        confidence=confidence,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# --------------------------------------------------------------------------- #
# Integração com RabbitMQ (mock)
# --------------------------------------------------------------------------- #

RABBITMQ_EXCHANGE = "market.predictions.exchange"
RABBITMQ_EXCHANGE_TYPE = "topic"


def publish_to_rabbitmq(
    payload: PredictionPayload,
    host: str = "localhost",
    port: int = 5672,
    username: str = "guest",
    password: str = "guest",
    timeout: float = 3.0,
) -> tuple[bool, str]:
    """Publica uma previsão na exchange do RabbitMQ (`market.predictions.exchange`).

    Estabelece uma ligação real via `pika.BlockingConnection`, declara a
    exchange (idempotente) e publica a mensagem com a routing key
    `market.predictions.<symbol>` — a mesma routing key que o
    `RabbitMQConfig` do Core Backend (Java) usa no bind à queue
    `market.predictions.queue`.

    Args:
        payload: previsão processada, pronta a serializar em JSON.
        host: endereço do broker RabbitMQ.
        port: porta AMQP (default: 5672).
        username: utilizador do broker.
        password: password do broker.
        timeout: timeout de ligação em segundos, para falhar rápido em vez
            de bloquear a UI do Streamlit indefinidamente.

    Returns:
        Tuplo (sucesso, mensagem) — a mensagem descreve o resultado ou o
        erro ocorrido, para ser mostrada diretamente na interface.
    """
    import pika

    message_body = payload.to_json()

    try:
        credentials = pika.PlainCredentials(username, password)
        parameters = pika.ConnectionParameters(
            host=host,
            port=port,
            credentials=credentials,
            blocked_connection_timeout=timeout,
            socket_timeout=timeout,
        )

        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()

        channel.exchange_declare(
            exchange=RABBITMQ_EXCHANGE,
            exchange_type=RABBITMQ_EXCHANGE_TYPE,
            durable=True,
        )

        routing_key = f"market.predictions.{payload.symbol}"

        channel.basic_publish(
            exchange=RABBITMQ_EXCHANGE,
            routing_key=routing_key,
            body=message_body,
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=pika.DeliveryMode.Persistent,
            ),
        )

        connection.close()

        logger.info(
            "Previsão publicada em '%s' com routing key '%s': %s",
            RABBITMQ_EXCHANGE,
            routing_key,
            message_body,
        )
        return True, f"Publicado com sucesso (routing key: `{routing_key}`)."

    except pika.exceptions.AMQPConnectionError as exc:
        logger.error("Falha ao ligar ao RabbitMQ em %s:%s -> %s", host, port, exc)
        return False, (
            f"Não foi possível ligar ao RabbitMQ em {host}:{port}. "
            "Confirma que o broker está a correr (`docker compose up rabbitmq`)."
        )
    except Exception as exc:  # noqa: BLE001 - capturar e mostrar qualquer falha ao utilizador
        logger.exception("Erro inesperado ao publicar no RabbitMQ")
        return False, f"Erro inesperado ao publicar: {exc}"


# --------------------------------------------------------------------------- #
# Camada de visualização
# --------------------------------------------------------------------------- #

def render_candlestick_chart(df: pd.DataFrame, ticker: str) -> go.Figure:
    """Constrói o gráfico de velas interativo com as médias móveis sobrepostas."""
    fig = go.Figure()

    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name=ticker,
            increasing_line_color=ACCENT_UP,
            decreasing_line_color=ACCENT_DOWN,
        )
    )

    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["SMA_20"],
            mode="lines",
            name="SMA 20",
            line=dict(color=SMA_20_COLOR, width=1.5),
        )
    )

    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["SMA_50"],
            mode="lines",
            name="SMA 50",
            line=dict(color=SMA_50_COLOR, width=1.5),
        )
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=DARK_BG,
        plot_bgcolor=DARK_BG,
        font=dict(color=TEXT_COLOR),
        xaxis_rangeslider_visible=False,
        margin=dict(l=20, r=20, t=40, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=600,
    )

    return fig


# --------------------------------------------------------------------------- #
# Interface Streamlit
# --------------------------------------------------------------------------- #

def main() -> None:
    st.sidebar.title("📈 TrendPulse AI")
    st.sidebar.caption("Plataforma reativa de análise preditiva de mercados")

    ticker = st.sidebar.selectbox(
        "Ativo",
        options=["AAPL", "BTC-USD", "MSFT", "TSLA", "ETH-USD"],
        index=0,
    )
    period = st.sidebar.selectbox(
        "Período histórico",
        options=["3mo", "6mo", "1y", "2y"],
        index=1,
    )

    st.sidebar.divider()
    st.sidebar.caption("Ingestão → Processamento → Mensageria → WebSocket")

    st.sidebar.subheader("🐇 Ligação RabbitMQ")
    rabbitmq_host = st.sidebar.text_input("Host", value="localhost")
    rabbitmq_port = st.sidebar.number_input("Porta", value=5672, min_value=1, max_value=65535)
    rabbitmq_user = st.sidebar.text_input("Utilizador", value="guest")
    rabbitmq_pass = st.sidebar.text_input("Password", value="guest", type="password")

    st.title(f"{ticker} — Análise Técnica")

    try:
        with st.spinner("A obter dados de mercado..."):
            raw_data = fetch_market_data(ticker, period=period)
            enriched_data = calculate_moving_averages(raw_data)
    except ValueError as exc:
        st.error(str(exc))
        return

    payload = build_prediction_payload(ticker, enriched_data)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Preço Atual", f"${payload.current_price:,.2f}")
    col2.metric(
        "Preço Previsto",
        f"${payload.predicted_price:,.2f}",
        delta=f"{(payload.predicted_price - payload.current_price):+.2f}",
    )
    col3.metric("Tendência", payload.trend)
    col4.metric("Confiança do Modelo", f"{payload.confidence * 100:.0f}%")

    fig = render_candlestick_chart(enriched_data, ticker)
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("📤 Publicação assíncrona (RabbitMQ)", expanded=True):
        st.code(payload.to_json(), language="json")
        if st.button("Publicar previsão na fila", type="primary"):
            with st.spinner(f"A ligar a {rabbitmq_host}:{rabbitmq_port}..."):
                success, message = publish_to_rabbitmq(
                    payload,
                    host=rabbitmq_host,
                    port=int(rabbitmq_port),
                    username=rabbitmq_user,
                    password=rabbitmq_pass,
                )
            if success:
                st.success(message)
                st.caption(
                    "Verifica os logs do `core-backend` — o `MarketDataListener` "
                    "deve ter consumido esta mensagem quase instantaneamente."
                )
            else:
                st.error(message)

    st.caption(
        "Dados fornecidos por Yahoo Finance via yfinance. "
        "Indicadores calculados com pandas-ta. Uso exclusivamente educacional."
    )


if __name__ == "__main__":
    main()
