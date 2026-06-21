from __future__ import annotations

import unittest

from main import flow_page


class FormValidationUiTests(unittest.TestCase):
    def test_required_fields_use_kiosk_popup_validation(self) -> None:
        page = flow_page(
            title="Giao hàng",
            subtitle="Test",
            action="/giao-do",
            fields=[
                ("phone", "phone", "Số điện thoại", "Nhập số điện thoại", "numeric"),
                ("order_code", "order_code", "Mã đơn hàng", "Nhập mã đơn hàng", "full"),
            ],
            submit_label="Lưu hàng",
        )

        self.assertIn('form.noValidate = true', page)
        self.assertIn('showFormValidationModal(labels, missing[0])', page)
        self.assertIn('Chưa nhập đủ thông tin', page)


if __name__ == "__main__":
    unittest.main()
