from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any


class LockerHardwareError(RuntimeError):
    pass


def env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name, "").strip().lower()
    if not raw_value:
        return default
    return raw_value in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class MqttSettings:
    host: str
    port: int
    topic_prefix: str
    client_id: str
    username: str
    password: str
    qos: int
    keepalive: int
    connect_timeout: float
    command_timeout: float
    door_close_timeout: float
    use_tls: bool


class MqttLockerController:
    """Publish locker commands and correlate controller acknowledgements over MQTT."""

    def __init__(self, settings: MqttSettings) -> None:
        self.settings = settings
        self._client: Any = None
        self._client_lock = threading.Lock()
        self._connected = threading.Event()
        self._pending_lock = threading.Lock()
        self._pending: dict[str, tuple[threading.Event, dict[str, Any]]] = {}
        self._door_condition = threading.Condition()
        self._door_event_sequence = 0
        self._door_open_sequence: dict[int, int] = {}
        self._door_closed_sequence: dict[int, int] = {}

    def start(self) -> None:
        with self._client_lock:
            if self._client is None:
                self._create_and_connect_client()

        if not self._connected.wait(self.settings.connect_timeout):
            self.stop()
            raise LockerHardwareError(
                f"Không kết nối được MQTT broker {self.settings.host}:{self.settings.port}."
            )

    def stop(self) -> None:
        with self._client_lock:
            client, self._client = self._client, None
            self._connected.clear()
        if client is not None:
            try:
                client.disconnect()
                client.loop_stop()
            except Exception:
                pass

    def open_locker(self, locker_id: int) -> None:
        self._validate_locker_id(locker_id)
        request_id = uuid.uuid4().hex
        done = threading.Event()
        response: dict[str, Any] = {}

        with self._pending_lock:
            self._pending[request_id] = (done, response)

        try:
            self._publish(locker_id, "open", request_id=request_id)
            if not done.wait(self.settings.command_timeout):
                raise LockerHardwareError(
                    f"Hết thời gian chờ xác nhận mở tủ {locker_id} từ MQTT controller."
                )
            if response.get("status") not in {"ok", "opened", "accepted"}:
                detail = str(response.get("message") or "controller từ chối lệnh")
                raise LockerHardwareError(f"Không mở được tủ {locker_id}: {detail}.")
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def mark_locker_used(self, locker_id: int) -> None:
        self._publish(locker_id, "set_occupied", retain=True, occupied=True)

    def mark_locker_empty(self, locker_id: int) -> None:
        self._publish(locker_id, "set_occupied", retain=True, occupied=False)

    def open_locker_for_dropoff(self, locker_id: int) -> None:
        """Open a locker, then turn on its occupied light only after the door closes."""
        self._validate_locker_id(locker_id)
        with self._door_condition:
            event_marker = self._door_event_sequence

        self.open_locker(locker_id)
        self._wait_for_open_then_close(locker_id, event_marker)
        self.mark_locker_used(locker_id)

    def _wait_for_open_then_close(self, locker_id: int, event_marker: int) -> None:
        deadline = time.monotonic() + self.settings.door_close_timeout
        with self._door_condition:
            while True:
                opened_at = self._door_open_sequence.get(locker_id, 0)
                closed_at = self._door_closed_sequence.get(locker_id, 0)
                if opened_at > event_marker and closed_at > opened_at:
                    return

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockerHardwareError(
                        f"Hết thời gian chờ shipper đóng cửa tủ {locker_id}. Đèn chưa được bật."
                    )
                self._door_condition.wait(remaining)

    def _create_and_connect_client(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ModuleNotFoundError as exc:
            raise LockerHardwareError("Chưa cài paho-mqtt nên không thể kết nối MQTT.") from exc

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.settings.client_id,
            protocol=mqtt.MQTTv311,
        )
        client.enable_logger()
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        if self.settings.username:
            client.username_pw_set(self.settings.username, self.settings.password)
        if self.settings.use_tls:
            client.tls_set()

        try:
            client.connect_async(
                self.settings.host,
                self.settings.port,
                keepalive=self.settings.keepalive,
            )
            client.loop_start()
        except Exception as exc:
            raise LockerHardwareError(f"Không khởi tạo được MQTT client: {exc}") from exc
        self._client = client

    def _publish(self, locker_id: int, command: str, retain: bool = False, **values: Any) -> None:
        self._validate_locker_id(locker_id)
        self.start()
        payload = {
            "command": command,
            "locker_id": locker_id,
            **values,
        }
        topic = f"{self.settings.topic_prefix}/lockers/{locker_id}/command"
        assert self._client is not None
        result = self._client.publish(
            topic,
            json.dumps(payload, separators=(",", ":")),
            qos=self.settings.qos,
            retain=retain,
        )
        if result.rc != 0:
            raise LockerHardwareError(f"Không publish được lệnh MQTT (mã lỗi {result.rc}).")

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if reason_code != 0:
            print(f"[smartlocker] MQTT connection rejected: {reason_code}")
            return
        prefix = self.settings.topic_prefix
        client.subscribe(f"{prefix}/lockers/+/ack", qos=self.settings.qos)
        client.subscribe(f"{prefix}/lockers/+/event", qos=self.settings.qos)
        self._connected.set()
        print(f"[smartlocker] MQTT connected to {self.settings.host}:{self.settings.port}.")

    def _on_disconnect(
        self,
        client: Any,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        self._connected.clear()
        if reason_code != 0:
            print(f"[smartlocker] MQTT disconnected unexpectedly: {reason_code}")

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            print(f"[smartlocker] Ignored invalid MQTT payload on {message.topic}.")
            return

        if message.topic.endswith("/ack"):
            request_id = str(payload.get("request_id") or "")
            with self._pending_lock:
                pending = self._pending.get(request_id)
                if pending is not None:
                    done, response = pending
                    response.update(payload)
                    done.set()
            return

        if message.topic.endswith("/event"):
            try:
                locker_id = int(payload["locker_id"])
                event_name = str(payload["event"])
            except (KeyError, TypeError, ValueError):
                print(f"[smartlocker] Ignored invalid MQTT event on {message.topic}.")
                return

            if event_name in {"door_open", "door_closed"}:
                with self._door_condition:
                    self._door_event_sequence += 1
                    if event_name == "door_open":
                        self._door_open_sequence[locker_id] = self._door_event_sequence
                    else:
                        self._door_closed_sequence[locker_id] = self._door_event_sequence
                    self._door_condition.notify_all()

        print(f"[smartlocker] MQTT event {message.topic}: {payload}")

    @staticmethod
    def _validate_locker_id(locker_id: int) -> None:
        if locker_id < 1:
            raise LockerHardwareError("Mã tủ không hợp lệ.")


HARDWARE_ENABLED = env_bool("SMARTLOCKER_HARDWARE_ENABLED", False)
HARDWARE_REQUIRED = env_bool("SMARTLOCKER_HARDWARE_REQUIRED", HARDWARE_ENABLED)
MQTT_SETTINGS = MqttSettings(
    host=os.getenv("SMARTLOCKER_MQTT_HOST", "localhost").strip() or "localhost",
    port=env_int("SMARTLOCKER_MQTT_PORT", 1883),
    topic_prefix=os.getenv("SMARTLOCKER_MQTT_TOPIC_PREFIX", "smartlocker").strip().strip("/") or "smartlocker",
    client_id=os.getenv("SMARTLOCKER_MQTT_CLIENT_ID", "smartlocker-app").strip() or "smartlocker-app",
    username=os.getenv("SMARTLOCKER_MQTT_USERNAME", "").strip(),
    password=os.getenv("SMARTLOCKER_MQTT_PASSWORD", ""),
    qos=max(0, min(2, env_int("SMARTLOCKER_MQTT_QOS", 1))),
    keepalive=max(10, env_int("SMARTLOCKER_MQTT_KEEPALIVE", 60)),
    connect_timeout=max(0.1, env_float("SMARTLOCKER_MQTT_CONNECT_TIMEOUT", 5.0)),
    command_timeout=max(0.1, env_float("SMARTLOCKER_MQTT_COMMAND_TIMEOUT", 5.0)),
    door_close_timeout=max(1.0, env_float("SMARTLOCKER_DOOR_CLOSE_TIMEOUT", 120.0)),
    use_tls=env_bool("SMARTLOCKER_MQTT_USE_TLS", False),
)
controller = MqttLockerController(MQTT_SETTINGS)


def start_hardware() -> None:
    if HARDWARE_ENABLED:
        try:
            controller.start()
        except LockerHardwareError as exc:
            if HARDWARE_REQUIRED:
                raise
            print(f"[smartlocker] MQTT startup warning: {exc}")


def close_hardware() -> None:
    controller.stop()


def open_locker(locker_id: int) -> None:
    if not HARDWARE_ENABLED:
        print(f"[smartlocker] Hardware disabled, simulated MQTT open for locker {locker_id}.")
        return
    try:
        controller.open_locker(locker_id)
    except LockerHardwareError as exc:
        if HARDWARE_REQUIRED:
            raise
        print(f"[smartlocker] MQTT warning, simulated open for locker {locker_id}: {exc}")


def open_locker_for_dropoff(locker_id: int) -> None:
    if not HARDWARE_ENABLED:
        print(f"[smartlocker] Hardware disabled, simulated dropoff for locker {locker_id}.")
        return
    try:
        controller.open_locker_for_dropoff(locker_id)
    except LockerHardwareError as exc:
        if HARDWARE_REQUIRED:
            raise
        print(f"[smartlocker] MQTT warning, simulated dropoff for locker {locker_id}: {exc}")


def mark_locker_used(locker_id: int) -> None:
    _publish_state_with_fallback(locker_id, occupied=True)


def mark_locker_empty(locker_id: int) -> None:
    _publish_state_with_fallback(locker_id, occupied=False)


def _publish_state_with_fallback(locker_id: int, occupied: bool) -> None:
    if not HARDWARE_ENABLED:
        return
    try:
        if occupied:
            controller.mark_locker_used(locker_id)
        else:
            controller.mark_locker_empty(locker_id)
    except LockerHardwareError as exc:
        if HARDWARE_REQUIRED:
            raise
        print(f"[smartlocker] MQTT warning, skipped locker state update: {exc}")
