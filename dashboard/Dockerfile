# Este Dockerfile espera ser construído a partir da RAIZ do monorepo
# (ver docker-compose.yml: `context: .`, `dockerfile: dashboard/Dockerfile`),
# porque o dashboard importa módulos de `ml-engine/` (ver `predict.py`,
# `ML_ENGINE_PATH` em `dashboard/app.py`) e precisamos de ambas as pastas
# dentro da mesma imagem, na mesma disposição relativa do repositório.

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Instala as dependências de ambos os módulos Python
COPY dashboard/requirements.txt ./dashboard/requirements.txt
COPY ml-engine/requirements.txt ./ml-engine/requirements.txt
RUN pip install --no-cache-dir -r dashboard/requirements.txt \
    && pip install --no-cache-dir -r ml-engine/requirements.txt

# Copia o código mantendo a estrutura de pastas irmãs do monorepo
COPY dashboard ./dashboard
COPY ml-engine/features.py ./ml-engine/features.py
COPY ml-engine/predict.py ./ml-engine/predict.py

# O modelo treinado é montado via volume partilhado (ver docker-compose.yml),
# não copiado para a imagem — assim um novo treino não obriga a rebuild.

WORKDIR /app/dashboard
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

ENTRYPOINT ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
