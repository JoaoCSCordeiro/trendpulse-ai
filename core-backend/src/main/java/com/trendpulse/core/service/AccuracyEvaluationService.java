package com.trendpulse.core.service;

import com.trendpulse.core.client.YahooFinanceClient;
import com.trendpulse.core.dto.AccuracyStats;
import com.trendpulse.core.model.PredictionEntity;
import com.trendpulse.core.repository.PredictionRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Optional;

/**
 * Fecha o loop entre "previsão" e "realidade": compara o {@code predictedPrice}
 * de cada previsão com o preço de mercado realmente observado no dia
 * seguinte, e preenche {@code actualPrice}/{@code actualReturn} na entidade
 * persistida.
 * <p>
 * Sem este serviço, os campos {@code actualPrice}/{@code actualReturn} da
 * {@link PredictionEntity} ficariam para sempre nulos — o sistema saberia
 * "o que o modelo previu", mas nunca "se o modelo acertou". Este job é o que
 * transforma isso numa métrica real de accuracy (% de acertos de direção),
 * que é o número que efetivamente importa para avaliar se o modelo é útil.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class AccuracyEvaluationService {

    /**
     * Limiar de "maturidade": só avaliamos previsões feitas há mais de 1 dia,
     * para dar tempo ao mercado de fechar e ao preço do "dia seguinte" estar
     * de facto disponível.
     */
    private static final int MATURITY_THRESHOLD_DAYS = 1;

    /**
     * Threshold de retorno absoluto abaixo do qual uma previsão "NEUTRAL" é
     * considerada correta (mercados raramente ficam exatamente em 0.000%).
     */
    private static final double NEUTRAL_TOLERANCE = 0.002; // 0.2%

    private final PredictionRepository predictionRepository;
    private final YahooFinanceClient yahooFinanceClient;

    /**
     * Corre todos os dias às 22:00 (hora do servidor), depois do fecho dos
     * principais mercados de ações — altura em que o "preço de amanhã" já
     * é conhecido para as previsões feitas no dia anterior.
     * <p>
     * Cron configurável via {@code trendpulse.accuracy.cron} (ver application.yml),
     * para facilitar testar com uma cadência mais curta em desenvolvimento.
     */
    @Scheduled(cron = "${trendpulse.accuracy.cron:0 0 22 * * *}")
    public void evaluatePendingPredictions() {
        Instant threshold = Instant.now().minus(MATURITY_THRESHOLD_DAYS, ChronoUnit.DAYS);
        List<String> pendingSymbols = predictionRepository.findDistinctSymbolsPendingEvaluation(threshold);

        if (pendingSymbols.isEmpty()) {
            log.debug("Nenhuma previsão pendente de avaliação de accuracy.");
            return;
        }

        log.info("A avaliar accuracy para {} símbolo(s) com previsões pendentes: {}",
                pendingSymbols.size(), pendingSymbols);

        for (String symbol : pendingSymbols) {
            evaluateSymbol(symbol, threshold);
        }
    }

    private void evaluateSymbol(String symbol, Instant threshold) {
        Optional<Double> latestPrice = yahooFinanceClient.fetchLatestPrice(symbol);

        if (latestPrice.isEmpty()) {
            log.warn("Não foi possível obter o preço atual de {} — avaliação adiada para o próximo ciclo.", symbol);
            return;
        }

        double actualPrice = latestPrice.get();
        List<PredictionEntity> pending =
                predictionRepository.findBySymbolAndActualPriceIsNullAndGeneratedAtBefore(symbol, threshold);

        for (PredictionEntity prediction : pending) {
            double actualReturn = (actualPrice - prediction.getCurrentPrice()) / prediction.getCurrentPrice();
            prediction.setActualPrice(actualPrice);
            prediction.setActualReturn(actualReturn);
        }

        predictionRepository.saveAll(pending);
        log.info("Accuracy: {} previsão(ões) de {} avaliadas com preço real ${}.",
                pending.size(), symbol, actualPrice);
    }

    /**
     * Calcula as estatísticas de accuracy agregadas de um símbolo, a partir
     * de todas as previsões já avaliadas (com preço real conhecido).
     */
    public AccuracyStats computeStats(String symbol) {
        List<PredictionEntity> evaluated = predictionRepository.findBySymbolAndActualPriceIsNotNull(symbol);

        if (evaluated.isEmpty()) {
            return new AccuracyStats(symbol, 0, 0, 0.0, 0.0);
        }

        long correct = evaluated.stream().filter(this::isDirectionCorrect).count();
        double averageConfidence = evaluated.stream()
                .mapToDouble(PredictionEntity::getConfidence)
                .average()
                .orElse(0.0);

        return new AccuracyStats(
                symbol,
                evaluated.size(),
                correct,
                (double) correct / evaluated.size(),
                averageConfidence
        );
    }

    /**
     * Determina se a tendência prevista (UP/DOWN/NEUTRAL) coincidiu com a
     * direção real do retorno observado.
     */
    private boolean isDirectionCorrect(PredictionEntity prediction) {
        double actualReturn = prediction.getActualReturn();
        return switch (prediction.getTrend()) {
            case "UP" -> actualReturn > 0;
            case "DOWN" -> actualReturn < 0;
            case "NEUTRAL" -> Math.abs(actualReturn) <= NEUTRAL_TOLERANCE;
            default -> false;
        };
    }

}
