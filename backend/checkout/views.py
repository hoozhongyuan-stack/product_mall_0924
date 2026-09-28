from accounts.security import error, parse_json, response, source_fingerprint
from accounts.views import method
from customers.auth import resolve_member
from django.views.decorators.csrf import csrf_exempt

from .service import QuoteValidationError, create_quote
from shipping.service import ShippingUnavailable
from .models import CheckoutQuote
from django.utils import timezone
from datetime import timedelta


@csrf_exempt
def quotes_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    member = resolve_member(request)
    if request.headers.get("Authorization") and not member:
        return error(request, 401, "SESSION_EXPIRED", "登录已失效，请重新登录。")
    source = source_fingerprint(request)
    if CheckoutQuote.objects.filter(source_digest=source,
                                    created_at__gte=timezone.now() - timedelta(minutes=15)).count() >= 60:
        return error(request, 429, "RATE_LIMITED", "报价请求过于频繁，请稍后重试。")
    try:
        body = parse_json(request)
        return response(request, create_quote(body, member, source), status=201)
    except ShippingUnavailable as exc:
        return error(request, 503, "SHIPPING_UNAVAILABLE", str(exc))
    except (ValueError, QuoteValidationError) as exc:
        return error(request, 400, "VALIDATION_FAILED", str(exc))
