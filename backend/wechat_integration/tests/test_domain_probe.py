import json
from io import BytesIO
from unittest.mock import Mock, patch
from urllib.error import URLError

from django.test import SimpleTestCase

from wechat_integration.credentials import CredentialSnapshot
from wechat_integration.domain_probe import MAX_BYTES, check_request_domains
from wechat_integration.probe import NoRedirect


class DomainProbeTests(SimpleTestCase):
    snapshot = CredentialSnapshot('wx0123456789abcdef', 'synthetic-private-secret')

    def probe(self, payload):
        with patch('wechat_integration.probe._transport', return_value=b'{"access_token":"private-token","expires_in":7200}'), \
                patch('wechat_integration.domain_probe._transport', return_value=json.dumps(payload).encode()):
            return check_request_domains(self.snapshot)

    def test_success_normalizes_only_request_domains_and_hides_token(self):
        result = self.probe({'errcode': 0, 'requestdomain': ['https://API.example.com:443/', 'https://api.example.com'],
                             'downloaddomain': ['https://download.example.com']})
        self.assertEqual(result.status, 'PASS')
        self.assertEqual(result.request_domains, ('https://api.example.com',))
        self.assertNotIn('private-token', repr(result))

    def test_empty_domain_list_is_valid_but_malformed_is_unverifiable(self):
        self.assertEqual(self.probe({'errcode': 0, 'requestdomain': []}).status, 'PASS')
        for payload in [{}, [], {'errcode': True}, {'requestdomain': 'https://api.example.com'},
                        {'requestdomain': ['https://api.example.com/path']},
                        {'requestdomain': ['https://secret@api.example.com']},
                        {'requestdomain': ['https://api.example.com:0']},
                        {'requestdomain': ['https://api.example.com'] * 257}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.probe(payload).status, 'UNVERIFIED')

    def test_safe_platform_error_mapping(self):
        for number, status, code in [(48001, 'UNVERIFIED', 'API_PERMISSION_DENIED'),
                (40001, 'BLOCKED', 'INVALID_CREDENTIALS'), (40164, 'BLOCKED', 'IP_NOT_ALLOWED'),
                (45009, 'UNVERIFIED', 'PLATFORM_RATE_LIMITED'), (-1, 'UNVERIFIED', 'PLATFORM_ERROR')]:
            result = self.probe({'errcode': number, 'errmsg': self.snapshot.secret})
            self.assertEqual((result.status, result.code, result.platform_error_code), (status, code, number))
            self.assertNotIn(self.snapshot.secret, repr(result))

    def test_token_failure_prevents_domain_request(self):
        with patch('wechat_integration.probe._transport', return_value=b'{"errcode":40125,"errmsg":"private"}'), \
                patch('wechat_integration.domain_probe._transport') as transport:
            result = check_request_domains(self.snapshot)
        self.assertEqual((result.status, result.code), ('BLOCKED', 'INVALID_CREDENTIALS'))
        transport.assert_not_called()

    def test_fixed_tls_transport_bounds_and_no_redirect(self):
        from wechat_integration.domain_probe import _transport
        reply = BytesIO(b'{"requestdomain":[]}')
        reply.status = 200
        opener = Mock()
        opener.open.return_value = reply
        with patch('wechat_integration.domain_probe.build_opener', return_value=opener) as build:
            _transport('private-token')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.weixin.qq.com/wxa/getwxadevinfo?access_token=private-token')
        self.assertEqual(json.loads(request.data), {'action': 'getserverdomain'})
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 5)
        self.assertTrue(any(isinstance(item, NoRedirect) for item in build.call_args.args))
        for status, body in [(502, b'{}'), (200, b'x' * (MAX_BYTES + 1))]:
            reply = BytesIO(body)
            reply.status = status
            opener.open.return_value = reply
            with patch('wechat_integration.domain_probe.build_opener', return_value=opener), self.assertRaises(ValueError):
                _transport('private-token')
        with patch('wechat_integration.probe._transport', return_value=b'{"access_token":"private-token","expires_in":7200}'), \
                patch('wechat_integration.domain_probe._transport', side_effect=URLError('private-token')):
            self.assertEqual(check_request_domains(self.snapshot).code, 'PLATFORM_UNAVAILABLE')
