package com.trendpulse.core.repository;

import com.trendpulse.core.model.PredictionEntity;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

/**
 * Acesso a dados para o histórico de previsões persistido em
 * {@code predictions} (hipertable TimescaleDB).
 */
@Repository
public interface PredictionRepository extends JpaRepository<PredictionEntity, Long> {

    /**
     * Devolve as previsões mais recentes de um símbolo, ordenadas da mais
     * recente para a mais antiga. Usa {@link Pageable} (em vez de um
     * parâmetro `limit` cru) para tirar partido da paginação nativa do
     * Spring Data, incluindo geração automática do `LIMIT`/`OFFSET` no SQL.
     */
    List<PredictionEntity> findBySymbolOrderByGeneratedAtDesc(String symbol, Pageable pageable);

    /** Conta quantas previsões existem no total para um símbolo (uso em métricas/UI). */
    long countBySymbol(String symbol);

}
