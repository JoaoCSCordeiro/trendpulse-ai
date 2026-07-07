FROM python:3.12-slim

WORKDIR /app

# Dependências de sistema necessárias para compilar pacotes científicos (numpy/scikit-learn)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY features.py train_model.py predict.py ./
COPY models ./models

# Este container corre o treino uma vez e termina — não é um serviço de longa duração.
# O modelo resultante fica no volume partilhado `ml-models`, para o dashboard o ler.
ENTRYPOINT ["python", "train_model.py"]
