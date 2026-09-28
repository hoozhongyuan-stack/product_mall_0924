import json

from django.test import RequestFactory, SimpleTestCase

from common.http import cursor_response, error, method, offset_response, parse_json_object, response


class HttpContractTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get('/')
        method(self.request, 'GET')

    def payload(self, result):
        return json.loads(result.content)

    def test_explicit_pagination_preserves_all_legacy_data(self):
        for key in ('rows', 'items', 'list'):
            data = {key: [{'id': 'retained'}], 'page': 2, 'pageSize': 20, 'total': 21}
            result = self.payload(offset_response(self.request, data))
            self.assertEqual(result['data'], data)
            self.assertEqual(result['meta'], {'page': 2, 'pageSize': 20, 'total': 21})
            self.assertNotIn('meta', self.payload(response(self.request, data)))
        data = {'items': [], 'pagination': {'page': 1, 'pageSize': 10, 'total': 0}}
        result = self.payload(offset_response(self.request, data, pagination_key='pagination'))
        self.assertEqual(result['data'], data)
        self.assertEqual(result['meta'], data['pagination'])

    def test_cursor_meta_does_not_invent_offset_fields(self):
        for cursor in (None, 'opaque-cursor'):
            data = {'items': [], 'nextCursor': cursor, 'timeZone': 'Asia/Shanghai'}
            result = self.payload(cursor_response(self.request, data))
            self.assertEqual(result['data'], data)
            self.assertEqual(result['meta'], {'nextCursor': cursor})

    def test_private_response_wrapper_keeps_cache_and_status(self):
        def private(*args, **kwargs):
            result = response(*args, **kwargs)
            result['Cache-Control'] = 'private, no-store'
            result['Vary'] = 'Authorization, Cookie'
            return result
        result = offset_response(self.request, {'page': 1, 'pageSize': 10, 'total': 0},
                                 respond=private, status=201)
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result['Cache-Control'], 'private, no-store')
        self.assertEqual(result['Vary'], 'Authorization, Cookie')

    def test_method_error_has_allow_and_stable_request_id(self):
        request = self.factory.delete('/')
        result = method(request, 'GET', 'POST')
        self.assertEqual(result.status_code, 405)
        self.assertEqual(result['Allow'], 'GET, POST')
        payload = self.payload(result)
        self.assertFalse(payload['success'])
        self.assertEqual(payload['requestId'], str(request.request_id))
        self.assertEqual(payload['error']['code'], 'METHOD_NOT_ALLOWED')
        self.assertEqual(self.payload(error(request, 403, 'FORBIDDEN', '拒绝'))['requestId'], payload['requestId'])

    def test_json_limits_are_bytes_and_boundary_is_inclusive(self):
        for limit in (8192, 32768, 262144):
            raw = b'{"x":"' + b'a' * (limit - 8) + b'"}'
            self.assertEqual(len(raw), limit)
            request = self.factory.post('/', raw, content_type='application/json')
            self.assertEqual(len(parse_json_object(request, max_bytes=limit)['x']), limit - 8)
            request = self.factory.post('/', raw + b' ', content_type='application/json')
            with self.assertRaisesRegex(ValueError, f'{limit // 1024} KB'):
                parse_json_object(request, max_bytes=limit)

    def test_json_rejects_invalid_content_encoding_shape_and_media_type(self):
        for raw, expected in ((b'', 'JSON 格式'), (b'{', 'JSON 格式'), (b'{"x":"\xff"}', 'JSON 格式'),
                              (b'[]', '必须是对象'), (b'null', '必须是对象'), (b'"x"', '必须是对象')):
            with self.subTest(raw=raw):
                request = self.factory.generic('POST', '/', raw, CONTENT_TYPE='application/json')
                with self.assertRaisesRegex(ValueError, expected):
                    parse_json_object(request)
        request = self.factory.post('/', b'{}', content_type='text/plain')
        with self.assertRaisesRegex(ValueError, '32 KB'):
            parse_json_object(request)

    def test_domain_error_types_and_messages_are_preserved(self):
        from catalog.validation import CatalogError
        from catalog.views import body
        from pages.storefront_views import _body
        from pages.validation import PageConfigError
        request = self.factory.post('/', b'[]', content_type='application/json')
        with self.assertRaisesRegex(CatalogError, '请求内容必须是对象') as caught:
            body(request)
        self.assertEqual(caught.exception.code, 'VALIDATION_FAILED')
        self.assertEqual(caught.exception.status, 400)
        with self.assertRaisesRegex(PageConfigError, '请求内容须为对象'):
            _body(request)

    def test_compatibility_imports_share_transport_implementation(self):
        from accounts import security, views
        from customers import views as customer_views
        self.assertIs(security.response, response)
        self.assertIs(security.error, error)
        self.assertIs(views.method, method)
        self.assertIs(customer_views.success, response)
        self.assertIs(customer_views.failure, error)
        self.assertIs(customer_views.prepare, method)

    def test_domain_parsers_keep_existing_size_limits_and_error_types(self):
        from accounts.security import parse_json
        from catalog.views import body
        from catalog.validation import CatalogError
        from customers.views import parse_body
        from pages.views import _body as page_body
        from pages.startup_views import _body as startup_body, StartupError
        from pages.storefront_views import _body as storefront_body
        from pages.validation import PageConfigError
        parsers = ((parse_json, 32768, ValueError), (body, 262144, CatalogError),
                   (parse_body, 8192, ValueError), (page_body, 262144, PageConfigError),
                   (startup_body, 8192, StartupError), (storefront_body, 8192, PageConfigError))
        for parser, limit, error_type in parsers:
            with self.subTest(parser=parser.__module__):
                raw = b'{"x":"' + b'a' * (limit - 8) + b'"}'
                request = self.factory.post('/', raw, content_type='application/json')
                self.assertEqual(len(parser(request)['x']), limit - 8)
                request = self.factory.post('/', raw + b' ', content_type='application/json')
                with self.assertRaisesRegex(error_type, f'{limit // 1024} KB'):
                    parser(request)
