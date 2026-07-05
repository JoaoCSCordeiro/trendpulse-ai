package com.trendpulse.core.config;

import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.Queue;
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
 */
@Configuration
public class RabbitMQConfig {

    public static final String EXCHANGE_NAME = "market.predictions.exchange";
    public static final String QUEUE_NAME = "market.predictions.queue";
    public static final String ROUTING_KEY = "market.predictions.*";

    @Bean
    public TopicExchange predictionsExchange() {
        return new TopicExchange(EXCHANGE_NAME, true, false);
    }

    @Bean
    public Queue predictionsQueue() {
        // Queue durável: sobrevive a reinícios do broker, evitando perda
        // de previsões geradas pelo ML Engine.
        return new Queue(QUEUE_NAME, true);
    }

    @Bean
    public Binding predictionsBinding(Queue predictionsQueue, TopicExchange predictionsExchange) {
        return BindingBuilder.bind(predictionsQueue)
                .to(predictionsExchange)
                .with(ROUTING_KEY);
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
