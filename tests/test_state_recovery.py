from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from main import restore_locker_hardware_state


class LockerStateRecoveryTests(unittest.TestCase):
    def test_restore_uses_database_occupancy_without_opening_lockers(self) -> None:
        records = [SimpleNamespace(locker_id=2), SimpleNamespace(locker_id=7)]

        with (
            patch("main.get_active_records", return_value=records),
            patch("main.mark_locker_used") as mark_used,
            patch("main.mark_locker_empty") as mark_empty,
            patch("main.open_locker") as open_locker,
        ):
            restore_locker_hardware_state()

        self.assertEqual([call.args[0] for call in mark_used.call_args_list], [2, 7])
        self.assertEqual([call.args[0] for call in mark_empty.call_args_list], [1, 3, 4, 5, 6, 8])
        open_locker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
