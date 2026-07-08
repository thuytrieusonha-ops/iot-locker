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

    def test_order_camera_supports_phone_rear_camera_capture(self) -> None:
        page = flow_page(
            title="Giao hàng",
            subtitle="Test",
            action="/giao-do",
            fields=[
                ("phone", "phone", "Số điện thoại", "Nhập số điện thoại", "numeric"),
            ],
            submit_label="Lưu hàng",
            enable_order_camera=True,
        )

        self.assertIn('accept="image/*"', page)
        self.assertIn('capture="environment"', page)
        self.assertIn("isMobileCaptureDevice()", page)
        self.assertIn("cameraFileInput.click()", page)

    def test_phone_browser_uses_native_keyboard_instead_of_kiosk_keyboard(self) -> None:
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

        self.assertIn("nativeMobileInputActive", page)
        self.assertIn("field.readOnly = false", page)
        self.assertIn('field.setAttribute("inputmode", nativeInputModeFor(field))', page)
        self.assertIn('if (mode === "numeric") return "numeric"', page)
        self.assertIn('if (mode === "email") return "email"', page)

    def test_order_code_can_be_filled_by_phone_qr_scanner(self) -> None:
        page = flow_page(
            title="Giao hàng",
            subtitle="Test",
            action="/giao-do",
            fields=[
                ("phone", "phone", "Số điện thoại", "Nhập số điện thoại", "numeric"),
                ("order_code", "order_code", "Mã đơn hàng", "Quét hoặc nhập mã đơn hàng", "full"),
            ],
            submit_label="Lưu hàng",
        )

        self.assertIn("Quét QR mã đơn hàng", page)
        self.assertIn('data-open-qr-scanner', page)
        self.assertIn('data-qr-target="#order_code"', page)
        self.assertIn('data-qr-scanner-video', page)
        self.assertIn("new window.BarcodeDetector", page)
        self.assertIn("qrScannerTarget.value = value", page)


if __name__ == "__main__":
    unittest.main()
