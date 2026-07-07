package com.trendpulse.core.dto;

import com.trendpulse.core.model.PredictionEntity;
import org.springframework.data.domain.Page;

import java.util.List;

/**
 * Envelope de resposta paginada para o histórico de previsões.
 * <p>
 * Substitui o antigo {@code List<PredictionEntity>} com {@code limit} fixo
 * por paginação real do Spring Data, expondo metadados (total de páginas,
 * total de elementos) que o cliente (dashboard ou qualquer outro consumidor)
 * precisa para construir controlos de paginação corretos.
 */
public record PredictionHistoryPage(
        List<PredictionEntity> content,
        int pageNumber,
        int pageSize,
        long totalElements,
        int totalPages,
        boolean last
) {
    public static PredictionHistoryPage from(Page<PredictionEntity> page) {
        return new PredictionHistoryPage(
                page.getContent(),
                page.getNumber(),
                page.getSize(),
                page.getTotalElements(),
                page.getTotalPages(),
                page.isLast()
        );
    }
}
