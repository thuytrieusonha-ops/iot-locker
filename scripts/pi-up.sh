#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-.env.docker}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Khong tim thay file env: $ENV_FILE"
  echo "Hay tao bang lenh: cp .env.docker.example .env.docker"
  exit 1
fi

docker compose --env-file "$ENV_FILE" up -d --build
