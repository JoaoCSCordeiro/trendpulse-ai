# 📈 TrendPulse AI

### Plataforma reativa de análise preditiva de mercados financeiros, orientada a eventos e alimentada por Machine Learning

TrendPulse AI combina um pipeline de Machine Learning em Python com um backend orientado a eventos em Java/Spring Boot para transformar dados de mercado em previsões acionáveis, entregues em tempo real através de WebSockets. O projeto foi desenhado como demonstração prática de arquitetura distribuída, mensageria assíncrona e orquestração de sistemas de IA em produção.

---

## 🏗️ Arquitetura do Sistema

O sistema está dividido em três componentes independentes, comunicando de forma assíncrona e desacoplada:

- **ML Engine & Ingestão de Dados (Python):** responsável por obter dados históricos e em tempo real via `yfinance`, calcular indicadores técnicos com `pandas-ta` (ex: Médias Móveis Simples), e gerar previsões através de modelos `scikit-learn`. Após o processamento, publica os resultados numa fila RabbitMQ.
- **Core Backend (Java / Spring Boot):** consome as previsões da fila RabbitMQ através de um `@RabbitListener`, e distribui-as instantaneamente a todos os clientes ligados via WebSocket (STOMP sobre SockJS), garantindo baixa latência e comunicação bidirecional.
- **Dashboard (Python / Streamlit):** interface visual "dark mode" que apresenta gráficos de velas interativos (Plotly), médias móveis e métricas de previsão, pensada para um contexto de trading.

Esta separação de responsabilidades permite escalar cada componente de forma independente — por exemplo, múltiplas instâncias do ML Engine podem publicar previsões para a mesma fila, enquanto o Core Backend distribui essa informação a milhares de clientes WebSocket sem acoplamento direto entre os serviços.

### Fluxo Event-Driven

```mermaid
flowchart LR
    subgraph ML["ML Engine (Python)"]
        A[yfinance<br/>Ingestão de Dados] --> B[pandas-ta<br/>Indicadores Técnicos]
        B --> C[scikit-learn<br/>Modelo Preditivo]
    end

    subgraph MQ["Mensageria Assíncrona"]
        D[(RabbitMQ<br/>market.predictions.queue)]
    end

    subgraph BE["Core Backend (Spring Boot)"]
        E[RabbitListener<br/>MarketDataListener] --> F[WebSocket Broker<br/>/ws-market]
    end

    subgraph FE["Frontend"]
        G[Dashboard Streamlit<br/>Gráfico de Velas + SMA]
    end

    C -->|Publica previsão JSON| D
    D -->|Consome mensagem| E
    F -->|Difusão em tempo real| G

    style ML fill:#1E2761,color:#fff
    style MQ fill:#2B3470,color:#fff
    style BE fill:#0E1117,color:#fff
    style FE fill:#161A25,color:#fff
```

---

## 🛠️ Tecnologias Utilizadas

| Camada | Tecnologia | Finalidade |
|---|---|---|
| Core Backend | Java 17, Spring Boot 3 | Orquestração, WebSockets, integração AMQP |
| Mensageria | RabbitMQ (Spring AMQP) | Comunicação assíncrona entre serviços |
| Comunicação Real-Time | WebSocket (STOMP/SockJS) | Difusão de previsões em baixa latência |
| ML Engine | Python 3.11, scikit-learn | Modelação preditiva de séries temporais |
| Dados de Mercado | yfinance | Ingestão de dados históricos e em tempo real |
| Indicadores Técnicos | pandas-ta | Cálculo de SMA, RSI e outros indicadores |
| Frontend | Streamlit, Plotly | Dashboard interativo em dark mode |
| Build/Dependências | Maven, pip | Gestão de dependências dos módulos |

---

## 🗺️ Roadmap de Desenvolvimento

**Fase 1 — Fundações (atual)**
- [x] Estrutura de monorepo
- [x] Configuração base do Spring Boot (WebSocket + RabbitMQ)
- [x] Script inicial de ingestão e visualização de dados

**Fase 2 — Inteligência Preditiva**
- [ ] Treino e serialização de modelos scikit-learn (regressão / séries temporais)
- [ ] Pipeline de feature engineering com indicadores adicionais (RSI, MACD, Bandas de Bollinger)
- [ ] Publicação real das previsões via `pika` para o RabbitMQ

**Fase 3 — Tempo Real e Escala**
- [ ] Consumo de dados de mercado em streaming (near real-time)
- [ ] Persistência histórica de previsões (PostgreSQL / TimescaleDB)
- [ ] Autenticação e gestão de múltiplos utilizadores no dashboard

**Fase 4 — Produção**
- [ ] Containerização completa (Docker Compose: RabbitMQ + Core Backend + ML Engine + Dashboard)
- [ ] CI/CD (GitHub Actions)
- [ ] Observabilidade (métricas, logging estruturado, alertas)

---

## Como Executar Localmente

```bash
# 1. Core Backend
cd core-backend
mvn spring-boot:run

# 2. ML Engine (ambiente virtual recomendado)
cd ml-engine
pip install -r requirements.txt

# 3. Dashboard
cd dashboard
pip install -r requirements.txt
streamlit run app.py
```

> Nota: é necessária uma instância do RabbitMQ em execução (`localhost:5672` por defeito). Pode ser iniciada rapidamente via Docker: `docker run -d -p 5672:5672 -p 15672:15672 rabbitmq:3-management`.

---




