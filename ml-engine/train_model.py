"""
Treino do modelo preditivo do TrendPulse AI.

Treina um `RandomForestRegressor` para prever o retorno percentual do dia
seguinte (`next_return`), usando um conjunto diversificado de ativos para
que o modelo generalize entre diferentes perfis de mercado (ações vs.
criptomoedas, alta vs. baixa volatilidade).

Uso:
    cd ml-engine
    python train_model.py
    python train_model.py --tickers AAPL MSFT BTC-USD --period 2y

O modelo treinado e a lista de features usadas são serializados em
`models/price_predictor.joblib`, pronto a ser carregado por `predict.py`
ou diretamente pelo dashboard.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import joblib
import pandas as pd
import yfinance as yf
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split

from features import FEATURE_COLUMNS, TARGET_COLUMN, build_training_frame

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trendpulse.train")

DEFAULT_TICKERS = ["AAPL", "MSFT", "TSLA", "BTC-USD", "ETH-USD"]
MODEL_PATH = Path(__file__).parent / "models" / "price_predictor.joblib"


def collect_training_data(tickers: list[str], period: str) -> pd.DataFrame:
    """Descarrega e concatena os dados de treino de vários ativos.

    Treinar com múltiplos ativos em simultâneo (em vez de um modelo por
    ativo) dá ao `RandomForestRegressor` exemplos suficientes para aprender
    padrões de mercado mais gerais, em vez de decorar o histórico de um
    único ticker.
    """
    frames = []
    for ticker in tickers:
        logger.info("A descarregar histórico de %s (período=%s)...", ticker, period)
        raw = yf.download(ticker, period=period, interval="1d", progress=False)

        if raw.empty:
            logger.warning("Sem dados para %s — a ignorar.", ticker)
            continue

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        training_frame = build_training_frame(raw)
        training_frame["ticker"] = ticker
        frames.append(training_frame)
        logger.info("  -> %d amostras válidas extraídas de %s", len(training_frame), ticker)

    if not frames:
        raise RuntimeError("Nenhum dado de treino foi obtido para os tickers fornecidos.")

    return pd.concat(frames, ignore_index=True)


def train(tickers: list[str], period: str, n_estimators: int, test_size: float) -> None:
    dataset = collect_training_data(tickers, period)
    logger.info("Dataset combinado: %d amostras no total.", len(dataset))

    X = dataset[FEATURE_COLUMNS]
    y = dataset[TARGET_COLUMN]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=42, shuffle=True
    )

    model = RandomForestRegressor(
        n_estimators=n_estimators,
        max_depth=6,
        min_samples_leaf=10,
        random_state=42,
        n_jobs=-1,
    )

    logger.info("A treinar RandomForestRegressor (n_estimators=%d)...", n_estimators)
    model.fit(X_train, y_train)

    train_mae = mean_absolute_error(y_train, model.predict(X_train))
    test_mae = mean_absolute_error(y_test, model.predict(X_test))
    logger.info("MAE (treino): %.5f | MAE (teste): %.5f", train_mae, test_mae)

    # Importância relativa de cada feature — útil para justificar o modelo
    # na defesa/portfólio ("o modelo aprendeu a dar mais peso a X").
    importances = pd.Series(model.feature_importances_, index=FEATURE_COLUMNS).sort_values(
        ascending=False
    )
    logger.info("Importância das features:\n%s", importances.to_string())

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "feature_columns": FEATURE_COLUMNS,
            "trained_on": tickers,
            "period": period,
            "test_mae": test_mae,
        },
        MODEL_PATH,
    )
    logger.info("Modelo guardado em %s", MODEL_PATH)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Treina o modelo preditivo do TrendPulse AI.")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument("--period", default="2y")
    parser.add_argument("--n-estimators", type=int, default=300)
    parser.add_argument("--test-size", type=float, default=0.2)
    args = parser.parse_args()

    train(args.tickers, args.period, args.n_estimators, args.test_size)
