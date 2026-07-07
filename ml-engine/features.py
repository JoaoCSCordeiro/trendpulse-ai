"""
Feature engineering partilhado entre o treino (`train_model.py`) e a
inferência (`predict.py` / dashboard).

Manter esta lógica num único módulo garante que o modelo é sempre treinado
e usado com exatamente as mesmas features — um dos erros mais comuns em
pipelines de ML é ter "training/serving skew" (features calculadas de forma
ligeiramente diferente em treino vs. produção).

Nota de desenho: as features são todas *relativas* (retornos, rácios,
osciladores limitados a 0-100) em vez de preços absolutos. Isto permite que
um único modelo generalize razoavelmente entre ativos com escalas de preço
muito diferentes (ex: AAPL ~$200 vs. BTC-USD ~$60000).
"""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta

# Nomes das colunas de features, pela ordem que o modelo espera.
# Manter esta lista sincronizada entre treino e inferência é crítico.
FEATURE_COLUMNS = [
    "return_1d",
    "return_5d",
    "sma20_ratio",
    "sma50_ratio",
    "rsi_14",
    "volatility_10d",
    "volume_ratio",
]

TARGET_COLUMN = "next_return"


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Deriva as features de séries temporais a partir de um DataFrame OHLCV.

    Args:
        df: DataFrame com, no mínimo, as colunas "Close" e "Volume",
            indexado por data (como devolvido por `yfinance`).

    Returns:
        Cópia do DataFrame original, enriquecida com as colunas definidas
        em `FEATURE_COLUMNS`. Linhas iniciais sem histórico suficiente para
        calcular todas as features (ex: SMA_50 precisa de 50 dias) ficam
        com NaN e devem ser removidas antes do treino/inferência.
    """
    out = df.copy()

    close = out["Close"]
    volume = out["Volume"]

    def _safe_indicator(series: pd.Series | None, index: pd.Index) -> pd.Series:
        """pandas_ta devolve `None` (em vez de uma série de NaNs) quando o
        histórico é demasiado curto para a janela pedida (ex: SMA de 50 dias
        com apenas 10 dias de dados). Normalizamos esse caso para uma série
        de NaNs, para que o resto do pipeline (dropna) funcione de forma
        previsível em vez de rebentar com um TypeError.
        """
        if series is None:
            return pd.Series(index=index, dtype="float64")
        return series

    # Retornos percentuais a 1 e 5 dias — captura momentum de curto prazo.
    out["return_1d"] = close.pct_change(1)
    out["return_5d"] = close.pct_change(5)

    # Posição do preço face às médias móveis, normalizada (ratio - 1),
    # em vez do preço absoluto da SMA.
    sma_20 = _safe_indicator(ta.sma(close, length=20), out.index)
    sma_50 = _safe_indicator(ta.sma(close, length=50), out.index)
    out["sma20_ratio"] = (close / sma_20) - 1
    out["sma50_ratio"] = (close / sma_50) - 1

    # RSI(14) — oscilador de força relativa, já naturalmente 0-100.
    out["rsi_14"] = _safe_indicator(ta.rsi(close, length=14), out.index)

    # Volatilidade recente: desvio-padrão dos retornos diários numa janela de 10 dias.
    out["volatility_10d"] = out["return_1d"].rolling(window=10).std()

    # Volume relativo à média móvel de 20 dias — deteta picos de atividade.
    volume_sma_20 = volume.rolling(window=20).mean()
    out["volume_ratio"] = volume / volume_sma_20

    return out


def build_training_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Constrói o dataset de treino: features + target (retorno do dia seguinte).

    O target `next_return` é o retorno percentual entre o fecho de hoje e o
    fecho de amanhã — prever um retorno relativo é mais estável e mais fácil
    de generalizar entre ativos do que prever um preço absoluto.

    Args:
        df: DataFrame OHLCV bruto de um único ativo.

    Returns:
        DataFrame só com as colunas de `FEATURE_COLUMNS` + `TARGET_COLUMN`,
        já sem linhas com NaN (início da série sem histórico suficiente, e
        a última linha, que não tem "dia seguinte" para calcular o target).
    """
    enriched = build_features(df)
    enriched[TARGET_COLUMN] = enriched["Close"].shift(-1) / enriched["Close"] - 1

    columns = FEATURE_COLUMNS + [TARGET_COLUMN]
    return enriched[columns].dropna()


def extract_latest_features(df: pd.DataFrame) -> pd.DataFrame:
    """Extrai a linha de features mais recente, pronta para `model.predict()`.

    Usado em inferência: queremos as features do último dia disponível
    (hoje), para prever o retorno de amanhã.

    Args:
        df: DataFrame OHLCV bruto, já com histórico suficiente (idealmente
            >= 60 dias, para que SMA_50 e o resto estejam preenchidos).

    Returns:
        DataFrame de uma única linha, com as colunas em `FEATURE_COLUMNS`.
    """
    enriched = build_features(df).dropna(subset=FEATURE_COLUMNS)
    if enriched.empty:
        raise ValueError(
            "Histórico insuficiente para calcular todas as features "
            "(é necessário pelo menos ~60 dias de dados)."
        )
    return enriched[FEATURE_COLUMNS].iloc[[-1]]
