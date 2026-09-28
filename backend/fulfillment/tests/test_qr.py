"""Voucher images encode only the existing bearer code."""
import base64
from unittest.mock import patch

from django.test import SimpleTestCase

from fulfillment.qr import voucher_qr_data_url


class VoucherQrTests(SimpleTestCase):
    def test_png_data_url_has_real_png_header_and_bounded_size(self):
        data_url = voucher_qr_data_url("JDQKK66YYLAZ2GMLLNPWLYXU5P")
        self.assertTrue(data_url.startswith("data:image/png;base64,"))
        png = base64.b64decode(data_url.split(",", 1)[1], validate=True)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertLess(len(png), 65536)

    def test_qr_payload_is_the_code_without_order_or_member_details(self):
        code = "JDQKK66YYLAZ2GMLLNPWLYXU5P"
        with patch("fulfillment.qr.segno.make_qr") as make_qr:
            voucher_qr_data_url(code)
        make_qr.assert_called_once_with(code, error="m")
