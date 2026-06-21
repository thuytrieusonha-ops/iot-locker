from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from typing import Any

from locker_hardware import MQTT_SETTINGS, env_float, env_int


class PiMegaGateway:
    """Bridge MQTT commands to the Arduino Mega over Raspberry Pi UART."""

    def __init__(self) -> None:
        self.uart_port = os.getenv("SMARTLOCKER_UART_PORT", "/dev/serial0").strip() or "/dev/serial0"
        self.uart_baudrate = env_int("SMARTLOCKER_UART_BAUDRATE", 9600)
        self.uart_timeout = max(0.01, env_float("SMARTLOCKER_UART_TIMEOUT", 0.2))
        self.command_timeout = max(0.1, env_float("SMARTLOCKER_UART_COMMAND_TIMEOUT", 3.0))
        self.locker_count = max(1, env_int("SMARTLOCKER_LOCKER_COUNT", 8))
        self.client_id = (
            os.getenv("SMARTLOCKER_GATEWAY_MQTT_CLIENT_ID", "smartlocker-pi-gateway").strip()
            or "smartlocker-pi-gateway"
        )
        self._serial: Any = None
        self._mqtt: Any = None
        self._uart_write_lock = threading.Lock()
        self._open_command_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending_locker_id: int | None = None
        self._pending_response: str | None = None
        self._pending_event = threading.Event()
        self._request_lock = threading.Lock()
        self._inflight_open_requests: dict[str, tuple[int, threading.Event]] = {}
        self._completed_open_requests: OrderedDict[str, tuple[int, str, str]] = OrderedDict()
        self._completed_request_limit = 1024

    def run(self) -> None:
        self._open_uart()
        threading.Thread(target=self._uart_listener, daemon=True).start()
        self._mqtt = self._create_mqtt_client()
        print(
            f"[gateway] UART ready on {self.uart_port} at {self.uart_baudrate} baud; "
            f"connecting MQTT {MQTT_SETTINGS.host}:{MQTT_SETTINGS.port}."
        )
        self._mqtt.connect(
            MQTT_SETTINGS.host,
            MQTT_SETTINGS.port,
            keepalive=MQTT_SETTINGS.keepalive,
        )
        self._mqtt.loop_forever()

    def stop(self) -> None:
        if self._mqtt is not None:
            status_topic = f"{MQTT_SETTINGS.topic_prefix}/gateway/status"
            self._mqtt.publish(status_topic, "offline", qos=MQTT_SETTINGS.qos, retain=True)
            self._mqtt.disconnect()
        if self._serial is not None:
            self._serial.close()

    def _open_uart(self) -> None:
        try:
            import serial
        except ModuleNotFoundError as exc:
            raise RuntimeError("Chưa cài pyserial cho gateway UART.") from exc
        try:
            self._serial = serial.Serial(
                self.uart_port,
                self.uart_baudrate,
                timeout=self.uart_timeout,
            )
        except Exception as exc:
            raise RuntimeError(f"Không mở được UART {self.uart_port}: {exc}") from exc

    def _create_mqtt_client(self) -> Any:
        try:
            import paho.mqtt.client as mqtt
        except ModuleNotFoundError as exc:
            raise RuntimeError("Chưa cài paho-mqtt cho gateway.") from exc

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
            protocol=mqtt.MQTTv311,
        )
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        if MQTT_SETTINGS.username:
            client.username_pw_set(MQTT_SETTINGS.username, MQTT_SETTINGS.password)
        if MQTT_SETTINGS.use_tls:
            client.tls_set()
        client.will_set(
            f"{MQTT_SETTINGS.topic_prefix}/gateway/status",
            "offline",
            qos=MQTT_SETTINGS.qos,
            retain=True,
        )
        return client

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if reason_code != 0:
            print(f"[gateway] MQTT connection rejected: {reason_code}")
            return
        prefix = MQTT_SETTINGS.topic_prefix
        client.subscribe(f"{prefix}/lockers/+/command", qos=MQTT_SETTINGS.qos)
        client.publish(f"{prefix}/gateway/status", "online", qos=MQTT_SETTINGS.qos, retain=True)
        print("[gateway] MQTT connected; waiting for locker commands.")

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            locker_id = int(payload["locker_id"])
            command = str(payload["command"])
            topic_locker_id = int(message.topic.split("/")[-2])
            if locker_id != topic_locker_id or not 1 <= locker_id <= self.locker_count:
                raise ValueError("locker_id không hợp lệ")
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(f"[gateway] Ignored invalid command on {message.topic}: {exc}")
            return
        threading.Thread(
            target=self._execute_command,
            args=(locker_id, command, payload),
            daemon=True,
        ).start()

    def _execute_command(self, locker_id: int, command: str, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("request_id") or "")
        if command == "open" and request_id:
            self._execute_open_command(locker_id, request_id)
            return

        try:
            if command == "open":
                raise RuntimeError("Lệnh open thiếu request_id")
            elif command == "set_occupied":
                uart_command = "LOCKER_USED" if bool(payload.get("occupied")) else "LOCKER_EMPTY"
                self._write_uart(f"{uart_command},{locker_id}")
            else:
                raise RuntimeError(f"Lệnh không hỗ trợ: {command}")
        except Exception as exc:
            print(f"[gateway] Command failed for locker {locker_id}: {exc}")
            if request_id:
                self._publish_ack(locker_id, request_id, "error", str(exc))

    def _execute_open_command(self, locker_id: int, request_id: str) -> None:
        """Execute each MQTT open request once and replay its acknowledgement on redelivery."""
        try:
            owner, event, completed = self._claim_open_request(locker_id, request_id)
        except RuntimeError as exc:
            print(f"[gateway] Command rejected for locker {locker_id}: {exc}")
            self._publish_ack(locker_id, request_id, "error", str(exc))
            return
        if completed is not None:
            status, message = completed
            self._publish_ack(locker_id, request_id, status, message)
            return

        if not owner:
            event.wait(self.command_timeout + 1.0)
            completed = self._completed_open_request(locker_id, request_id)
            if completed is not None:
                status, message = completed
                self._publish_ack(locker_id, request_id, status, message)
            return

        status = "opened"
        message = ""
        try:
            self._open_locker(locker_id)
        except Exception as exc:
            status = "error"
            message = str(exc)
            print(f"[gateway] Command failed for locker {locker_id}: {exc}")
        finally:
            self._finish_open_request(locker_id, request_id, status, message, event)

        self._publish_ack(locker_id, request_id, status, message)

    def _claim_open_request(
        self,
        locker_id: int,
        request_id: str,
    ) -> tuple[bool, threading.Event, tuple[str, str] | None]:
        with self._request_lock:
            completed = self._completed_open_requests.get(request_id)
            if completed is not None:
                completed_locker_id, status, message = completed
                if completed_locker_id != locker_id:
                    raise RuntimeError(f"request_id {request_id} đã được dùng cho tủ khác")
                self._completed_open_requests.move_to_end(request_id)
                return False, threading.Event(), (status, message)

            inflight = self._inflight_open_requests.get(request_id)
            if inflight is not None:
                inflight_locker_id, event = inflight
                if inflight_locker_id != locker_id:
                    raise RuntimeError(f"request_id {request_id} đang được dùng cho tủ khác")
                return False, event, None

            event = threading.Event()
            self._inflight_open_requests[request_id] = (locker_id, event)
            return True, event, None

    def _completed_open_request(self, locker_id: int, request_id: str) -> tuple[str, str] | None:
        with self._request_lock:
            completed = self._completed_open_requests.get(request_id)
            if completed is None or completed[0] != locker_id:
                return None
            self._completed_open_requests.move_to_end(request_id)
            return completed[1], completed[2]

    def _finish_open_request(
        self,
        locker_id: int,
        request_id: str,
        status: str,
        message: str,
        event: threading.Event,
    ) -> None:
        with self._request_lock:
            self._inflight_open_requests.pop(request_id, None)
            self._completed_open_requests[request_id] = (locker_id, status, message)
            self._completed_open_requests.move_to_end(request_id)
            while len(self._completed_open_requests) > self._completed_request_limit:
                self._completed_open_requests.popitem(last=False)
            event.set()

    def _open_locker(self, locker_id: int) -> None:
        with self._open_command_lock:
            with self._pending_lock:
                self._pending_locker_id = locker_id
                self._pending_response = None
                self._pending_event.clear()
            try:
                self._write_uart(f"OPEN,{locker_id}")
                if not self._pending_event.wait(self.command_timeout):
                    raise RuntimeError(f"Mega không xác nhận OK,{locker_id}")
                with self._pending_lock:
                    if self._pending_response != f"OK,{locker_id}":
                        raise RuntimeError(f"Phản hồi UART không hợp lệ: {self._pending_response}")
            finally:
                with self._pending_lock:
                    self._pending_locker_id = None
                    self._pending_response = None

    def _write_uart(self, command: str) -> None:
        if self._serial is None:
            raise RuntimeError("UART chưa sẵn sàng")
        with self._uart_write_lock:
            self._serial.write((command + "\n").encode("utf-8"))
            self._serial.flush()
        print(f"[gateway] UART TX: {command}")

    def _uart_listener(self) -> None:
        while True:
            try:
                if self._serial is None:
                    return
                line = self._serial.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                print(f"[gateway] UART RX: {line}")
                self._handle_uart_line(line)
            except Exception as exc:
                print(f"[gateway] UART listener warning: {exc}")
                time.sleep(0.1)

    def _handle_uart_line(self, line: str) -> None:
        parts = line.split(",", 1)
        if len(parts) != 2:
            return
        event_name, raw_locker_id = parts
        try:
            locker_id = int(raw_locker_id)
        except ValueError:
            return

        if event_name == "OK":
            with self._pending_lock:
                if self._pending_locker_id == locker_id:
                    self._pending_response = line
                    self._pending_event.set()
            return

        event_map = {"DOOR_OPEN": "door_open", "DOOR_CLOSED": "door_closed"}
        if event_name in event_map and self._mqtt is not None:
            topic = f"{MQTT_SETTINGS.topic_prefix}/lockers/{locker_id}/event"
            payload = json.dumps({"locker_id": locker_id, "event": event_map[event_name]})
            self._mqtt.publish(topic, payload, qos=MQTT_SETTINGS.qos, retain=False)

    def _publish_ack(self, locker_id: int, request_id: str, status: str, message: str = "") -> None:
        if self._mqtt is None:
            return
        topic = f"{MQTT_SETTINGS.topic_prefix}/lockers/{locker_id}/ack"
        payload = {"request_id": request_id, "locker_id": locker_id, "status": status}
        if message:
            payload["message"] = message
        self._mqtt.publish(topic, json.dumps(payload), qos=MQTT_SETTINGS.qos, retain=False)


def main() -> None:
    gateway = PiMegaGateway()
    try:
        gateway.run()
    except KeyboardInterrupt:
        pass
    finally:
        gateway.stop()


if __name__ == "__main__":
    main()
