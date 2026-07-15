package com.trendpulse.core.listener;

import com.trendpulse.core.config.RabbitMQConfig;
import com.trendpulse.core.config.WebSocketConfig;
import com.trendpulse.core.model.PredictionEntity;
import com.trendpulse.core.model.PredictionMessage;
import com.trendpulse.core.repository.PredictionRepository;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.messaging.simp.SimpMessagingTemplate;
import org.springframework.stereotype.Service;

/**
 * Consumidor da queue {@code market.predictions.queue}.
 * <p>
 * Cada mensagem publicada pelo ML Engine (Python) é recebida aqui,
 * desserializada automaticamente para {@link PredictionMessage} (via o
 * {@code Jackson2JsonMessageConverter} configurado em {@link RabbitMQConfig}),
 * validada, <b>persistida</b> na base de dados (TimescaleDB) e reencaminhada
 * em tempo real para todos os clientes subscritos no tópico WebSocket
 * correspondente ao símbolo do ativo.
 * <p>
 * Isto materializa o fluxo reativo completo:
 * Python (ML) -> RabbitMQ -> Spring Boot -> [TimescaleDB + WebSocket] -> Cliente.
 * <p>
 * <b>Resiliência:</b> se a mensagem for inválida (ex: campos essenciais em
 * falta) ou ocorrer um erro inesperado a persistir/publicar, o método lança
 * uma exceção em vez de a engolir silenciosamente. O
 * {@code SimpleRabbitListenerContainerFactory} (configurado em
 * {@code application.yml}) está preparado com retry automático; após
 * esgotar as tentativas, a mensagem é rejeitada e cai na Dead-Letter Queue
 * ({@code market.predictions.dlq}) em vez de ser perdida ou bloquear a fila.
 * <p>
 * <b>Observabilidade:</b> cada previsão processada é instrumentada com
 * métricas Micrometer, expostas em {@code /actuator/prometheus}:
 * <ul>
 *     <li>{@code trendpulse.prediction.total.duration} — latência end-to-end
 *         (receção da mensagem → persistência → difusão WebSocket), com
 *         histograma de percentis (p50/p95/p99)</li>
 *     <li>{@code trendpulse.prediction.persist.duration} /
 *         {@code trendpulse.prediction.broadcast.duration} — latência
 *         isolada de cada etapa, para identificar onde está o gargalo caso
 *         a latência total suba</li>
 *     <li>{@code trendpulse.predictions.processed} — contador (tagged por
 *         símbolo/tendência); combinado com {@code rate()} no Prometheus dá
 *         o throughput (previsões/segundo)</li>
 *     <li>{@code trendpulse.predictions.failed} — contador de falhas, para
 *         alertar se a taxa de erro subir</li>
 * </ul>
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class MarketDataListener {

    private final SimpMessagingTemplate messagingTemplate;
    private final PredictionRepository predictionRepository;
    private final MeterRegistry meterRegistry;

    @RabbitListener(queues = RabbitMQConfig.QUEUE_NAME)
    public void handlePrediction(PredictionMessage prediction) {
        Timer.Sample overallSample = Timer.start(meterRegistry);

        try {
            validate(prediction);

            log.info("Previsão recebida do ML Engine: {} -> preço previsto={} (confiança={})",
                    prediction.getSymbol(), prediction.getPredictedPrice(), prediction.getConfidence());

            timedStep(() -> persist(prediction), "trendpulse.prediction.persist.duration",
                    "Latência de escrita em TimescaleDB", prediction.getSymbol());

            String destination = WebSocketConfig.TOPIC_PREFIX + "/predictions/" + prediction.getSymbol();
            timedStep(() -> messagingTemplate.convertAndSend(destination, prediction),
                    "trendpulse.prediction.broadcast.duration",
                    "Latência de difusão via WebSocket", prediction.getSymbol());

            meterRegistry.counter(
                    "trendpulse.predictions.processed",
                    "symbol", prediction.getSymbol(),
                    "trend", prediction.getTrend()
            ).increment();

            log.debug("Previsão persistida e difundida via WebSocket em {}", destination);

        } catch (Exception ex) {
            meterRegistry.counter(
                    "trendpulse.predictions.failed",
                    "symbol", prediction != null && prediction.getSymbol() != null ? prediction.getSymbol() : "unknown"
            ).increment();

            // Não engolir a exceção: deixamos propagar para que o mecanismo de
            // retry do Spring AMQP a intercete. Se todas as tentativas falharem,
            // a mensagem é automaticamente reencaminhada para a DLQ.
            log.error("Falha ao processar a previsão de {}. A mensagem será " +
                    "reprocessada (retry) e, se persistir, movida para a DLQ.",
                    prediction != null ? prediction.getSymbol() : "desconhecido", ex);
            throw new IllegalStateException("Falha ao processar previsão de "
                    + (prediction != null ? prediction.getSymbol() : "desconhecido"), ex);
        } finally {
            overallSample.stop(Timer.builder("trendpulse.prediction.total.duration")
                    .description("Latência end-to-end: RabbitMQ -> persistência -> WebSocket")
                    .publishPercentileHistogram()
                    .publishPercentiles(0.5, 0.95, 0.99)
                    .register(meterRegistry));
        }
    }

    /**
     * Executa uma etapa do processamento (ex: persistência, broadcast) e
     * regista a sua duração isoladamente, sem duplicar o boilerplate de
     * {@code Timer.start()}/{@code stop()} em cada chamada.
     */
    private void timedStep(Runnable step, String metricName, String description, String symbol) {
        Timer.Sample sample = Timer.start(meterRegistry);
        try {
            step.run();
        } finally {
            sample.stop(Timer.builder(metricName)
                    .description(description)
                    .tag("symbol", symbol)
                    .publishPercentileHistogram()
                    .register(meterRegistry));
        }
    }

    /**
     * Grava a previsão na hipertable {@code predictions} (TimescaleDB), para
     * histórico permanente. Isto é o que permite, no futuro, calcular a
     * accuracy do modelo comparando `predictedPrice` com o preço real
     * observado no dia seguinte.
     */
    private void persist(PredictionMessage prediction) {
        PredictionEntity entity = PredictionEntity.builder()
                .symbol(prediction.getSymbol())
                .currentPrice(prediction.getCurrentPrice())
                .predictedPrice(prediction.getPredictedPrice())
                .trend(prediction.getTrend())
                .confidence(prediction.getConfidence())
                .generatedAt(prediction.getGeneratedAt())
                .build();

        predictionRepository.save(entity);
    }

    /**
     * Validação defensiva do payload recebido. Uma mensagem publicada por um
     * cliente Python malicioso ou com um bug (ex: symbol nulo, confiança fora
     * de [0,1]) não deve ser propagada nem persistida como se fosse válida —
     * é preferível falhar cedo e explicitamente.
     */
    private void validate(PredictionMessage prediction) {
        if (prediction == null) {
            throw new IllegalArgumentException("Mensagem de previsão nula recebida da queue.");
        }
        if (prediction.getSymbol() == null || prediction.getSymbol().isBlank()) {
            throw new IllegalArgumentException("Previsão recebida sem 'symbol' válido.");
        }
        if (prediction.getConfidence() < 0.0 || prediction.getConfidence() > 1.0) {
            throw new IllegalArgumentException(
                    "Confiança fora do intervalo [0,1] para " + prediction.getSymbol()
                            + ": " + prediction.getConfidence());
        }
    }

}
