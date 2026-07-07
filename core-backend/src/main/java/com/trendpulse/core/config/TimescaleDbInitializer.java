package com.trendpulse.core.config;

import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/**
 * Ativa a extensão TimescaleDB e converte a tabela {@code predictions} numa
 * <b>hypertable</b>, particionada automaticamente pela coluna
 * {@code generated_at}.
 * <p>
 * Porquê não deixar isto só para o Hibernate? O {@code ddl-auto=update} do
 * Hibernate sabe criar tabelas e colunas relacionais normais, mas não tem
 * qualquer noção de extensões específicas do PostgreSQL como o TimescaleDB.
 * Por isso, depois do Hibernate criar o schema relacional (JPA), este
 * componente corre uma vez, no arranque da aplicação, e:
 * <ol>
 *     <li>Ativa a extensão {@code timescaledb} (idempotente — não falha se já existir)</li>
 *     <li>Converte {@code predictions} numa hypertable particionada por {@code generated_at}
 *         (idempotente via {@code if_not_exists => TRUE})</li>
 * </ol>
 * <p>
 * Isto é suficiente para um projeto desta escala. Num sistema de produção
 * maior, esta lógica normalmente seria movida para uma ferramenta de
 * migração dedicada (Flyway/Liquibase), para ter versionamento explícito
 * do schema.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class TimescaleDbInitializer {

    private final JdbcTemplate jdbcTemplate;

    @PostConstruct
    public void initialize() {
        try {
            jdbcTemplate.execute("CREATE EXTENSION IF NOT EXISTS timescaledb");
            jdbcTemplate.execute(
                    "SELECT create_hypertable('predictions', 'generated_at', if_not_exists => TRUE)"
            );
            log.info("TimescaleDB: extensão ativa e 'predictions' configurada como hypertable.");
        } catch (Exception ex) {
            // Não deitamos a aplicação abaixo por causa disto: em ambientes de
            // desenvolvimento sem TimescaleDB (ex: um PostgreSQL vanilla), a
            // app continua a funcionar como uma tabela relacional normal —
            // só perde o particionamento temporal automático.
            log.warn(
                    "Não foi possível configurar a hypertable TimescaleDB (a app continua "
                            + "a funcionar com uma tabela PostgreSQL normal): {}",
                    ex.getMessage()
            );
        }
    }

}
