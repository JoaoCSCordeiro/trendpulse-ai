package com.trendpulse.core.model;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.Instant;

/**
 * DTO que representa uma previsão de mercado gerada pelo ML Engine (Python)
 * e transportada via RabbitMQ até ao Core Backend, que a reencaminha para
 * os clientes WebSocket subscritos.
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class PredictionMessage {

    /** Símbolo do ativo, ex: "AAPL", "BTC-USD". */
    private String symbol;

    /** Preço atual observado no momento da previsão. */
    private double currentPrice;

    /** Preço previsto pelo modelo (ex: scikit-learn) para o próximo período. */
    private double predictedPrice;

    /** Direção da tendência: "UP", "DOWN" ou "NEUTRAL". */
    private String trend;

    /** Grau de confiança do modelo, entre 0.0 e 1.0. */
    private double confidence;

    /** Timestamp (UTC) de geração da previsão. */
    private Instant generatedAt;

}
