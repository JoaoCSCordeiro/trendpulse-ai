package com.trendpulse.core.dto;

/**
 * Estatísticas de precisão (accuracy) do modelo para um símbolo, calculadas
 * a partir das previsões já avaliadas (ver
 * {@link com.trendpulse.core.service.AccuracyEvaluationService}).
 * <p>
 * Uma previsão só entra nestas estatísticas depois de ter {@code actualPrice}
 * preenchido — ou seja, depois do job diário de avaliação ter comparado a
 * previsão com o preço real observado no dia seguinte.
 *
 * @param symbol             símbolo do ativo
 * @param totalEvaluated     nº de previsões já avaliadas (com preço real conhecido)
 * @param correctDirection   nº de previsões em que a tendência prevista (UP/DOWN/NEUTRAL)
 *                           coincidiu com a direção real observada
 * @param accuracyRate       {@code correctDirection / totalEvaluated}, entre 0 e 1
 *                           (0 se ainda não houver previsões avaliadas)
 * @param averageConfidence  confiança média reportada pelo modelo nas previsões avaliadas
 */
public record AccuracyStats(
        String symbol,
        long totalEvaluated,
        long correctDirection,
        double accuracyRate,
        double averageConfidence
) {
}
