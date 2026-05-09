from __future__ import annotations

import os
import socket
from urllib.parse import quote_plus


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        return int(raw_value)
    except ValueError:
        return default


def local_access_host(bind_host: str) -> str:
    host = bind_host.strip()
    if host and host not in {"0.0.0.0", "::"}:
        return host

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            detected_host = sock.getsockname()[0]
            if detected_host:
                return detected_host
    except OSError:
        pass

    return "127.0.0.1"


def build_local_url(bind_host: str, port: int, scheme: str = "http") -> str:
    return f"{scheme}://{local_access_host(bind_host)}:{port}"


def build_database_url() -> str:
    direct_url = env_str("SMARTLOCKER_DATABASE_URL")
    if direct_url:
        return direct_url

    host = env_str("SMARTLOCKER_DATABASE_HOST")
    database = env_str("SMARTLOCKER_DATABASE_NAME")
    user = env_str("SMARTLOCKER_DATABASE_USER")

    if not host or not database or not user:
        return ""

    dialect = env_str("SMARTLOCKER_DATABASE_DIALECT", "mysql+pymysql")
    password = quote_plus(os.getenv("SMARTLOCKER_DATABASE_PASSWORD", ""))
    port = env_int("SMARTLOCKER_DATABASE_PORT", 3306)
    return f"{dialect}://{quote_plus(user)}:{password}@{host}:{port}/{database}"
