"""HTTP envelopes and parsing only; permissions and business validation stay in domains."""
import json
import uuid

from django.http import JsonResponse


def _request_id(request):
    if not getattr(request, 'request_id', None):
        request.request_id = uuid.uuid4()
    return str(request.request_id)


def response(request, data=None, status=200, *, meta=None):
    payload = {'success': True, 'data': data if data is not None else {},
               'requestId': _request_id(request)}
    if meta is not None:
        payload['meta'] = meta
    return JsonResponse(payload, status=status)


def error(request, status, code, message, details=None):
    return JsonResponse({'success': False, 'error': {'code': code, 'message': message,
                         'details': details or []}, 'requestId': _request_id(request)}, status=status)


def method(request, *allowed):
    _request_id(request)
    if request.method not in allowed:
        result = error(request, 405, 'METHOD_NOT_ALLOWED', '请求方法不支持。')
        result['Allow'] = ', '.join(allowed)
        return result
    return None


def parse_json_object(request, *, max_bytes=32768, error_type=ValueError,
                      object_message='请求内容必须是对象。'):
    if request.content_type != 'application/json' or len(request.body) > max_bytes:
        raise error_type(f'请发送不超过 {max_bytes // 1024} KB 的 JSON 请求。')
    try:
        body = json.loads(request.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise error_type('JSON 格式不正确。') from exc
    if not isinstance(body, dict):
        raise error_type(object_message)
    return body


def offset_response(request, data, status=200, *, pagination_key=None, respond=response):
    """Opt-in at a page-number endpoint; retain its existing data shape unchanged."""
    pagination = data if pagination_key is None else data[pagination_key]
    meta = {key: pagination[key] for key in ('page', 'pageSize', 'total')}
    return respond(request, data, status, meta=meta)


def cursor_response(request, data, status=200, *, respond=response):
    """Opt-in at a cursor endpoint; never invent page counts or totals."""
    return respond(request, data, status, meta={'nextCursor': data['nextCursor']})
