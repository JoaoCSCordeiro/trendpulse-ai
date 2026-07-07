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
import requests
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

# --- Sistema de design (tokens) ---------------------------------------------
# Paleta pensada como um terminal de trading moderno: navy quase-preto em vez
# de cinza neutro, com dois acentos que carregam significado de domínio
# (teal = alta, coral = baixa) em vez de cores decorativas arbitrárias.
BG = "#0A0E1A"            # fundo da aplicação
SURFACE = "#131826"       # cartões, painéis, sidebar
SURFACE_ALT = "#1A2032"   # hover/estados alternados
BORDER = "#232B3D"        # hairlines
TEXT_PRIMARY = "#E8EBF3"
TEXT_MUTED = "#7C8699"
ACCENT_UP = "#2DD4BF"      # teal — bullish / marca
ACCENT_DOWN = "#FB7185"    # coral — bearish
ACCENT_GOLD = "#FBBF24"    # confiança / destaque neutro
SMA_20_COLOR = "#38BDF8"
SMA_50_COLOR = "#C084FC"

# Aliases mantidos para compatibilidade com o resto do módulo (gráficos Plotly,
# gauge, etc. já referenciam estes nomes em várias funções).
DARK_BG = BG
PANEL_BG = SURFACE
TEXT_COLOR = TEXT_PRIMARY

CUSTOM_CSS = f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&display=swap');

    html, body, [class*="css"] {{
        font-family: 'Inter', -apple-system, sans-serif;
    }}

    .stApp {{
        background: radial-gradient(ellipse 120% 60% at 50% -10%, #131A2E 0%, {BG} 55%);
        color: {TEXT_PRIMARY};
    }}

    h1, h2, h3, .tp-display {{
        font-family: 'Space Grotesk', sans-serif !important;
        letter-spacing: -0.02em;
    }}

    section[data-testid="stSidebar"] {{
        background-color: {SURFACE};
        border-right: 1px solid {BORDER};
    }}
    section[data-testid="stSidebar"] .stSelectbox label,
    section[data-testid="stSidebar"] .stTextInput label,
    section[data-testid="stSidebar"] .stNumberInput label {{
        color: {TEXT_MUTED};
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }}

    /* Tabs — mais parecidas com abas de terminal do que com o default do Streamlit */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 4px;
        border-bottom: 1px solid {BORDER};
    }}
    .stTabs [data-baseweb="tab"] {{
        height: 44px;
        background-color: transparent;
        border-radius: 8px 8px 0 0;
        color: {TEXT_MUTED};
        font-family: 'Space Grotesk', sans-serif;
        font-weight: 600;
        font-size: 0.92rem;
    }}
    .stTabs [aria-selected="true"] {{
        background-color: {SURFACE} !important;
        color: {TEXT_PRIMARY} !important;
        border-bottom: 2px solid {ACCENT_UP} !important;
    }}

    /* Cartões de métrica nativos, para os poucos que ainda usamos */
    div[data-testid="stMetric"] {{
        background-color: {SURFACE};
        border-radius: 12px;
        padding: 14px 16px;
        border: 1px solid {BORDER};
    }}
    div[data-testid="stMetricValue"] {{
        font-family: 'JetBrains Mono', monospace;
    }}

    div[data-testid="stExpander"] {{
        background-color: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 12px;
    }}

    .stButton > button {{
        border-radius: 8px;
        font-family: 'Space Grotesk', sans-serif;
        font-weight: 600;
        border: 1px solid {BORDER};
    }}

    /* --- Fita de cotações (elemento assinatura) --- */
    .tp-ticker-wrap {{
        width: 100%;
        overflow: hidden;
        background-color: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 10px 0;
        margin-bottom: 1.2rem;
    }}
    .tp-ticker-track {{
        display: flex;
        width: max-content;
        animation: tp-scroll 32s linear infinite;
    }}
    .tp-ticker-wrap:hover .tp-ticker-track {{
        animation-play-state: paused;
    }}
    @keyframes tp-scroll {{
        0% {{ transform: translateX(0); }}
        100% {{ transform: translateX(-50%); }}
    }}
    .tp-ticker-item {{
        display: flex;
        align-items: baseline;
        gap: 8px;
        padding: 0 28px;
        border-right: 1px solid {BORDER};
        white-space: nowrap;
        font-family: 'JetBrains Mono', monospace;
    }}
    .tp-ticker-symbol {{
        font-family: 'Space Grotesk', sans-serif;
        font-weight: 600;
        color: {TEXT_PRIMARY};
        font-size: 0.85rem;
    }}
    .tp-ticker-price {{
        color: {TEXT_MUTED};
        font-size: 0.85rem;
    }}
    .tp-ticker-delta-up {{ color: {ACCENT_UP}; font-size: 0.8rem; }}
    .tp-ticker-delta-down {{ color: {ACCENT_DOWN}; font-size: 0.8rem; }}

    /* --- Cabeçalho com pill de estado "ao vivo" --- */
    .tp-header {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 0.4rem;
    }}
    .tp-live-pill {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        background-color: rgba(45, 212, 191, 0.1);
        border: 1px solid rgba(45, 212, 191, 0.35);
        color: {ACCENT_UP};
        border-radius: 999px;
        padding: 5px 12px;
        font-size: 0.78rem;
        font-family: 'Space Grotesk', sans-serif;
        font-weight: 600;
    }}
    .tp-pulse-dot {{
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background-color: {ACCENT_UP};
        box-shadow: 0 0 0 rgba(45, 212, 191, 0.6);
        animation: tp-pulse 1.8s infinite;
    }}
    @keyframes tp-pulse {{
        0% {{ box-shadow: 0 0 0 0 rgba(45, 212, 191, 0.5); }}
        70% {{ box-shadow: 0 0 0 8px rgba(45, 212, 191, 0); }}
        100% {{ box-shadow: 0 0 0 0 rgba(45, 212, 191, 0); }}
    }}

    /* --- Cartões de métrica customizados --- */
    .tp-card-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
        gap: 12px;
        margin-bottom: 1rem;
    }}
    .tp-card {{
        background-color: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 12px;
        padding: 16px 18px;
        transition: border-color 0.15s ease, transform 0.15s ease;
    }}
    .tp-card:hover {{
        border-color: {ACCENT_UP}55;
        transform: translateY(-2px);
    }}
    .tp-card-label {{
        color: {TEXT_MUTED};
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 6px;
    }}
    .tp-card-value {{
        font-family: 'JetBrains Mono', monospace;
        font-size: 1.55rem;
        font-weight: 600;
        color: {TEXT_PRIMARY};
        line-height: 1.1;
    }}
    .tp-card-delta {{
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.8rem;
        margin-top: 4px;
    }}

    /* --- Badge de fonte da previsão (modelo vs. heurística) --- */
    .tp-badge {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        border-radius: 999px;
        padding: 4px 12px;
        font-size: 0.75rem;
        font-weight: 600;
        font-family: 'Space Grotesk', sans-serif;
    }}
    .tp-badge-model {{
        background-color: rgba(45, 212, 191, 0.12);
        color: {ACCENT_UP};
        border: 1px solid rgba(45, 212, 191, 0.3);
    }}
    .tp-badge-heuristic {{
        background-color: rgba(251, 191, 36, 0.12);
        color: {ACCENT_GOLD};
        border: 1px solid rgba(251, 191, 36, 0.3);
    }}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def render_metric_cards(cards: list[dict]) -> None:
    """Grid de cartões de métrica customizados (substitui `st.metric` nativo).

    Cada `card` é um dict com `label`, `value`, e opcionalmente `delta`
    (string já formatada) e `delta_positive` (bool, controla a cor).
    Usar HTML/CSS próprio em vez de `st.metric` dá controlo total sobre
    tipografia (monoespaçada para os números, como um terminal financeiro)
    e permite o hover/elevação subtil que assinala interatividade.
    """
    cards_html = "".join(
        f"""
        <div class="tp-card">
            <div class="tp-card-label">{card['label']}</div>
            <div class="tp-card-value">{card['value']}</div>
            {f'<div class="tp-card-delta" style="color:{ACCENT_UP if card.get("delta_positive") else ACCENT_DOWN};">{card["delta"]}</div>' if card.get('delta') else ''}
        </div>
        """
        for card in cards
    )
    st.markdown(f'<div class="tp-card-grid">{cards_html}</div>', unsafe_allow_html=True)


@st.cache_data(ttl=300, show_spinner=False)
def build_ticker_tape(tickers: list[str]) -> str:
    """Constrói o HTML da fita de cotações animada (elemento assinatura do dashboard).

    Duplicamos a lista de itens uma vez (`items + items`) para que a animação
    CSS de translação a -50% crie um loop infinito sem salto percetível — é
    o truque clássico de implementar um "marquee" sem JavaScript.
    """
    items_html = []
    for ticker in tickers:
        try:
            raw = fetch_market_data_cached(ticker, period="3mo")
            if raw is None or raw.empty or len(raw) < 2:
                continue
            last_close = float(raw["Close"].iloc[-1])
            prev_close = float(raw["Close"].iloc[-2])
            change_pct = (last_close - prev_close) / prev_close * 100
            arrow = "▲" if change_pct >= 0 else "▼"
            delta_class = "tp-ticker-delta-up" if change_pct >= 0 else "tp-ticker-delta-down"
            items_html.append(
                f'<div class="tp-ticker-item">'
                f'<span class="tp-ticker-symbol">{ticker}</span>'
                f'<span class="tp-ticker-price">${last_close:,.2f}</span>'
                f'<span class="{delta_class}">{arrow} {abs(change_pct):.2f}%</span>'
                f'</div>'
            )
        except Exception:  # noqa: BLE001 - a fita é decorativa; uma falha isolada não deve derrubar o dashboard
            continue

    if not items_html:
        return ""

    track = "".join(items_html) * 2  # duplicado para o loop de animação
    return f'<div class="tp-ticker-wrap"><div class="tp-ticker-track">{track}</div></div>'


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
def _fetch_market_data_short_ttl(ticker: str, period: str) -> pd.DataFrame:
    """Bucket de cache para períodos curtos (TTL: 5 min).

    Períodos curtos (ex: 3mo/6mo) são os mais usados interativamente —
    faz sentido refrescar com mais frequência, já que o utilizador está
    provavelmente a acompanhar o ativo de perto.
    """
    return fetch_market_data(ticker, period=period)


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_market_data_long_ttl(ticker: str, period: str) -> pd.DataFrame:
    """Bucket de cache para períodos longos (TTL: 1 hora).

    Pedir 1y/2y de histórico é uma chamada mais pesada ao Yahoo Finance, e o
    histórico "antigo" praticamente não muda de um minuto para o outro — só
    o último dia é realmente novo. Um TTL de 5 minutos aqui seria
    desperdício de chamadas de rede sem qualquer benefício percetível.
    """
    return fetch_market_data(ticker, period=period)


# Períodos considerados "longos" o suficiente para usar o TTL de 1 hora.
_LONG_PERIODS = {"1y", "2y", "5y"}


def fetch_market_data_cached(ticker: str, period: str = "6mo") -> pd.DataFrame:
    """Wrapper cacheado de `fetch_market_data`, com TTL adaptado ao período pedido.

    Isolar a lógica de retry em `fetch_market_data` (sem decorador) também a
    torna testável de forma independente do Streamlit — só a camada de cache
    fica acoplada à UI.
    """
    if period in _LONG_PERIODS:
        return _fetch_market_data_long_ttl(ticker, period)
    return _fetch_market_data_short_ttl(ticker, period)


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
# Cliente REST do Core Backend — histórico e accuracy
# --------------------------------------------------------------------------- #

@st.cache_data(ttl=60, show_spinner=False)
def fetch_prediction_history(
    backend_url: str, symbol: str, page: int = 0, size: int = 100
) -> Optional[pd.DataFrame]:
    """Consulta o histórico paginado de previsões via `GET /api/v1/predictions/{symbol}/history`.

    Usado para construir o gráfico de "previsto vs. real" — só é útil depois
    do `AccuracyEvaluationService` (Java) ter avaliado previsões antigas, mas
    a chamada é sempre segura mesmo antes disso (devolve `actualPrice` nulo).

    Args:
        backend_url: URL base do core-backend, tal como acessível a partir
            deste processo Python (não confundir com o `BACKEND_PUBLIC_URL`
            usado pelo cliente WebSocket no browser — esta chamada corre
            server-side, dentro do container/processo do dashboard).
        symbol: símbolo do ativo.
        page: número da página (0-indexed).
        size: tamanho da página.

    Returns:
        DataFrame com as previsões (colunas: symbol, currentPrice,
        predictedPrice, trend, confidence, generatedAt, actualPrice,
        actualReturn), ou `None` se o backend estiver indisponível.
    """
    try:
        response = requests.get(
            f"{backend_url.rstrip('/')}/api/v1/predictions/{symbol}/history",
            params={"page": page, "size": size},
            timeout=3,
        )
        response.raise_for_status()
        payload = response.json()

        records = payload.get("content", [])
        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df["generatedAt"] = pd.to_datetime(df["generatedAt"])
        return df.sort_values("generatedAt")

    except requests.exceptions.RequestException as exc:
        logger.warning("Não foi possível obter histórico de previsões do backend: %s", exc)
        return None


@st.cache_data(ttl=60, show_spinner=False)
def fetch_accuracy_stats(backend_url: str, symbol: str) -> Optional[dict]:
    """Consulta `GET /api/v1/predictions/{symbol}/accuracy`.

    Returns:
        Dict com `totalEvaluated`, `correctDirection`, `accuracyRate`,
        `averageConfidence`, ou `None` se o backend estiver indisponível.
    """
    try:
        response = requests.get(
            f"{backend_url.rstrip('/')}/api/v1/predictions/{symbol}/accuracy",
            timeout=3,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("Não foi possível obter estatísticas de accuracy do backend: %s", exc)
        return None


def render_accuracy_chart(history_df: pd.DataFrame, ticker: str) -> go.Figure:
    """Constrói o gráfico "previsto vs. real" a partir do histórico persistido.

    Cada ponto no tempo mostra o preço previsto pelo modelo (linha
    tracejada) contra o preço real observado no dia seguinte (linha sólida)
    — só previsões já avaliadas (`actualPrice` preenchido) aparecem na linha
    "real", já que as mais recentes ainda não "amadureceram" o suficiente
    para o job de accuracy as ter avaliado.
    """
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=history_df["generatedAt"],
            y=history_df["predictedPrice"],
            mode="lines+markers",
            name="Previsto",
            line=dict(color=SMA_20_COLOR, width=2, dash="dot"),
            marker=dict(size=5),
        )
    )

    evaluated = history_df.dropna(subset=["actualPrice"])
    if not evaluated.empty:
        fig.add_trace(
            go.Scatter(
                x=evaluated["generatedAt"],
                y=evaluated["actualPrice"],
                mode="lines+markers",
                name="Real (observado)",
                line=dict(color=ACCENT_UP, width=2),
                marker=dict(size=6),
            )
        )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=DARK_BG,
        plot_bgcolor=DARK_BG,
        font=dict(color=TEXT_COLOR),
        margin=dict(l=20, r=20, t=30, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=320,
        title=f"{ticker} — Previsto vs. Real ao longo do tempo",
    )

    return fig


def render_confidence_gauge(confidence: float) -> go.Figure:
    """Semicírculo colorido de confiança — mais imediato de ler do que só um número.

    Vermelho/laranja para confiança baixa, verde para confiança alta. Os
    limiares (0.5/0.65/0.8) refletem o intervalo real produzido pelo modelo
    (ver `MIN_CONFIDENCE`/`MAX_CONFIDENCE` em `ml-engine/predict.py`), não
    uma escala genérica de 0-100%.
    """
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=confidence * 100,
            number={"suffix": "%", "font": {"color": TEXT_COLOR, "size": 28}},
            gauge={
                "axis": {"range": [50, 95], "tickcolor": TEXT_COLOR, "tickfont": {"color": TEXT_COLOR}},
                "bar": {"color": SMA_20_COLOR},
                "bgcolor": PANEL_BG,
                "borderwidth": 0,
                "steps": [
                    {"range": [50, 65], "color": "#3F1D24"},
                    {"range": [65, 80], "color": "#3A2E1E"},
                    {"range": [80, 95], "color": "#134E4A"},
                ],
            },
        )
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT_COLOR),
        height=160,
        margin=dict(l=20, r=20, t=10, b=10),
    )
    return fig


# --------------------------------------------------------------------------- #
# Camada de visualização
# --------------------------------------------------------------------------- #

def render_candlestick_chart(
    df: pd.DataFrame, ticker: str, prediction: Optional[PredictionPayload] = None
) -> go.Figure:
    """Constrói o gráfico de velas interativo com as médias móveis sobrepostas.

    Se `prediction` for fornecido, adiciona uma anotação visual a marcar
    onde o modelo prevê que o preço vá no dia seguinte — em vez do valor
    previsto viver só nos cartões de métricas, fica ancorado visualmente ao
    ponto exato do gráfico a que se refere.
    """
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

    if prediction is not None:
        last_date = df.index[-1]
        # Projeta o ponto previsto um dia útil à frente do último dado real,
        # para ficar visualmente separado do candlestick mais recente.
        next_date = last_date + pd.tseries.offsets.BDay(1)
        marker_color = ACCENT_UP if prediction.trend == "UP" else (
            ACCENT_DOWN if prediction.trend == "DOWN" else TEXT_COLOR
        )

        fig.add_trace(
            go.Scatter(
                x=[last_date, next_date],
                y=[prediction.current_price, prediction.predicted_price],
                mode="lines+markers",
                name="Previsão (modelo)",
                line=dict(color=marker_color, width=2, dash="dash"),
                marker=dict(size=[6, 12], symbol=["circle", "star"]),
            )
        )

        fig.add_annotation(
            x=next_date,
            y=prediction.predicted_price,
            text=f"Previsto: ${prediction.predicted_price:,.2f} ({prediction.trend})",
            showarrow=True,
            arrowhead=2,
            arrowcolor=marker_color,
            font=dict(color=marker_color, size=12),
            bgcolor=PANEL_BG,
            bordercolor=marker_color,
            borderwidth=1,
            ax=40,
            ay=-40,
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
        font-family: 'Inter', -apple-system, sans-serif;
        background-color: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 12px;
        padding: 18px;
        color: {TEXT_PRIMARY};
    ">
        <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:14px;">
            <div style="font-weight:600; font-size:14px; font-family: 'Space Grotesk', sans-serif;">
                🔌 Live feed — <span style="color:{SMA_20_COLOR};">{topic}</span>
            </div>
            <div id="tp-status" style="font-size:12px; padding:4px 10px; border-radius:999px; background:#3A2E1E; color:{ACCENT_GOLD};">
                A ligar...
            </div>
        </div>

        <div id="tp-current" style="
            display:flex; gap:24px; margin-bottom:14px; flex-wrap:wrap;
        ">
            <div><div style="font-size:11px;color:{TEXT_MUTED};text-transform:uppercase;letter-spacing:0.04em;">Preço Atual</div><div id="tp-price" style="font-size:20px;font-weight:700;font-family:'JetBrains Mono',monospace;">—</div></div>
            <div><div style="font-size:11px;color:{TEXT_MUTED};text-transform:uppercase;letter-spacing:0.04em;">Previsto</div><div id="tp-predicted" style="font-size:20px;font-weight:700;font-family:'JetBrains Mono',monospace;">—</div></div>
            <div><div style="font-size:11px;color:{TEXT_MUTED};text-transform:uppercase;letter-spacing:0.04em;">Tendência</div><div id="tp-trend" style="font-size:20px;font-weight:700;font-family:'JetBrains Mono',monospace;">—</div></div>
            <div><div style="font-size:11px;color:{TEXT_MUTED};text-transform:uppercase;letter-spacing:0.04em;">Confiança</div><div id="tp-confidence" style="font-size:20px;font-weight:700;font-family:'JetBrains Mono',monospace;">—</div></div>
        </div>

        <div style="font-size:11px;color:{TEXT_MUTED};margin-bottom:6px;text-transform:uppercase;letter-spacing:0.04em;">Histórico de Mensagens Recebidas</div>
        <div id="tp-log" style="
            height:140px; overflow-y:auto; background:{BG}; border-radius:8px;
            padding:10px; font-family: 'JetBrains Mono', monospace; font-size:12px;
            color:{TEXT_MUTED}; line-height:1.6; border:1px solid {BORDER};
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
                // Regista a ligação ativa numa "prateleira" partilhada em
                // `window.top`, para conseguirmos fechar explicitamente
                // qualquer ligação anterior (ex: de um ticker diferente)
                // antes de abrir uma nova. `components.html` recria este
                // iframe a cada rerun do Streamlit; o browser normalmente já
                // fecha sockets de iframes destruídos, mas esta prateleira
                // dá-nos uma garantia explícita em vez de depender só desse
                // comportamento implícito (e cobre também o caso de o
                // navegador reutilizar o iframe em vez de o recriar).
                let registry;
                try {{
                    window.top.__tpActiveSockets = window.top.__tpActiveSockets || {{}};
                    registry = window.top.__tpActiveSockets;
                }} catch (crossOriginErr) {{
                    // Fallback se `window.top` não for acessível (iframe sandboxed
                    // com origem diferente) — usamos o próprio `window` do iframe.
                    window.__tpActiveSockets = window.__tpActiveSockets || {{}};
                    registry = window.__tpActiveSockets;
                }}

                const REGISTRY_KEY = "tp-live-feed";
                const previousClient = registry[REGISTRY_KEY];
                if (previousClient && previousClient.connected) {{
                    previousClient.disconnect();
                    appendLog("Ligação anterior fechada explicitamente (troca de ativo).");
                }}

                const socket = new SockJS("{ws_endpoint}");
                const stompClient = Stomp.over(socket);
                stompClient.debug = null; // silencia logs verbosos no console

                stompClient.connect(
                    {{}},
                    function onConnect() {{
                        setStatus("● Ligado", "#134E4A", "#2DD4BF");
                        appendLog("Ligação WebSocket estabelecida a {ws_endpoint}");

                        stompClient.subscribe("{topic}", function (message) {{
                            const prediction = JSON.parse(message.body);
                            priceEl.textContent = "$" + Number(prediction.currentPrice).toFixed(2);
                            predictedEl.textContent = "$" + Number(prediction.predictedPrice).toFixed(2);
                            trendEl.textContent = prediction.trend;
                            trendEl.style.color = prediction.trend === "UP" ? "#2DD4BF"
                                : prediction.trend === "DOWN" ? "#FB7185" : "#E8EBF3";
                            confidenceEl.textContent = Math.round(prediction.confidence * 100) + "%";
                            appendLog("Previsão recebida: " + message.body);
                        }});
                    }},
                    function onError(error) {{
                        setStatus("● Erro de ligação", "#3F1D24", "#FB7185");
                        appendLog("Erro ao ligar: " + JSON.stringify(error));
                    }}
                );

                registry[REGISTRY_KEY] = stompClient;

                function disconnectCleanly() {{
                    if (stompClient && stompClient.connected) {{
                        stompClient.disconnect();
                    }}
                }}

                // 'beforeunload' cobre navegação/fecho normal; 'pagehide' cobre
                // também bfcache (voltar atrás no browser, comum em mobile Safari)
                // — sem isto, alguns browsers deixam a ligação pendurada nesses casos.
                window.addEventListener("beforeunload", disconnectCleanly);
                window.addEventListener("pagehide", disconnectCleanly);
            }} catch (err) {{
                setStatus("● Indisponível", "#3F1D24", "#FB7185");
                appendLog("Exceção ao inicializar o cliente WebSocket: " + err);
            }}
        }})();
    </script>
    """


# --------------------------------------------------------------------------- #
# Grid comparativo multi-ativo
# --------------------------------------------------------------------------- #

DEFAULT_COMPARISON_TICKERS = ["AAPL", "BTC-USD", "TSLA", "MSFT"]


def render_multi_asset_grid(tickers: list[str], period: str) -> None:
    """Mostra um grid de mini-cards comparando vários ativos em simultâneo.

    Pensado para responder a "como está o mercado em geral", em vez de
    forçar a escolha de um único ticker de cada vez — mais rápido de
    varrer visualmente do que alternar o dropdown da sidebar repetidamente.
    """
    trend_icon = {"UP": "▲", "DOWN": "▼", "NEUTRAL": "●"}
    trend_color_map = {"UP": ACCENT_UP, "DOWN": ACCENT_DOWN, "NEUTRAL": TEXT_MUTED}

    cards_html = []
    for ticker in tickers:
        try:
            raw = fetch_market_data_cached(ticker, period=period)
            enriched = calculate_moving_averages(raw)
            payload = build_prediction_payload(ticker, enriched)
        except MarketDataUnavailableError:
            cards_html.append(
                f'<div class="tp-card"><div class="tp-card-label">{ticker}</div>'
                f'<div class="tp-card-value" style="font-size:1rem;color:{TEXT_MUTED};">Indisponível</div></div>'
            )
            continue

        color = trend_color_map.get(payload.trend, TEXT_MUTED)
        icon = trend_icon.get(payload.trend, "●")
        badge = "🤖 modelo" if payload.source == "model" else "⚠️ heurística"
        delta = payload.predicted_price - payload.current_price

        cards_html.append(
            f"""
            <div class="tp-card">
                <div class="tp-card-label">{ticker}</div>
                <div class="tp-card-value">${payload.current_price:,.2f}</div>
                <div class="tp-card-delta" style="color:{color};">
                    {icon} {payload.trend} · {delta:+.2f}
                </div>
                <div style="margin-top:8px;font-size:0.72rem;color:{TEXT_MUTED};">
                    confiança {payload.confidence * 100:.0f}% · {badge}
                </div>
            </div>
            """
        )

    st.markdown(f'<div class="tp-card-grid">{"".join(cards_html)}</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Interface Streamlit
# --------------------------------------------------------------------------- #

def main() -> None:
    st.sidebar.markdown(
        '<div class="tp-display" style="font-size:1.5rem;font-weight:700;">📈 TrendPulse AI</div>',
        unsafe_allow_html=True,
    )
    st.sidebar.caption("Plataforma reativa de análise preditiva de mercados")
    st.sidebar.divider()

    comparison_mode = st.sidebar.checkbox(
        "📊 Comparar vários ativos",
        value=False,
        help="Mostra um grid comparativo de vários ativos em simultâneo, "
        "em vez da análise detalhada de um único ticker.",
    )

    ticker = st.sidebar.selectbox(
        "Ativo",
        options=["AAPL", "BTC-USD", "MSFT", "TSLA", "ETH-USD"],
        index=0,
        disabled=comparison_mode,
    )
    period = st.sidebar.selectbox(
        "Período histórico",
        options=["3mo", "6mo", "1y", "2y"],
        index=1,
    )

    st.sidebar.divider()

    with st.sidebar.expander("🐇 Ligação RabbitMQ"):
        rabbitmq_host = st.text_input("Host", value=os.environ.get("RABBITMQ_HOST", "localhost"))
        rabbitmq_port = st.number_input(
            "Porta", value=int(os.environ.get("RABBITMQ_PORT", 5672)), min_value=1, max_value=65535
        )
        rabbitmq_user = st.text_input("Utilizador", value=os.environ.get("RABBITMQ_USER", "guest"))
        rabbitmq_pass = st.text_input(
            "Password", value=os.environ.get("RABBITMQ_PASS", "guest"), type="password"
        )

    with st.sidebar.expander("🔌 Core Backend (WebSocket)", expanded=True):
        backend_url = st.text_input(
            "URL base",
            value=os.environ.get("BACKEND_PUBLIC_URL", "http://localhost:8080"),
            help="Endereço onde o core-backend Spring Boot está a correr, "
            "**tal como acessível a partir do teu browser** (o cliente WebSocket "
            "corre no browser, não dentro do container do dashboard).",
        )

    # --- Cabeçalho: título + pill "ao vivo" ---
    st.markdown(
        f"""
        <div class="tp-header">
            <div class="tp-display" style="font-size:2rem;font-weight:700;">
                {ticker if not comparison_mode else "Visão Geral do Mercado"}
                <span style="color:{TEXT_MUTED};font-weight:500;font-size:1.1rem;"> — Análise Técnica</span>
            </div>
            <div class="tp-live-pill"><span class="tp-pulse-dot"></span> AO VIVO</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --- Fita de cotações (elemento assinatura) ---
    ticker_tape_html = build_ticker_tape(DEFAULT_COMPARISON_TICKERS)
    if ticker_tape_html:
        st.markdown(ticker_tape_html, unsafe_allow_html=True)

    if comparison_mode:
        render_multi_asset_grid(DEFAULT_COMPARISON_TICKERS, period)
        st.caption(
            "Modo de comparação ativo — desmarca '📊 Comparar vários ativos' na "
            "sidebar para veres a análise detalhada de um único ativo."
        )
        return

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
            _fetch_market_data_short_ttl.clear()
            _fetch_market_data_long_ttl.clear()
            st.rerun()
        return

    payload = build_prediction_payload(ticker, enriched_data)

    badge_class = "tp-badge-model" if payload.source == "model" else "tp-badge-heuristic"
    badge_text = "🤖 Modelo RandomForestRegressor" if payload.source == "model" else "⚠️ Heurística de fallback (SMA)"
    st.markdown(f'<span class="tp-badge {badge_class}">{badge_text}</span>', unsafe_allow_html=True)
    if payload.source != "model":
        st.caption("Corre `python ml-engine/train_model.py` para ativar previsões reais de ML.")

    st.markdown("<div style='height: 0.6rem'></div>", unsafe_allow_html=True)

    delta = payload.predicted_price - payload.current_price
    render_metric_cards([
        {"label": "Preço Atual", "value": f"${payload.current_price:,.2f}"},
        {
            "label": "Preço Previsto",
            "value": f"${payload.predicted_price:,.2f}",
            "delta": f"{delta:+.2f}",
            "delta_positive": delta >= 0,
        },
        {"label": "Tendência", "value": payload.trend},
        {"label": "Confiança do Modelo", "value": f"{payload.confidence * 100:.0f}%"},
    ])

    tab_analysis, tab_live, tab_accuracy, tab_publish = st.tabs(
        ["📈 Análise Técnica", "📡 Ao Vivo", "🎯 Accuracy", "📤 Publicar"]
    )

    with tab_analysis:
        gauge_col, _ = st.columns([1, 3])
        with gauge_col:
            st.plotly_chart(render_confidence_gauge(payload.confidence), use_container_width=True)

        fig = render_candlestick_chart(enriched_data, ticker, prediction=payload)
        st.plotly_chart(fig, use_container_width=True)

    with tab_live:
        st.caption(
            "Este painel liga-se diretamente ao `core-backend` via STOMP/SockJS e "
            "atualiza-se sozinho assim que uma nova previsão é publicada — sem "
            "recarregar a página. Publica uma previsão (aba '📤 Publicar') e "
            "observa esta secção reagir em tempo real."
        )
        components.html(
            build_realtime_widget_html(backend_url, ticker),
            height=340,
            scrolling=False,
        )

    with tab_accuracy:
        st.caption(
            "Compara o preço previsto com o preço real observado no dia seguinte, "
            "usando o histórico persistido em TimescaleDB. Só aparecem aqui "
            "previsões já avaliadas pelo job diário `AccuracyEvaluationService` "
            "(Java) — previsões muito recentes ainda não têm um 'dia seguinte' "
            "para comparar."
        )

        accuracy_stats = fetch_accuracy_stats(backend_url, ticker)
        history_df = fetch_prediction_history(backend_url, ticker, size=200)

        if accuracy_stats is None or history_df is None:
            st.info(
                "⚠️ Não foi possível ligar ao Core Backend para obter o histórico "
                "de accuracy. Confirma que o `core-backend` está a correr e que "
                "a URL na sidebar está correta."
            )
        elif accuracy_stats["totalEvaluated"] == 0:
            st.info(
                "Ainda não há previsões avaliadas para este ativo. O job diário "
                "de accuracy corre às 22:00 — publica algumas previsões na aba "
                "'📤 Publicar' e volta amanhã, ou ajusta `trendpulse.accuracy.cron` "
                "para testar mais cedo."
            )
        else:
            render_metric_cards([
                {"label": "Previsões Avaliadas", "value": str(accuracy_stats["totalEvaluated"])},
                {"label": "Acertos de Direção", "value": str(accuracy_stats["correctDirection"])},
                {"label": "Accuracy", "value": f"{accuracy_stats['accuracyRate'] * 100:.0f}%"},
            ])

            if history_df is not None and not history_df.empty:
                st.plotly_chart(render_accuracy_chart(history_df, ticker), use_container_width=True)

    with tab_publish:
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
