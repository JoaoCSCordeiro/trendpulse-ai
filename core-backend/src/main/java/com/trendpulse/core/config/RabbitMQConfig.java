package com.trendpulse.core.config;

import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.amqp.core.TopicExchange;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Configuração de mensageria assíncrona (RabbitMQ) para o TrendPulse AI.
 * <p>
 * O ML Engine (Python) publica previsões processadas na exchange
 * {@code market.predictions.exchange}, usando a routing key
 * {@code market.predictions.<symbol>}. O Core Backend (Java) consome estas
 * mensagens através da queue {@code market.predictions.queue} e distribui-as
 * em tempo real para os clientes ligados via WebSocket.
 * <p>
 * <b>Resiliência:</b> a queue principal está configurada com uma Dead-Letter
 * Exchange (DLX). Combinado com a política de retry definida em
 * {@code application.yml} ({@code spring.rabbitmq.listener.simple.retry}),
 * uma mensagem que falhe repetidamente no processamento (ex: JSON malformado,
 * exceção no listener) é, após esgotar as tentativas, rejeitada e
 * automaticamente reencaminhada para a {@code market.predictions.dlq} —
 * em vez de ser perdida silenciosamente ou bloquear a queue principal.
 */
@Configuration
public class RabbitMQConfig {

    public static final String EXCHANGE_NAME = "market.predictions.exchange";
    public static final String QUEUE_NAME = "market.predictions.queue";
    public static final String ROUTING_KEY = "market.predictions.*";

    public static final String DLX_EXCHANGE_NAME = "market.predictions.dlx";
    public static final String DLQ_QUEUE_NAME = "market.predictions.dlq";
    public static final String DLQ_ROUTING_KEY = "market.predictions.dead";

    // --- Exchange e queue principais ---

    @Bean
    public TopicExchange predictionsExchange() {
        return new TopicExchange(EXCHANGE_NAME, true, false);
    }

    @Bean
    public Queue predictionsQueue() {
        // Queue durável (sobrevive a reinícios do broker) e associada a uma
        // dead-letter exchange: mensagens rejeitadas (após esgotar retries)
        // são automaticamente reencaminhadas para lá, em vez de perdidas.
        return QueueBuilder.durable(QUEUE_NAME)
                .withArgument("x-dead-letter-exchange", DLX_EXCHANGE_NAME)
                .withArgument("x-dead-letter-routing-key", DLQ_ROUTING_KEY)
                .build();
    }

    @Bean
    public Binding predictionsBinding(Queue predictionsQueue, TopicExchange predictionsExchange) {
        return BindingBuilder.bind(predictionsQueue)
                .to(predictionsExchange)
                .with(ROUTING_KEY);
    }

    // --- Dead-Letter Exchange / Queue ---

    @Bean
    public TopicExchange predictionsDlxExchange() {
        return new TopicExchange(DLX_EXCHANGE_NAME, true, false);
    }

    @Bean
    public Queue predictionsDlq() {
        // Queue "de quarentena": guarda mensagens que falharam definitivamente,
        // para inspeção manual (ex: via RabbitMQ Management UI) sem bloquear
        // o processamento normal do fluxo principal.
        return QueueBuilder.durable(DLQ_QUEUE_NAME).build();
    }

    @Bean
    public Binding predictionsDlqBinding(Queue predictionsDlq, TopicExchange predictionsDlxExchange) {
        return BindingBuilder.bind(predictionsDlq)
                .to(predictionsDlxExchange)
                .with(DLQ_ROUTING_KEY);
    }

    /**
     * Conversor de mensagens JSON <-> POJO, permitindo que o payload Python
     * (JSON) seja desserializado diretamente para os DTOs Java (e vice-versa).
     */
    @Bean
    public MessageConverter jsonMessageConverter() {
        return new Jackson2JsonMessageConverter();
    }

}
