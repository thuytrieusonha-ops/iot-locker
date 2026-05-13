#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-.env.docker}"

docker compose --env-file "$ENV_FILE" down
