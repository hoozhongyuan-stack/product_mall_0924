import json
from io import BytesIO
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from django.test import SimpleTestCase

from wechat_integration.credentials import CredentialSnapshot
from wechat_integration.probe import ENDPOINT, MAX_BYTES, NoRedirect, _transport, check_credentials


class CredentialProbeTests(SimpleTestCase):
    snapshot = CredentialSnapshot('wx-app', 'secret-do-not-expose')

    def test_fixed_tls_post_false_and_no_redirect(self):
        reply = BytesIO(b'{"access_token":"sensitive","expires_in":7200}')
        reply.status = 200
        opener = Mock()
        opener.open.return_value = reply
        with patch('wechat_integration.probe.build_opener', return_value=opener) as build:
            self.assertEqual(check_credentials(self.snapshot).status, 'SUCCESS')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.method, 'POST')
        payload = json.loads(request.data)
        self.assertIs(payload['force_refresh'], False)
        self.assertEqual(payload['secret'], self.snapshot.secret)
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 5)
        self.assertTrue(any(isinstance(item, NoRedirect) for item in build.call_args.args))
        self.assertIsNone(NoRedirect().redirect_request(Request(ENDPOINT), None, 302, '', {}, 'https://evil.test'))

    def test_official_errors_and_malformed_responses_never_return_provider_text(self):
        cases = [(40013, 'INVALID_CREDENTIALS'), (40125, 'INVALID_CREDENTIALS'), (40001, 'INVALID_CREDENTIALS'),
                 (40164, 'IP_NOT_ALLOWED'), (89503, 'ADMIN_CONFIRMATION_REQUIRED'),
                 (89506, 'ADMIN_REJECTED'), (89507, 'ADMIN_REJECTED'),
                 (45009, 'PLATFORM_RATE_LIMITED'), (45011, 'PLATFORM_RATE_LIMITED'), (-1, 'PLATFORM_ERROR')]
        for error, expected in cases:
            with self.subTest(error=error), patch('wechat_integration.probe._transport',
                    return_value=json.dumps({'errcode': error, 'errmsg': 'secret-do-not-expose'}).encode()):
                result = check_credentials(self.snapshot)
                self.assertEqual(result.code, expected)
                self.assertNotIn(self.snapshot.secret, repr(result))
        for data in [b'\xff', b'broken', b'[]', b'{}', b'{"errcode":true}', b'{"errcode":"0"}',
                     b'{"access_token":"sensitive","expires_in":true}', b'{"access_token":"","expires_in":3}']:
            with patch('wechat_integration.probe._transport', return_value=data):
                self.assertEqual(check_credentials(self.snapshot).status, 'UNAVAILABLE')

    def test_transport_failure_and_body_limits(self):
        for exc in [URLError('sensitive'), TimeoutError('sensitive'), HTTPError(ENDPOINT, 302, '', {}, None)]:
            with patch('wechat_integration.probe._transport', side_effect=exc):
                self.assertEqual(check_credentials(self.snapshot).code, 'PLATFORM_UNAVAILABLE')
        for status, data in [(502, b'{}'), (200, b'x' * (MAX_BYTES+1))]:
            reply = BytesIO(data)
            reply.status = status
            opener = Mock()
            opener.open.return_value = reply
            with patch('wechat_integration.probe.build_opener', return_value=opener):
                with self.assertRaises(ValueError):
                    _transport({})
