"""
TrendPulse AI - Dashboard
==========================

Ponto de entrada do frontend visual (Streamlit). Responsável por:
    1. Descarregar dados históricos de mercado via `yfinance`.
    2. Calcular indicadores técnicos (SMA 20 / SMA 50) com `pandas-ta`.
    3. Obter uma previsão do dia seguinte através do modelo scikit-learn
       treinado em `ml-engine/train_model.py` (com fallback heurístico caso
       o modelo ainda não tenha sido treinado).
    4. Renderizar um gráfico de velas interativo (dark mode) com `plotly`.
    5. Publicar o resultado processado numa fila RabbitMQ, para consumo
       pelo `core-backend` (Java/Spring Boot), que o distribui em tempo
       real via WebSocket.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trendpulse.dashboard")

# Torna o módulo `ml-engine` importável a partir do dashboard, já que os
# dois vivem em pastas irmãs dentro do monorepo (não são um único pacote
# Python instalado/publicado).
ML_ENGINE_PATH = Path(__file__).resolve().parent.parent / "ml-engine"
if str(ML_ENGINE_PATH) not in sys.path:
    sys.path.insert(0, str(ML_ENGINE_PATH))

try:
    from predict import predict_next_return  # noqa: E402

    MODEL_IMPORT_OK = True
except ImportError:
    logger.warning(
        "Módulo 'predict' do ml-engine não encontrado — só o fallback "
        "heurístico (cruzamento de médias móveis) estará disponível."
    )
    MODEL_IMPORT_OK = False
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
    source: str = "model"  # "model" (scikit-learn) ou "heuristic" (fallback SMA)

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


class MarketDataUnavailableError(Exception):
    """Erro de negócio: não foi possível obter dados de mercado após retries.

    Distinguimos deliberadamente este erro de exceções genéricas para que a
    UI (Streamlit) possa mostrar uma mensagem clara ao utilizador em vez de
    um stack trace bruto.
    """


def fetch_market_data(ticker: str, period: str = "6mo", max_retries: int = 3) -> pd.DataFrame:
    """Descarrega o histórico de preços (OHLCV) de um ativo via yfinance.

    Envolve a chamada de rede numa política de retry com backoff exponencial,
    já que APIs de dados de mercado gratuitas (Yahoo Finance) ocasionalmente
    devolvem respostas vazias ou erros transitórios (rate limiting, timeouts).
    Uma falha isolada não deve derrubar o dashboard — só desistimos e
    mostramos erro ao utilizador depois de `max_retries` tentativas.

    Args:
        ticker: símbolo do ativo (ex: "AAPL", "BTC-USD").
        period: janela histórica a obter (default: 6 meses).
        max_retries: número máximo de tentativas antes de desistir.

    Returns:
        DataFrame indexado por data, com colunas Open/High/Low/Close/Volume.

    Raises:
        MarketDataUnavailableError: se todas as tentativas falharem.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "A descarregar dados históricos para %s (período=%s, tentativa %d/%d)",
                ticker, period, attempt, max_retries,
            )
            data = yf.download(ticker, period=period, interval="1d", progress=False)

            if data.empty:
                raise ValueError(f"Resposta vazia do Yahoo Finance para '{ticker}'.")

            # yfinance pode devolver colunas MultiIndex quando se pedem vários
            # tickers; normalizamos para o caso de um único ticker.
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)

            return data

        except Exception as exc:  # noqa: BLE001 - qualquer falha de rede/parsing é elegível a retry
            last_error = exc
            logger.warning(
                "Falha ao obter dados para %s (tentativa %d/%d): %s",
                ticker, attempt, max_retries, exc,
            )
            if attempt < max_retries:
                backoff_seconds = 2 ** (attempt - 1)  # 1s, 2s, 4s, ...
                time.sleep(backoff_seconds)

    raise MarketDataUnavailableError(
        f"Não foi possível obter dados para '{ticker}' após {max_retries} tentativas. "
        f"Último erro: {last_error}"
    )


@st.cache_data(ttl=300, show_spinner=False)
def fetch_market_data_cached(ticker: str, period: str = "6mo") -> pd.DataFrame:
    """Wrapper cacheado de `fetch_market_data` (5 minutos de TTL).

    Mantido como função separada porque `st.cache_data` não deve envolver
    diretamente uma função com efeitos colaterais de retry/sleep visíveis —
    isolar a lógica de retry em `fetch_market_data` também a torna testável
    de forma independente do Streamlit.
    """
    return fetch_market_data(ticker, period=period)


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
    """Gera o payload de previsão para o ativo, usando o modelo scikit-learn
    treinado sempre que disponível, com fallback heurístico (cruzamento de
    médias móveis) caso contrário.

    Esta função nunca falha por falta do modelo: se `predict_next_return`
    devolver `None` (modelo não treinado, histórico insuficiente, ou erro
    de import), cai automaticamente na heurística — o dashboard continua
    sempre funcional, apenas com uma previsão menos sofisticada.
    """
    if MODEL_IMPORT_OK:
        model_result = predict_next_return(df)
        if model_result is not None:
            return PredictionPayload(
                symbol=ticker,
                current_price=round(float(df["Close"].iloc[-1]), 2),
                predicted_price=round(model_result.predicted_price, 2),
                trend=model_result.trend,
                confidence=round(model_result.confidence, 2),
                generated_at=datetime.now(timezone.utc).isoformat(),
                source="model",
            )

    return _build_heuristic_prediction(ticker, df)


def _build_heuristic_prediction(ticker: str, df: pd.DataFrame) -> PredictionPayload:
    """Fallback: previsão heurística por cruzamento de SMA 20 / SMA 50.

    Usada apenas quando o modelo scikit-learn ainda não foi treinado
    (`ml-engine/train_model.py`) ou não consegue gerar uma previsão.
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
        source="heuristic",
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
# Cliente WebSocket (STOMP/SockJS) embutido — liga o browser em tempo real
# --------------------------------------------------------------------------- #

def build_realtime_widget_html(backend_url: str, symbol: str) -> str:
    """Constrói o componente HTML/JS que liga diretamente ao WebSocket do Core Backend.

    O Streamlit em si não mantém uma ligação WebSocket persistente ao servidor
    Java — cada interação provoca um novo rerun do script Python. Para fechar
    o loop reativo "de facto", embutimos um pequeno cliente STOMP (via
    `st.components.v1.html`), que corre isolado num iframe no browser do
    utilizador e liga-se diretamente a `<backend_url>/ws-market`.

    Fluxo resultante:
        RabbitMQ -> @RabbitListener (Java) -> SimpMessagingTemplate
        -> /topic/predictions/<symbol> -> este cliente STOMP -> DOM (iframe)

    Ou seja: assim que o Core Backend recebe uma previsão da fila e a
    publica no tópico WebSocket, este widget atualiza-se sozinho — sem
    qualquer rerun do Streamlit e sem polling.

    Args:
        backend_url: URL base do core-backend (ex: "http://localhost:8080").
        symbol: símbolo do ativo a subscrever (ex: "AAPL").

    Returns:
        Bloco HTML/JS pronto a passar a `st.components.v1.html`.
    """
    ws_endpoint = f"{backend_url.rstrip('/')}/ws-market"
    topic = f"/topic/predictions/{symbol}"

    return f"""
    <div id="tp-widget" style="
        font-family: -apple-system, Segoe UI, Roboto, sans-serif;
        background-color: #161A25;
        border: 1px solid #232838;
        border-radius: 8px;
        padding: 16px;
        color: #E6E6E6;
    ">
        <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;">
            <div style="font-weight:600; font-size:14px;">
                🔌 Live feed — <span style="color:#4CC9F0;">{topic}</span>
            </div>
            <div id="tp-status" style="font-size:12px; padding:4px 10px; border-radius:12px; background:#3A2E1E; color:#F7B267;">
                A ligar...
            </div>
        </div>

        <div id="tp-current" style="
            display:flex; gap:24px; margin-bottom:14px; flex-wrap:wrap;
        ">
            <div><div style="font-size:11px;color:#8890A6;">PREÇO ATUAL</div><div id="tp-price" style="font-size:20px;font-weight:700;">—</div></div>
            <div><div style="font-size:11px;color:#8890A6;">PREVISTO</div><div id="tp-predicted" style="font-size:20px;font-weight:700;">—</div></div>
            <div><div style="font-size:11px;color:#8890A6;">TENDÊNCIA</div><div id="tp-trend" style="font-size:20px;font-weight:700;">—</div></div>
            <div><div style="font-size:11px;color:#8890A6;">CONFIANÇA</div><div id="tp-confidence" style="font-size:20px;font-weight:700;">—</div></div>
        </div>

        <div style="font-size:11px;color:#8890A6;margin-bottom:6px;">HISTÓRICO DE MENSAGENS RECEBIDAS</div>
        <div id="tp-log" style="
            height:140px; overflow-y:auto; background:#0E1117; border-radius:6px;
            padding:8px; font-family: 'SFMono-Regular', Consolas, monospace; font-size:12px;
            color:#8890A6; line-height:1.5;
        ">
            <div>À espera de mensagens...</div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/sockjs-client/1.6.1/sockjs.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/stomp.js/2.3.3/stomp.min.js"></script>
    <script>
        (function() {{
            const statusEl = document.getElementById("tp-status");
            const logEl = document.getElementById("tp-log");
            const priceEl = document.getElementById("tp-price");
            const predictedEl = document.getElementById("tp-predicted");
            const trendEl = document.getElementById("tp-trend");
            const confidenceEl = document.getElementById("tp-confidence");

            function setStatus(text, bg, color) {{
                statusEl.textContent = text;
                statusEl.style.background = bg;
                statusEl.style.color = color;
            }}

            function appendLog(line) {{
                const entry = document.createElement("div");
                const now = new Date().toLocaleTimeString();
                entry.textContent = "[" + now + "] " + line;
                logEl.appendChild(entry);
                logEl.scrollTop = logEl.scrollHeight;
            }}

            try {{
                const socket = new SockJS("{ws_endpoint}");
                const stompClient = Stomp.over(socket);
                stompClient.debug = null; // silencia logs verbosos no console

                stompClient.connect(
                    {{}},
                    function onConnect() {{
                        setStatus("● Ligado", "#1E3A2E", "#00D68F");
                        appendLog("Ligação WebSocket estabelecida a {ws_endpoint}");

                        stompClient.subscribe("{topic}", function (message) {{
                            const prediction = JSON.parse(message.body);
                            priceEl.textContent = "$" + Number(prediction.currentPrice).toFixed(2);
                            predictedEl.textContent = "$" + Number(prediction.predictedPrice).toFixed(2);
                            trendEl.textContent = prediction.trend;
                            trendEl.style.color = prediction.trend === "UP" ? "#00D68F"
                                : prediction.trend === "DOWN" ? "#FF4B5C" : "#E6E6E6";
                            confidenceEl.textContent = Math.round(prediction.confidence * 100) + "%";
                            appendLog("Previsão recebida: " + message.body);
                        }});
                    }},
                    function onError(error) {{
                        setStatus("● Erro de ligação", "#3A1E1E", "#FF4B5C");
                        appendLog("Erro ao ligar: " + JSON.stringify(error));
                    }}
                );

                window.addEventListener("beforeunload", function () {{
                    if (stompClient && stompClient.connected) {{
                        stompClient.disconnect();
                    }}
                }});
            }} catch (err) {{
                setStatus("● Indisponível", "#3A1E1E", "#FF4B5C");
                appendLog("Exceção ao inicializar o cliente WebSocket: " + err);
            }}
        }})();
    </script>
    """


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
    rabbitmq_host = st.sidebar.text_input("Host", value=os.environ.get("RABBITMQ_HOST", "localhost"))
    rabbitmq_port = st.sidebar.number_input(
        "Porta", value=int(os.environ.get("RABBITMQ_PORT", 5672)), min_value=1, max_value=65535
    )
    rabbitmq_user = st.sidebar.text_input("Utilizador", value=os.environ.get("RABBITMQ_USER", "guest"))
    rabbitmq_pass = st.sidebar.text_input(
        "Password", value=os.environ.get("RABBITMQ_PASS", "guest"), type="password"
    )

    st.sidebar.subheader("🔌 Core Backend (WebSocket)")
    backend_url = st.sidebar.text_input(
        "URL base",
        value=os.environ.get("BACKEND_PUBLIC_URL", "http://localhost:8080"),
        help="Endereço onde o core-backend Spring Boot está a correr, "
        "**tal como acessível a partir do teu browser** (o cliente WebSocket "
        "corre no browser, não dentro do container do dashboard). "
        "O endpoint STOMP/SockJS `/ws-market` é montado a partir daqui.",
    )

    st.title(f"{ticker} — Análise Técnica")

    try:
        with st.spinner("A obter dados de mercado..."):
            raw_data = fetch_market_data_cached(ticker, period=period)
            enriched_data = calculate_moving_averages(raw_data)
    except MarketDataUnavailableError as exc:
        st.error(f"⚠️ {exc}")
        st.caption(
            "O Yahoo Finance pode estar temporariamente indisponível ou a "
            "limitar pedidos. Tenta novamente dentro de alguns segundos."
        )
        if st.button("🔄 Tentar novamente"):
            fetch_market_data_cached.clear()
            st.rerun()
        return

    payload = build_prediction_payload(ticker, enriched_data)

    if payload.source == "model":
        st.success(
            "🤖 Previsão gerada pelo modelo **RandomForestRegressor** treinado "
            "(`ml-engine/train_model.py`).",
            icon="✅",
        )
    else:
        st.warning(
            "⚠️ A usar previsão **heurística de fallback** (cruzamento SMA 20/50) — "
            "o modelo scikit-learn ainda não foi treinado. Corre "
            "`python ml-engine/train_model.py` para ativar previsões reais de ML.",
            icon="⚠️",
        )

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

    st.subheader("📡 Previsão ao vivo (WebSocket)")
    st.caption(
        "Este painel liga-se diretamente ao `core-backend` via STOMP/SockJS e "
        "atualiza-se sozinho assim que uma nova previsão é publicada — sem "
        "recarregar a página. Publica uma previsão (abaixo) e observa esta "
        "secção reagir em tempo real."
    )
    components.html(
        build_realtime_widget_html(backend_url, ticker),
        height=340,
        scrolling=False,
    )

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
