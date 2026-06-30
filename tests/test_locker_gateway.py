from __future__ import annotations

import json
import threading
import time
import unittest

from locker_gateway import PiMegaGateway


class FakeSerial:
    def __init__(self, gateway: PiMegaGateway) -> None:
        self.gateway = gateway
        self.writes: list[bytes] = []

    def write(self, value: bytes) -> int:
        self.writes.append(value)
        command = value.decode("utf-8").strip()
        action, raw_locker_id = command.split(",", 1)
        if action == "OPEN":
            self.gateway._handle_uart_line(f"OK,{raw_locker_id}")
        return len(value)

    def flush(self) -> None:
        pass


class FakeMqtt:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, object]]] = []

    def publish(self, topic: str, payload: str, **kwargs: object) -> None:
        self.messages.append((topic, json.loads(payload)))


class FakeCommandMessage:
    def __init__(self, locker_id: int, command: str, request_id: str, retain: bool) -> None:
        self.topic = f"smartlocker/lockers/{locker_id}/command"
        self.payload = json.dumps(
            {"locker_id": locker_id, "command": command, "request_id": request_id}
        ).encode("utf-8")
        self.retain = retain


class PiMegaGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = PiMegaGateway()
        self.serial = FakeSerial(self.gateway)
        self.mqtt = FakeMqtt()
        self.gateway._serial = self.serial
        self.gateway._mqtt = self.mqtt

    def test_duplicate_requests_for_lockers_1_2_3_send_uart_once(self) -> None:
        for locker_id in (1, 2, 3):
            payload = {"request_id": f"open-{locker_id}"}

            self.gateway._execute_command(locker_id, "open", payload)
            self.gateway._execute_command(locker_id, "open", payload)

        self.assertEqual(
            self.serial.writes,
            [b"OPEN,1\n", b"OPEN,2\n", b"OPEN,3\n"],
        )
        self.assertEqual(
            [message[1]["status"] for message in self.mqtt.messages],
            ["opened", "opened", "opened", "opened", "opened", "opened"],
        )

    def test_new_request_id_can_open_same_locker_again(self) -> None:
        self.gateway._execute_command(1, "open", {"request_id": "first"})
        self.gateway._execute_command(1, "open", {"request_id": "second"})

        self.assertEqual(self.serial.writes, [b"OPEN,1\n", b"OPEN,1\n"])

    def test_concurrent_duplicate_request_sends_uart_once(self) -> None:
        original_open_locker = self.gateway._open_locker

        def delayed_open(locker_id: int) -> None:
            time.sleep(0.02)
            original_open_locker(locker_id)

        self.gateway._open_locker = delayed_open
        payload = {"request_id": "same-concurrent-request"}
        threads = [
            threading.Thread(target=self.gateway._execute_command, args=(1, "open", payload))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(self.serial.writes, [b"OPEN,1\n"])
        self.assertEqual(len(self.mqtt.messages), 2)

    def test_retained_open_command_is_never_sent_to_mega(self) -> None:
        message = FakeCommandMessage(1, "open", "stale-after-power-loss", retain=True)

        self.gateway._on_message(None, None, message)

        self.assertEqual(self.serial.writes, [])
        self.assertEqual(self.mqtt.messages, [])


if __name__ == "__main__":
    unittest.main()
