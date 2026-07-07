package com.trendpulse.core.listener;

import com.trendpulse.core.config.RabbitMQConfig;
import com.trendpulse.core.config.WebSocketConfig;
import com.trendpulse.core.model.PredictionEntity;
import com.trendpulse.core.model.PredictionMessage;
import com.trendpulse.core.repository.PredictionRepository;
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
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class MarketDataListener {

    private final SimpMessagingTemplate messagingTemplate;
    private final PredictionRepository predictionRepository;

    @RabbitListener(queues = RabbitMQConfig.QUEUE_NAME)
    public void handlePrediction(PredictionMessage prediction) {
        validate(prediction);

        log.info("Previsão recebida do ML Engine: {} -> preço previsto={} (confiança={})",
                prediction.getSymbol(), prediction.getPredictedPrice(), prediction.getConfidence());

        try {
            persist(prediction);

            // Destino dinâmico por símbolo, ex: /topic/predictions/AAPL
            String destination = WebSocketConfig.TOPIC_PREFIX + "/predictions/" + prediction.getSymbol();
            messagingTemplate.convertAndSend(destination, prediction);

            log.debug("Previsão persistida e difundida via WebSocket em {}", destination);
        } catch (Exception ex) {
            // Não engolir a exceção: deixamos propagar para que o mecanismo de
            // retry do Spring AMQP a intercete. Se todas as tentativas falharem,
            // a mensagem é automaticamente reencaminhada para a DLQ.
            log.error("Falha ao processar a previsão de {}. A mensagem será " +
                    "reprocessada (retry) e, se persistir, movida para a DLQ.",
                    prediction.getSymbol(), ex);
            throw new IllegalStateException("Falha ao processar previsão de " + prediction.getSymbol(), ex);
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
