package com.trendpulse.core.controller;

import com.trendpulse.core.model.PredictionEntity;
import com.trendpulse.core.repository.PredictionRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * Expõe o histórico de previsões persistido em TimescaleDB, para consumo
 * por dashboards externos (ex: um futuro painel "accuracy histórica" no
 * Streamlit) ou para inspeção manual durante o desenvolvimento.
 * <p>
 * Antes desta persistência (Fase 2/3), cada previsão existia apenas em
 * memória durante a difusão WebSocket — não havia forma de responder a
 * "o que é que o modelo previu para a AAPL na semana passada?". Este
 * endpoint fecha essa lacuna.
 */
@RestController
@RequestMapping("/api/predictions")
@RequiredArgsConstructor
public class PredictionHistoryController {

    private final PredictionRepository predictionRepository;

    /**
     * Devolve as últimas {@code limit} previsões de um símbolo, da mais
     * recente para a mais antiga.
     *
     * Exemplo: {@code GET /api/predictions/AAPL/history?limit=20}
     */
    @GetMapping("/{symbol}/history")
    public List<PredictionEntity> history(
            @PathVariable String symbol,
            @RequestParam(defaultValue = "50") int limit
    ) {
        PageRequest pageRequest = PageRequest.of(0, Math.min(limit, 500), Sort.unsorted());
        return predictionRepository.findBySymbolOrderByGeneratedAtDesc(symbol, pageRequest);
    }

    /** Contagem total de previsões guardadas para um símbolo. */
    @GetMapping("/{symbol}/count")
    public Map<String, Object> count(@PathVariable String symbol) {
        return Map.of("symbol", symbol, "count", predictionRepository.countBySymbol(symbol));
    }

}
