package com.trendpulse.core.controller;

import com.trendpulse.core.dto.AccuracyStats;
import com.trendpulse.core.dto.PredictionHistoryPage;
import com.trendpulse.core.model.PredictionEntity;
import com.trendpulse.core.repository.PredictionRepository;
import com.trendpulse.core.service.AccuracyEvaluationService;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * Expõe o histórico de previsões persistido em TimescaleDB e as respetivas
 * estatísticas de accuracy, para consumo por dashboards externos (ex: o
 * painel "accuracy histórica" no Streamlit) ou para inspeção manual durante
 * o desenvolvimento.
 * <p>
 * <b>Versionamento:</b> a API vive sob {@code /api/v1/...}. Isto permite
 * introduzir uma futura {@code /api/v2/...} com um contrato diferente (ex:
 * novos campos, formato de paginação distinto) sem quebrar clientes já
 * integrados com a v1 — uma prática elementar de API design que faltava
 * nas fases anteriores.
 */
@RestController
@RequestMapping("/api/v1/predictions")
@RequiredArgsConstructor
public class PredictionHistoryController {

    private static final int MAX_PAGE_SIZE = 200;

    private final PredictionRepository predictionRepository;
    private final AccuracyEvaluationService accuracyEvaluationService;

    /**
     * Devolve uma página do histórico de previsões de um símbolo, da mais
     * recente para a mais antiga.
     * <p>
     * Exemplo: {@code GET /api/v1/predictions/AAPL/history?page=0&size=20}
     * <p>
     * Substituiu o antigo parâmetro {@code limit} (que devolvia sempre um
     * único bloco, sem forma de navegar para trás no histórico) por
     * paginação real do Spring Data — a resposta inclui
     * {@code totalElements}/{@code totalPages}, para o cliente construir
     * corretamente os controlos de paginação.
     */
    @GetMapping("/{symbol}/history")
    public PredictionHistoryPage history(
            @PathVariable String symbol,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "50") int size
    ) {
        int safeSize = Math.min(Math.max(size, 1), MAX_PAGE_SIZE);
        PageRequest pageRequest = PageRequest.of(
                Math.max(page, 0), safeSize, Sort.by("generatedAt").descending()
        );

        Page<PredictionEntity> result =
                predictionRepository.findBySymbolOrderByGeneratedAtDesc(symbol, pageRequest);

        return PredictionHistoryPage.from(result);
    }

    /** Contagem total de previsões guardadas para um símbolo. */
    @GetMapping("/{symbol}/count")
    public Map<String, Object> count(@PathVariable String symbol) {
        return Map.of("symbol", symbol, "count", predictionRepository.countBySymbol(symbol));
    }

    /**
     * Estatísticas de accuracy do modelo para um símbolo: % de previsões em
     * que a tendência (UP/DOWN/NEUTRAL) coincidiu com a direção real
     * observada no dia seguinte. Só considera previsões já avaliadas pelo
     * {@link AccuracyEvaluationService} (job diário).
     * <p>
     * Exemplo: {@code GET /api/v1/predictions/AAPL/accuracy}
     */
    @GetMapping("/{symbol}/accuracy")
    public AccuracyStats accuracy(@PathVariable String symbol) {
        return accuracyEvaluationService.computeStats(symbol);
    }

}
