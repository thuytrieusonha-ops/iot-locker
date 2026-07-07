from __future__ import annotations

import unittest
import errno
import socket
from types import SimpleNamespace
from unittest.mock import patch

from main import (
    claim_pending_unlock_command_for_display,
    deliver_pending_email_order,
    execute_admin_unlock_command,
    is_network_connectivity_error,
    restore_locker_hardware_state,
    retry_pending_email_deliveries_once,
)


class LockerStateRecoveryTests(unittest.TestCase):
    def test_restore_uses_database_occupancy_without_opening_lockers(self) -> None:
        records = [SimpleNamespace(locker_id=2), SimpleNamespace(locker_id=7)]

        with (
            patch("main.reconcile_locker_master_state", return_value={record.locker_id for record in records}),
            patch("main.mark_locker_used") as mark_used,
            patch("main.mark_locker_empty") as mark_empty,
            patch("main.open_locker") as open_locker,
        ):
            restore_locker_hardware_state()

        self.assertEqual([call.args[0] for call in mark_used.call_args_list], [2, 7])
        self.assertEqual([call.args[0] for call in mark_empty.call_args_list], [1, 3, 4, 5, 6, 8])
        open_locker.assert_not_called()

    def test_pending_admin_unlock_is_not_blocked_by_last_seen_state(self) -> None:
        command = SimpleNamespace(id=5)

        with (
            patch("main.fetch_pending_unlock_command", return_value=command),
            patch("main.last_seen_admin_command_id", return_value=182),
            patch("main.mark_admin_command_seen") as mark_seen,
        ):
            claimed = claim_pending_unlock_command_for_display()

        self.assertIs(claimed, command)
        mark_seen.assert_called_once_with(5)

    def test_admin_unlock_uses_linked_locker_targets(self) -> None:
        command = SimpleNamespace(id=12, action="unlock_single_locker", note="manual unlock")

        with (
            patch("main.complete_stale_unlock_commands") as complete_stale,
            patch("main.unlock_command_locker_ids", return_value=[2, 4]),
            patch("main.open_locker") as open_locker,
            patch("main.complete_unlock_command") as complete_command,
        ):
            result = execute_admin_unlock_command(command)

        complete_stale.assert_called_once_with(12)
        self.assertEqual([call.args[0] for call in open_locker.call_args_list], [2, 4])
        complete_command.assert_called_once()
        self.assertIn("Tủ 2 đã mở", result)
        self.assertIn("Tủ 4 đã mở", result)

    def test_network_email_errors_are_detected_separately(self) -> None:
        self.assertTrue(is_network_connectivity_error(OSError(errno.ENETUNREACH, "Network is unreachable")))
        self.assertTrue(is_network_connectivity_error(socket.gaierror("Temporary failure in name resolution")))

    def test_pending_email_worker_waits_until_network_returns(self) -> None:
        with (
            patch("main.using_database", return_value=True),
            patch("main.email_delivery_enabled", return_value=True),
            patch("main.has_network_connection", return_value=False),
            patch("main.pending_email_orders") as pending_orders,
        ):
            attempted, delivered = retry_pending_email_deliveries_once()

        self.assertEqual((attempted, delivered), (0, 0))
        pending_orders.assert_not_called()

    def test_pending_email_network_failure_stays_pending(self) -> None:
        record = SimpleNamespace(
            recipient_email="user@example.com",
            email_link_base_url="https://locker.example",
        )
        network_error = OSError(errno.ENETUNREACH, "Network is unreachable")

        with (
            patch("main.deliver_pickup_email", side_effect=network_error),
            patch("main.update_record_email_delivery") as update_delivery,
        ):
            delivered = deliver_pending_email_order(record)

        self.assertFalse(delivered)
        self.assertEqual(update_delivery.call_args.args[2], "pending")


if __name__ == "__main__":
    unittest.main()
