package com.trendpulse.core.repository;

import com.trendpulse.core.model.PredictionEntity;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.Instant;
import java.util.List;

/**
 * Acesso a dados para o histórico de previsões persistido em
 * {@code predictions} (hipertable TimescaleDB).
 */
@Repository
public interface PredictionRepository extends JpaRepository<PredictionEntity, Long> {

    /**
     * Devolve uma página de previsões de um símbolo, ordenadas da mais
     * recente para a mais antiga. Usa {@link Page} (paginação real do
     * Spring Data) em vez de um {@code limit} fixo, para que o cliente
     * consiga navegar por todo o histórico e saiba quantas páginas existem.
     */
    Page<PredictionEntity> findBySymbolOrderByGeneratedAtDesc(String symbol, Pageable pageable);

    /** Conta quantas previsões existem no total para um símbolo (uso em métricas/UI). */
    long countBySymbol(String symbol);

    /**
     * Previsões de um símbolo ainda por avaliar (sem preço real conhecido),
     * geradas antes do instante indicado — usado pelo
     * {@link com.trendpulse.core.service.AccuracyEvaluationService} para
     * saber quais previsões já "amadureceram" o suficiente (ex: > 1 dia)
     * para serem comparadas com o preço real observado.
     */
    List<PredictionEntity> findBySymbolAndActualPriceIsNullAndGeneratedAtBefore(
            String symbol, Instant threshold);

    /**
     * Lista de símbolos distintos com previsões pendentes de avaliação.
     * Evita fazer uma query por símbolo "às cegas" — o job de accuracy só
     * chama o Yahoo Finance para símbolos que realmente têm trabalho por fazer.
     */
    @Query("select distinct p.symbol from PredictionEntity p "
            + "where p.actualPrice is null and p.generatedAt < :threshold")
    List<String> findDistinctSymbolsPendingEvaluation(@Param("threshold") Instant threshold);

    /** Previsões já avaliadas (com preço real conhecido) de um símbolo, para cálculo de accuracy. */
    List<PredictionEntity> findBySymbolAndActualPriceIsNotNull(String symbol);

}
