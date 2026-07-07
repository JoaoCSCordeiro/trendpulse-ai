"""
Camada de inferência do TrendPulse AI.

Carrega o modelo treinado por `train_model.py` (`models/price_predictor.joblib`)
e expõe uma função única, `predict_next_return`, que o dashboard (ou
qualquer outro consumidor) pode chamar com dados OHLCV brutos.

Se o modelo ainda não tiver sido treinado, `load_model()` devolve `None` e
os consumidores devem cair num fallback heurístico (ver `dashboard/app.py`),
para que a aplicação nunca fique bloqueada por falta do artefacto de ML.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd

from features import extract_latest_features

logger = logging.getLogger("trendpulse.predict")

MODEL_PATH = Path(__file__).parent / "models" / "price_predictor.joblib"

# Confiança é limitada a este intervalo para evitar extremos pouco
# informativos (100% de confiança é irrealista para previsão de mercado;
# <50% seria pior que uma moeda ao ar).
MIN_CONFIDENCE = 0.50
MAX_CONFIDENCE = 0.95


@dataclass
class ModelPrediction:
    predicted_return: float
    predicted_price: float
    trend: str
    confidence: float
    model_version: str


def load_model() -> Optional[dict]:
    """Carrega o artefacto do modelo (dict com model + metadata), se existir."""
    if not MODEL_PATH.exists():
        logger.warning(
            "Nenhum modelo encontrado em %s. Corre `python train_model.py` primeiro.",
            MODEL_PATH,
        )
        return None

    try:
        return joblib.load(MODEL_PATH)
    except Exception:
        logger.exception("Falha ao carregar o modelo em %s", MODEL_PATH)
        return None


def _confidence_from_tree_variance(model, features_row: pd.DataFrame) -> float:
    """Deriva um score de confiança a partir da variância entre as árvores da floresta.

    Cada árvore do `RandomForestRegressor` faz a sua própria previsão; se
    todas concordarem (baixo desvio-padrão), a confiança é alta. Se
    discordarem muito, a confiança é baixa. Isto dá um sinal de incerteza
    muito mais informativo do que um valor fixo.
    """
    tree_predictions = np.array(
        [tree.predict(features_row.to_numpy())[0] for tree in model.estimators_]
    )
    std = tree_predictions.std()

    # Mapeamento heurístico: desvio-padrão baixo -> confiança perto do máximo;
    # desvio-padrão alto -> confiança perto do mínimo. O fator de escala (40)
    # foi calibrado empiricamente para a ordem de grandeza de retornos diários (~1%).
    raw_confidence = MAX_CONFIDENCE - (std * 40)
    return float(np.clip(raw_confidence, MIN_CONFIDENCE, MAX_CONFIDENCE))


def predict_next_return(
    df: pd.DataFrame, trend_threshold: float = 0.001
) -> Optional[ModelPrediction]:
    """Prevê o retorno do dia seguinte para o ativo representado em `df`.

    Args:
        df: DataFrame OHLCV bruto (como devolvido por `yfinance`), com
            histórico suficiente (idealmente >= 60 dias).
        trend_threshold: retorno mínimo (em valor absoluto) para classificar
            a tendência como UP/DOWN em vez de NEUTRAL.

    Returns:
        `ModelPrediction` com o retorno previsto, preço previsto, tendência
        e confiança — ou `None` se o modelo não estiver disponível ou o
        histórico for insuficiente.
    """
    artifact = load_model()
    if artifact is None:
        return None

    model = artifact["model"]
    feature_columns = artifact["feature_columns"]

    try:
        features_row = extract_latest_features(df)[feature_columns]
    except ValueError as exc:
        logger.warning("Não foi possível gerar features: %s", exc)
        return None

    predicted_return = float(model.predict(features_row)[0])
    current_price = float(df["Close"].iloc[-1])
    predicted_price = current_price * (1 + predicted_return)

    if predicted_return > trend_threshold:
        trend = "UP"
    elif predicted_return < -trend_threshold:
        trend = "DOWN"
    else:
        trend = "NEUTRAL"

    confidence = _confidence_from_tree_variance(model, features_row)

    return ModelPrediction(
        predicted_return=predicted_return,
        predicted_price=predicted_price,
        trend=trend,
        confidence=confidence,
        model_version=str(artifact.get("period", "unknown")),
    )
