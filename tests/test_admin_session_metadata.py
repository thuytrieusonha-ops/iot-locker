from __future__ import annotations

from datetime import datetime, timedelta
import unittest

import monitor


class AdminSessionMetadataTests(unittest.TestCase):
    def tearDown(self) -> None:
        monitor.admin_sessions.clear()

    def test_active_admin_session_html_shows_device_and_address(self) -> None:
        monitor.admin_sessions.clear()
        monitor.admin_sessions["token"] = {
            "session_id": "abc123",
            "username": "admin",
            "client_host": "192.168.1.50",
            "device_label": "Chrome trên Android",
            "created_at": datetime(2026, 7, 8, 9, 30),
            "expires_at": datetime.now() + timedelta(hours=1),
            "csrf_token": "csrf",
        }

        html = monitor.admin_active_sessions_html()

        self.assertIn("Thiết bị đang đăng nhập", html)
        self.assertIn("Chrome trên Android", html)
        self.assertIn("192.168.1.50", html)
        self.assertIn("abc123", html)

    def test_user_agent_summary_identifies_common_browser_and_device(self) -> None:
        label = monitor.summarize_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )

        self.assertEqual(label, "Chrome trên Windows")


if __name__ == "__main__":
    unittest.main()
