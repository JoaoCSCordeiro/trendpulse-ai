package com.trendpulse.core.listener;

import com.trendpulse.core.model.PredictionEntity;
import com.trendpulse.core.model.PredictionMessage;
import com.trendpulse.core.repository.PredictionRepository;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.messaging.simp.SimpMessagingTemplate;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

/**
 * Testes unitários para {@link MarketDataListener}, isolando a lógica de
 * negócio (validação, persistência, difusão WebSocket) de qualquer
 * infraestrutura real (RabbitMQ, TimescaleDB) através de mocks Mockito.
 * <p>
 * Isto é deliberadamente um teste unitário, não um teste de integração
 * {@code @SpringBootTest} — corre em milissegundos e não exige nenhum
 * serviço externo a correr, o que o torna adequado para correr em CI a
 * cada commit.
 */
@ExtendWith(MockitoExtension.class)
class MarketDataListenerTest {

    @Mock
    private SimpMessagingTemplate messagingTemplate;

    @Mock
    private PredictionRepository predictionRepository;

    // Registo real (não mock) — Micrometer precisa de um MeterRegistry
    // funcional para criar/atualizar Counters e Timers; um mock exigiria
    // stubbing extensivo sem trazer nenhum benefício de teste.
    private MeterRegistry meterRegistry;

    private MarketDataListener listener;

    @BeforeEach
    void setUp() {
        meterRegistry = new SimpleMeterRegistry();
        listener = new MarketDataListener(messagingTemplate, predictionRepository, meterRegistry);
    }

    private PredictionMessage validPrediction() {
        return new PredictionMessage(
                "AAPL", 195.42, 197.10, "UP", 0.65, Instant.now()
        );
    }

    @Test
    void handlePrediction_persistsAndBroadcastsValidMessage() {
        PredictionMessage prediction = validPrediction();

        listener.handlePrediction(prediction);

        // Verifica que foi persistido com os campos corretos
        ArgumentCaptor<PredictionEntity> entityCaptor = ArgumentCaptor.forClass(PredictionEntity.class);
        verify(predictionRepository).save(entityCaptor.capture());
        PredictionEntity savedEntity = entityCaptor.getValue();
        assertThat(savedEntity.getSymbol()).isEqualTo("AAPL");
        assertThat(savedEntity.getPredictedPrice()).isEqualTo(197.10);
        assertThat(savedEntity.getTrend()).isEqualTo("UP");

        // Verifica que foi difundido no tópico WebSocket correto para o símbolo
        verify(messagingTemplate).convertAndSend(eq("/topic/predictions/AAPL"), eq(prediction));
    }

    @Test
    void handlePrediction_recordsProcessedCounterAndLatencyTimers() {
        PredictionMessage prediction = validPrediction();

        listener.handlePrediction(prediction);

        double processedCount = meterRegistry.counter(
                "trendpulse.predictions.processed", "symbol", "AAPL", "trend", "UP"
        ).count();
        assertThat(processedCount).isEqualTo(1.0);

        assertThat(meterRegistry.find("trendpulse.prediction.total.duration").timer())
                .as("timer de latência end-to-end deve existir após processar uma previsão")
                .isNotNull();
        assertThat(meterRegistry.find("trendpulse.prediction.total.duration").timer().count())
                .isEqualTo(1);

        assertThat(meterRegistry.find("trendpulse.prediction.persist.duration").tag("symbol", "AAPL").timer())
                .as("timer de latência de persistência deve existir, tagged por símbolo")
                .isNotNull();

        assertThat(meterRegistry.find("trendpulse.prediction.broadcast.duration").tag("symbol", "AAPL").timer())
                .as("timer de latência de broadcast deve existir, tagged por símbolo")
                .isNotNull();
    }

    @Test
    void handlePrediction_recordsFailedCounterWhenValidationFails() {
        PredictionMessage invalid = new PredictionMessage(
                "TSLA", 100.0, 101.0, "UP", 1.5, Instant.now()
        );

        assertThatThrownBy(() -> listener.handlePrediction(invalid))
                .isInstanceOf(IllegalArgumentException.class);

        double failedCount = meterRegistry.counter("trendpulse.predictions.failed", "symbol", "TSLA").count();
        assertThat(failedCount).isEqualTo(1.0);

        // Uma previsão inválida nunca deve incrementar o contador de sucesso.
        double processedCount = meterRegistry.counter(
                "trendpulse.predictions.processed", "symbol", "TSLA", "trend", "UP"
        ).count();
        assertThat(processedCount).isZero();
    }

    @Test
    void handlePrediction_rejectsNullSymbol() {
        PredictionMessage invalid = new PredictionMessage(
                null, 100.0, 101.0, "UP", 0.6, Instant.now()
        );

        assertThatThrownBy(() -> listener.handlePrediction(invalid))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("symbol");

        verify(predictionRepository, never()).save(any());
        verify(messagingTemplate, never()).convertAndSend(any(String.class), any(Object.class));
    }

    @Test
    void handlePrediction_rejectsBlankSymbol() {
        PredictionMessage invalid = new PredictionMessage(
                "   ", 100.0, 101.0, "UP", 0.6, Instant.now()
        );

        assertThatThrownBy(() -> listener.handlePrediction(invalid))
                .isInstanceOf(IllegalArgumentException.class);

        verify(predictionRepository, never()).save(any());
    }

    @Test
    void handlePrediction_rejectsConfidenceAboveOne() {
        PredictionMessage invalid = new PredictionMessage(
                "AAPL", 100.0, 101.0, "UP", 1.5, Instant.now()
        );

        assertThatThrownBy(() -> listener.handlePrediction(invalid))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Confiança");

        verify(predictionRepository, never()).save(any());
    }

    @Test
    void handlePrediction_rejectsNegativeConfidence() {
        PredictionMessage invalid = new PredictionMessage(
                "AAPL", 100.0, 101.0, "DOWN", -0.1, Instant.now()
        );

        assertThatThrownBy(() -> listener.handlePrediction(invalid))
                .isInstanceOf(IllegalArgumentException.class);

        verify(predictionRepository, never()).save(any());
    }

    @Test
    void handlePrediction_propagatesExceptionWhenBroadcastFails() {
        PredictionMessage prediction = validPrediction();

        doThrow(new RuntimeException("Falha simulada de rede"))
                .when(messagingTemplate)
                .convertAndSend(any(String.class), any(Object.class));

        // A exceção deve propagar (não ser engolida), para que o retry do
        // Spring AMQP a possa interceptar e, eventualmente, mover a
        // mensagem para a Dead-Letter Queue.
        assertThatThrownBy(() -> listener.handlePrediction(prediction))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("AAPL");

        // A persistência já deve ter acontecido antes da falha de broadcast
        verify(predictionRepository).save(any());
    }

}
