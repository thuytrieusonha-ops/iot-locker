FROM python:3.12-slim-bookworm AS server

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md /app/

RUN pip install --upgrade pip \
    && pip install \
        cryptography>=47.0.0 \
        fastapi>=0.136.0 \
        paho-mqtt>=2.1.0 \
        pillow>=11.2.1 \
        pymysql>=1.1.2 \
        pyserial>=3.5 \
        python-multipart>=0.0.20 \
        qrcode>=8.2 \
        sqlalchemy>=2.0.49 \
        uvicorn>=0.44.0

COPY . /app

EXPOSE 8000 8001
