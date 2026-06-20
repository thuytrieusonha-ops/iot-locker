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


FROM server AS kiosk

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libegl1 \
        libgbm1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libsm6 \
        libx11-6 \
        libx11-xcb1 \
        libxcb-render0 \
        libxcb-shape0 \
        libxcb-shm0 \
        libxcb-util1 \
        libxcb-xfixes0 \
        libxcb1 \
        libxcomposite1 \
        libxcursor1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxi6 \
        libxkbcommon0 \
        libxrandr2 \
        libxrender1 \
        libxtst6 \
        xauth \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip \
    && pip install \
        pyqt6>=6.8.1 \
        pyqt6-webengine>=6.8.0 \
        pywebview>=5.4 \
        qtpy>=2.4.3

ENV QT_QPA_PLATFORM=xcb
ENV QTWEBENGINE_DISABLE_SANDBOX=1
ENV XDG_RUNTIME_DIR=/tmp/runtime-root

RUN mkdir -p /tmp/runtime-root \
    && chmod 700 /tmp/runtime-root
