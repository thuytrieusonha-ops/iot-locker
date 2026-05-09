from __future__ import annotations

import os
import socket
import threading
import time

import uvicorn
import webview

from config import env_int, env_str
import main as kiosk_main


BIND_HOST = env_str("SMARTLOCKER_APP_HOST", "0.0.0.0") or "0.0.0.0"
DEFAULT_PORT = env_int("SMARTLOCKER_APP_PORT", 8000)
TITLE = "Smart Locker"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800
KIOSK_FORCE_RELOAD_DELAY_SECONDS = 3


def connect_host(bind_host: str) -> str:
    return "127.0.0.1" if bind_host in {"0.0.0.0", "::"} else bind_host


def wait_for_server(host: str, port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"Khong the ket noi den server tai http://{host}:{port}")


def find_available_port(host: str, preferred_port: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, preferred_port))
            return preferred_port
        except OSError:
            pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def run_server(host: str, port: int) -> None:
    uvicorn.run(kiosk_main.app, host=host, port=port, log_level="warning")


def force_reload_window(window: webview.Window, base_url: str) -> None:
    time.sleep(KIOSK_FORCE_RELOAD_DELAY_SECONDS)
    try:
        window.load_url(f"{base_url}?_reload={int(time.time())}")
    except Exception:
        pass


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    port = find_available_port(BIND_HOST, DEFAULT_PORT)
    ui_host = connect_host(BIND_HOST)
    base_url = os.getenv("SMARTLOCKER_BASE_URL", "").strip()

    os.environ["SMARTLOCKER_APP_HOST"] = BIND_HOST
    os.environ["SMARTLOCKER_APP_PORT"] = str(port)
    if not base_url:
        os.environ["SMARTLOCKER_BASE_URL"] = f"http://{ui_host}:{port}"

    kiosk_main.APP_HOST = BIND_HOST
    kiosk_main.APP_PORT = port
    if not kiosk_main.BASE_URL:
        kiosk_main.BASE_URL = os.environ["SMARTLOCKER_BASE_URL"].rstrip("/")

    server_thread = threading.Thread(target=run_server, args=(BIND_HOST, port), daemon=True)
    server_thread.start()
    wait_for_server(ui_host, port)
    kiosk_base_url = f"http://{ui_host}:{port}/"
    kiosk_url = f"{kiosk_base_url}?_kiosk={int(time.time())}"

    window = webview.create_window(
        TITLE,
        kiosk_url,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        resizable=True,
        text_select=False,
    )
    webview.start(force_reload_window, args=(window, kiosk_base_url), gui="qt")


if __name__ == "__main__":
    main()
