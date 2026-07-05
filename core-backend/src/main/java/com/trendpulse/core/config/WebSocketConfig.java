package com.trendpulse.core.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.messaging.simp.config.MessageBrokerRegistry;
import org.springframework.web.socket.config.annotation.EnableWebSocketMessageBroker;
import org.springframework.web.socket.config.annotation.StompEndpointRegistry;
import org.springframework.web.socket.config.annotation.WebSocketMessageBrokerConfigurer;

/**
 * Configuração do canal WebSocket (STOMP sobre SockJS) usado para difundir
 * previsões de mercado em tempo real para os clientes subscritos
 * (ex: dashboard Streamlit, futuros clientes web/mobile).
 */
@Configuration
@EnableWebSocketMessageBroker
public class WebSocketConfig implements WebSocketMessageBrokerConfigurer {

    /** Endpoint que os clientes usam para estabelecer a ligação WebSocket. */
    public static final String WS_ENDPOINT = "/ws-market";

    /** Prefixo dos destinos geridos pelo broker simples (in-memory). */
    public static final String TOPIC_PREFIX = "/topic";

    /** Prefixo usado pelo cliente para enviar mensagens para o servidor. */
    public static final String APP_PREFIX = "/app";

    @Override
    public void registerStompEndpoints(StompEndpointRegistry registry) {
        registry.addEndpoint(WS_ENDPOINT)
                .setAllowedOriginPatterns("*") // ajustar em produção
                .withSockJS();
    }

    @Override
    public void configureMessageBroker(MessageBrokerRegistry registry) {
        // Broker simples em memória para tópicos de difusão (broadcast).
        registry.enableSimpleBroker(TOPIC_PREFIX);
        // Prefixo para mensagens enviadas do cliente para métodos @MessageMapping.
        registry.setApplicationDestinationPrefixes(APP_PREFIX);
    }

}
