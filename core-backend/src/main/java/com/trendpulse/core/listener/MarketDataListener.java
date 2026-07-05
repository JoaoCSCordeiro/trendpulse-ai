package com.trendpulse.core.listener;

import com.trendpulse.core.config.RabbitMQConfig;
import com.trendpulse.core.config.WebSocketConfig;
import com.trendpulse.core.model.PredictionMessage;
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
 * e reencaminhada em tempo real para todos os clientes subscritos no
 * tópico WebSocket correspondente ao símbolo do ativo.
 * <p>
 * Isto materializa o fluxo reativo: Python (ML) -> RabbitMQ -> Spring Boot -> WebSocket -> Cliente.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class MarketDataListener {

    private final SimpMessagingTemplate messagingTemplate;

    @RabbitListener(queues = RabbitMQConfig.QUEUE_NAME)
    public void handlePrediction(PredictionMessage prediction) {
        log.info("Previsão recebida do ML Engine: {} -> preço previsto={} (confiança={})",
                prediction.getSymbol(), prediction.getPredictedPrice(), prediction.getConfidence());

        // Destino dinâmico por símbolo, ex: /topic/predictions/AAPL
        String destination = WebSocketConfig.TOPIC_PREFIX + "/predictions/" + prediction.getSymbol();

        messagingTemplate.convertAndSend(destination, prediction);

        log.debug("Previsão difundida via WebSocket em {}", destination);
    }

}
