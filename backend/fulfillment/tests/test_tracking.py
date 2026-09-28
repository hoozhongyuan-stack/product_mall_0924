"""Provider protocol tests never contact a real logistics account."""
import base64
import hashlib
import json
from unittest.mock import patch
from urllib.error import URLError

from django.test import SimpleTestCase

from fulfillment.tracking import TrackingUnavailable, query_kdniao


class TrackingAdapterTests(SimpleTestCase):
    def test_signed_request_and_bounded_events(self):
        calls = []

        def fake_post(url, body, timeout):
            calls.append((url, body, timeout))
            return json.dumps({"Success": True, "State": "2", "Traces": [
                {"AcceptTime": "2026-09-26 12:00:00", "AcceptStation": "已揽收", "Remark": ""},
                {"AcceptTime": "2026-09-27 09:00:00", "AcceptStation": "运输中", "Remark": ""},
            ]}).encode()

        with patch("fulfillment.tracking._post", side_effect=fake_post):
            result = query_kdniao("SF", "SF123456", "merchant-id", "private-key")
        self.assertEqual(result["status"], "IN_TRANSIT")
        self.assertEqual(len(result["events"]), 2)
        from urllib.parse import parse_qs
        fields = parse_qs(calls[0][1].decode())
        request_data = fields["RequestData"][0]
        expected = base64.b64encode(hashlib.md5((request_data + "private-key").encode()).digest()).decode()
        self.assertEqual(fields["DataSign"], [expected])
        self.assertEqual(fields["RequestType"], ["1002"])
        self.assertEqual(json.loads(request_data), {"ShipperCode": "SF", "LogisticCode": "SF123456"})
        self.assertTrue(calls[0][0].startswith("https://api.kdniao.com/"))

    def test_provider_errors_do_not_expose_raw_response(self):
        for payload in (b"not-json", b"{}", b'{"Success":false,"Reason":"private details"}',
                        json.dumps({"Success": True, "State": "2", "Traces": [
                            {"AcceptTime": "<bad>", "AcceptStation": "bad"}]}).encode()):
            with self.subTest(payload=payload), patch("fulfillment.tracking._post", return_value=payload):
                with self.assertRaises(TrackingUnavailable):
                    query_kdniao("SF", "SF123456", "merchant-id", "private-key")
        with patch("fulfillment.tracking._post", side_effect=URLError("private network details")):
            with self.assertRaises(TrackingUnavailable):
                query_kdniao("SF", "SF123456", "merchant-id", "private-key")

    def test_no_events_is_distinct_from_provider_failure(self):
        with patch("fulfillment.tracking._post", return_value=b'{"Success":true,"State":"0","Traces":[]}'):
            self.assertEqual(query_kdniao("SF", "SF123456", "id", "key")["status"], "NO_EVENTS")
