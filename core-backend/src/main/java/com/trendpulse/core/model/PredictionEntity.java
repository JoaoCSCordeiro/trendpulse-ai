package com.trendpulse.core.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.Instant;

/**
 * Entidade JPA que representa uma previsão persistida na base de dados
 * ({@code predictions}, convertida em hipertabela TimescaleDB — ver
 * {@link com.trendpulse.core.config.TimescaleDbInitializer}).
 * <p>
 * Cada mensagem consumida da queue {@code market.predictions.queue} é
 * gravada aqui pelo {@link com.trendpulse.core.listener.MarketDataListener},
 * antes/depois de ser difundida via WebSocket. Isto cria um histórico
 * permanente de previsões, que permite no futuro comparar previsto vs. real
 * e calcular a "accuracy" do modelo ao longo do tempo — algo que, com o
 * fluxo anterior (apenas em memória), era impossível.
 * <p>
 * Os campos {@code actualPrice} e {@code actualReturn} ficam nulos no
 * momento da previsão e destinam-se a ser preenchidos por um job futuro
 * (fora do âmbito desta fase) que compara a previsão com o preço real
 * observado no dia seguinte.
 */
@Entity
@Table(
        name = "predictions",
        indexes = {
                @Index(name = "idx_predictions_symbol", columnList = "symbol"),
                @Index(name = "idx_predictions_generated_at", columnList = "generated_at")
        }
)
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class PredictionEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, length = 20)
    private String symbol;

    @Column(name = "current_price", nullable = false)
    private double currentPrice;

    @Column(name = "predicted_price", nullable = false)
    private double predictedPrice;

    @Column(nullable = false, length = 10)
    private String trend;

    @Column(nullable = false)
    private double confidence;

    /**
     * Coluna de particionamento temporal da hipertable TimescaleDB — todas
     * as queries e agregações por intervalo de tempo (ex: "previsões dos
     * últimos 7 dias") tiram partido do particionamento automático nesta
     * coluna, mesmo à medida que a tabela cresce para milhões de linhas.
     */
    @Column(name = "generated_at", nullable = false)
    private Instant generatedAt;

    /** Preço real observado no dia seguinte (preenchido posteriormente). */
    @Column(name = "actual_price")
    private Double actualPrice;

    /** Retorno real observado (preenchido posteriormente, para cálculo de accuracy). */
    @Column(name = "actual_return")
    private Double actualReturn;

}
