from __future__ import annotations

import json
import threading
import time
import unittest

from locker_hardware import MqttLockerController, MqttSettings


class FakeMessage:
    def __init__(self, locker_id: int, event: str) -> None:
        self.topic = f"smartlocker/lockers/{locker_id}/event"
        self.payload = json.dumps({"locker_id": locker_id, "event": event}).encode("utf-8")


class MqttLockerControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = MqttSettings(
            host="localhost",
            port=1883,
            topic_prefix="smartlocker",
            client_id="test",
            username="",
            password="",
            qos=1,
            keepalive=60,
            connect_timeout=0.1,
            command_timeout=0.1,
            door_close_timeout=1.0,
            use_tls=False,
        )
        self.controller = MqttLockerController(settings)
        self.actions: list[str] = []
        self.controller.open_locker = lambda locker_id: self.actions.append(f"open:{locker_id}")
        self.controller.mark_locker_used = lambda locker_id: self.actions.append(f"used:{locker_id}")

    def publish_door_event(self, locker_id: int, event: str) -> None:
        self.controller._on_message(None, None, FakeMessage(locker_id, event))

    def test_occupied_light_turns_on_only_after_door_opens_then_closes(self) -> None:
        worker = threading.Thread(target=self.controller.open_locker_for_dropoff, args=(1,))
        worker.start()
        time.sleep(0.02)

        self.publish_door_event(1, "door_closed")
        time.sleep(0.02)
        self.assertEqual(self.actions, ["open:1"])

        self.publish_door_event(1, "door_open")
        time.sleep(0.02)
        self.assertEqual(self.actions, ["open:1"])

        self.publish_door_event(1, "door_closed")
        worker.join(0.5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(self.actions, ["open:1", "used:1"])


if __name__ == "__main__":
    unittest.main()
