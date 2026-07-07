package com.trendpulse.core.client;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.List;
import java.util.Optional;

/**
 * Cliente HTTP mínimo para o endpoint público de "chart" do Yahoo Finance,
 * usado exclusivamente pelo {@link com.trendpulse.core.service.AccuracyEvaluationService}
 * para obter o preço de fecho mais recente de um símbolo, e assim comparar
 * com o `predictedPrice` guardado no momento da previsão.
 * <p>
 * Nota: isto é deliberadamente independente do `ml-engine` (Python/yfinance)
 * — o Core Backend (Java) tem a sua própria fonte de verdade para o preço
 * "real" observado, em vez de depender de um segundo serviço Python só para
 * validar previsões antigas.
 */
@Slf4j
@Component
public class YahooFinanceClient {

    private static final String CHART_URL_TEMPLATE =
            "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d";

    private final RestClient restClient;

    public YahooFinanceClient(RestClient.Builder restClientBuilder) {
        this.restClient = restClientBuilder.build();
    }

    /**
     * Obtém o preço de mercado mais recente disponível para o símbolo.
     *
     * @param symbol símbolo do ativo (ex: "AAPL", "BTC-USD")
     * @return o preço mais recente, ou {@link Optional#empty()} se a chamada
     *         falhar ou o símbolo não devolver dados (ex: rate limit,
     *         símbolo inválido, indisponibilidade temporária da API).
     */
    public Optional<Double> fetchLatestPrice(String symbol) {
        try {
            ChartResponse response = restClient.get()
                    .uri(CHART_URL_TEMPLATE, symbol)
                    .retrieve()
                    .body(ChartResponse.class);

            return Optional.ofNullable(response)
                    .map(ChartResponse::chart)
                    .map(Chart::result)
                    .filter(results -> !results.isEmpty())
                    .map(results -> results.get(0))
                    .map(Result::meta)
                    .map(Meta::regularMarketPrice);

        } catch (Exception ex) {
            log.warn("Não foi possível obter o preço atual de '{}' via Yahoo Finance: {}",
                    symbol, ex.getMessage());
            return Optional.empty();
        }
    }

    // --- DTOs internos de desserialização (só os campos que nos interessam) ---

    @JsonIgnoreProperties(ignoreUnknown = true)
    private record ChartResponse(Chart chart) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    private record Chart(List<Result> result) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    private record Result(Meta meta) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    private record Meta(double regularMarketPrice) {
    }

}
