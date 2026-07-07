package com.trendpulse.core;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * TrendPulse AI - Core Backend.
 * <p>
 * Orquestra a comunicação event-driven entre o ML Engine (Python) e os
 * clientes finais, através de RabbitMQ (ingestão de previsões) e WebSockets
 * (difusão em tempo real para o dashboard/frontend). Também corre o job
 * diário de avaliação de accuracy (ver {@code AccuracyEvaluationService}),
 * daí o {@link EnableScheduling}.
 */
@SpringBootApplication
@EnableScheduling
public class CoreBackendApplication {

    public static void main(String[] args) {
        SpringApplication.run(CoreBackendApplication.class, args);
    }

}
