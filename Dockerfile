FROM python:3.14-slim

WORKDIR /app

# O tzdata entra explicito, e nao de carona em outra dependencia: sem ele um
# TZ=America/Sao_Paulo e aceito em silencio e o processo continua em UTC, o que
# e o pior dos mundos — a configuracao parece ter pegado e nao pegou.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-por libzbar0 tzdata \
    && rm -rf /var/lib/apt/lists/*

# Fuso do processo. UTC como padrao porque o app e generico e o fuso de quem
# escreveu o codigo nao pode virar o fuso de quem hospeda. Quem instala poe o
# seu no .env (ver TZ no .env.example): e essa variavel que decide a hora
# gravada em data_pagamento, em criado_em e na coluna `hora` do lancamento.
ENV TZ=UTC

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY LICENSE .
COPY static ./static
COPY migrations ./migrations

RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 5000

CMD ["python", "app.py"]
