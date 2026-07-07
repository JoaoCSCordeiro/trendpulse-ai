"""Testes unitários para `ml-engine/predict.py`.

Treina um modelo minúsculo em memória (sem depender de `train_model.py` nem
de rede) para validar o comportamento da camada de inferência: previsão
válida quando há histórico suficiente, fallback gracioso (`None`) quando o
modelo não existe, e classificação correta da tendência (UP/DOWN/NEUTRAL).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict as predict_module
from features import FEATURE_COLUMNS, build_training_frame


@pytest.fixture
def synthetic_ohlcv() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame(
        {
            "Open": close + rng.normal(0, 0.1, n),
            "High": close + abs(rng.normal(0, 0.5, n)),
            "Low": close - abs(rng.normal(0, 0.5, n)),
            "Close": close,
            "Volume": rng.integers(1_000_000, 5_000_000, n),
        },
        index=dates,
    )


@pytest.fixture
def trained_artifact(synthetic_ohlcv):
    """Treina um RandomForest minúsculo, só para efeitos de teste."""
    from sklearn.ensemble import RandomForestRegressor

    training_frame = build_training_frame(synthetic_ohlcv)
    X, y = training_frame[FEATURE_COLUMNS], training_frame["next_return"]

    model = RandomForestRegressor(n_estimators=20, random_state=0).fit(X, y)
    return {"model": model, "feature_columns": FEATURE_COLUMNS, "period": "test"}


def test_load_model_returns_none_when_missing(monkeypatch, tmp_path):
    fake_path = tmp_path / "does_not_exist.joblib"
    monkeypatch.setattr(predict_module, "MODEL_PATH", fake_path)
    assert predict_module.load_model() is None


def test_predict_next_return_returns_none_without_model(monkeypatch, synthetic_ohlcv, tmp_path):
    monkeypatch.setattr(predict_module, "MODEL_PATH", tmp_path / "missing.joblib")
    result = predict_module.predict_next_return(synthetic_ohlcv)
    assert result is None


def test_predict_next_return_returns_prediction_with_model(
    monkeypatch, synthetic_ohlcv, trained_artifact
):
    monkeypatch.setattr(predict_module, "load_model", lambda: trained_artifact)
    result = predict_module.predict_next_return(synthetic_ohlcv)

    assert result is not None
    assert isinstance(result.predicted_return, float)
    assert result.predicted_price > 0
    assert result.trend in {"UP", "DOWN", "NEUTRAL"}
    assert predict_module.MIN_CONFIDENCE <= result.confidence <= predict_module.MAX_CONFIDENCE


@pytest.mark.parametrize(
    "predicted_return,expected_trend",
    [(0.01, "UP"), (-0.01, "DOWN"), (0.0001, "NEUTRAL")],
)
def test_trend_classification_thresholds(
    monkeypatch, synthetic_ohlcv, trained_artifact, predicted_return, expected_trend
):
    # Substitui a previsão do modelo por um valor fixo para testar isoladamente
    # a lógica de classificação de tendência (UP/DOWN/NEUTRAL).
    class StubModel:
        estimators_ = trained_artifact["model"].estimators_

        def predict(self, X):
            return np.array([predicted_return])

    stub_artifact = {**trained_artifact, "model": StubModel()}
    monkeypatch.setattr(predict_module, "load_model", lambda: stub_artifact)

    result = predict_module.predict_next_return(synthetic_ohlcv, trend_threshold=0.001)
    assert result.trend == expected_trend


def test_predict_next_return_handles_insufficient_history(monkeypatch, trained_artifact):
    monkeypatch.setattr(predict_module, "load_model", lambda: trained_artifact)

    short_df = pd.DataFrame(
        {
            "Open": [100] * 5,
            "High": [101] * 5,
            "Low": [99] * 5,
            "Close": [100] * 5,
            "Volume": [1_000_000] * 5,
        },
        index=pd.date_range("2024-01-01", periods=5, freq="B"),
    )
    result = predict_module.predict_next_return(short_df)
    assert result is None
