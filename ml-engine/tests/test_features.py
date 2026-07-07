"""Testes unitários para `ml-engine/features.py`.

Usa dados OHLCV sintéticos (sem dependência de rede/yfinance) para validar
que a engenharia de features produz colunas corretas, sem NaNs residuais
onde não deveria haver, e que o dataset de treino/inferência tem a forma
esperada.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from features import (
    FEATURE_COLUMNS,
    TARGET_COLUMN,
    build_features,
    build_training_frame,
    extract_latest_features,
)


@pytest.fixture
def synthetic_ohlcv() -> pd.DataFrame:
    """Gera um DataFrame OHLCV sintético e determinístico (seed fixa)."""
    rng = np.random.default_rng(42)
    n = 120
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


def test_build_features_adds_expected_columns(synthetic_ohlcv):
    enriched = build_features(synthetic_ohlcv)
    for column in FEATURE_COLUMNS:
        assert column in enriched.columns, f"Feature '{column}' em falta no output"


def test_build_features_preserves_row_count(synthetic_ohlcv):
    enriched = build_features(synthetic_ohlcv)
    assert len(enriched) == len(synthetic_ohlcv)


def test_build_training_frame_has_no_nans(synthetic_ohlcv):
    training_frame = build_training_frame(synthetic_ohlcv)
    assert not training_frame.isna().any().any(), "Dataset de treino não deve conter NaNs"


def test_build_training_frame_has_expected_columns(synthetic_ohlcv):
    training_frame = build_training_frame(synthetic_ohlcv)
    expected = set(FEATURE_COLUMNS + [TARGET_COLUMN])
    assert set(training_frame.columns) == expected


def test_build_training_frame_drops_rows_without_enough_history(synthetic_ohlcv):
    # SMA_50 exige 50 dias de histórico; a linha 0 nunca deve sobreviver ao dropna.
    training_frame = build_training_frame(synthetic_ohlcv)
    assert training_frame.index[0] > synthetic_ohlcv.index[48]


def test_extract_latest_features_returns_single_row(synthetic_ohlcv):
    latest = extract_latest_features(synthetic_ohlcv)
    assert len(latest) == 1
    assert list(latest.columns) == FEATURE_COLUMNS
    assert latest.index[0] == synthetic_ohlcv.index[-1]


def test_extract_latest_features_raises_on_insufficient_history():
    # Só 10 dias de histórico — insuficiente para SMA_50 (precisa de 50).
    short_df = pd.DataFrame(
        {
            "Open": np.linspace(100, 110, 10),
            "High": np.linspace(101, 111, 10),
            "Low": np.linspace(99, 109, 10),
            "Close": np.linspace(100, 110, 10),
            "Volume": [1_000_000] * 10,
        },
        index=pd.date_range("2024-01-01", periods=10, freq="B"),
    )
    with pytest.raises(ValueError, match="Histórico insuficiente"):
        extract_latest_features(short_df)


def test_return_1d_matches_manual_calculation(synthetic_ohlcv):
    enriched = build_features(synthetic_ohlcv)
    expected_return = synthetic_ohlcv["Close"].pct_change(1)
    pd.testing.assert_series_equal(
        enriched["return_1d"], expected_return, check_names=False
    )
