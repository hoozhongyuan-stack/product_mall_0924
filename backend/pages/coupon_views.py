"""Published-version bound personalized coupon reads and private preview."""
from django.views.decorators.csrf import csrf_exempt
from common.http import method
from accounts.security import require, response, error
from customers.auth import require_member
from benefits.coupon_views import private_response
from .models import PagePublication
from .validation import PageConfigError, identifier
from .editor_v4 import validate_coupons, coupon_component_data
from .views import _body


@csrf_exempt
@private_response
def public_coupons_view(request, page_id=None):
    bad = method(request, 'GET')
    if bad:
        return bad
    try:
        if set(request.GET) != {'versionId'} or len(request.GET.getlist('versionId')) != 1:
            raise PageConfigError('请提供已读取的页面发布版本。')
        version_id = identifier(request.GET['versionId'], '页面版本')
        query = PagePublication.objects.select_related('current_version')
        query = query.filter(page_id=page_id, page__page_type='MICRO') if page_id else query.filter(page__page_type='HOME')
        publication = query.filter(current_version__isnull=False).first()
        if not publication:
            return error(request, 404, 'PAGE_UNPUBLISHED', '页面尚未发布。')
        if publication.current_version_id != version_id:
            return error(request, 409, 'PAGE_VERSION_CONFLICT', '页面已更新，请重新加载页面。')
        member = None
        if request.META.get('HTTP_AUTHORIZATION'):
            member, bad = require_member(request)
            if bad:
                return bad
        return response(request, {'pageId': str(publication.page_id) if page_id else 'home', 'versionId': str(version_id),
            'memberId': str(member.id) if member else None,
            'componentData': coupon_component_data(publication.current_version.config_json, member)})
    except PageConfigError as exc:
        return error(request, exc.status, exc.code, str(exc))


@private_response
def coupon_preview_view(request):
    bad = method(request, 'POST')
    if bad:
        return bad
    for permission in ('page.read', 'coupon.read'):
        _, bad = require(request, permission)
        if bad:
            return bad
    try:
        values = _body(request)
        if set(values) != {'props'}:
            raise PageConfigError('优惠券预览参数不正确。')
        validate_coupons(values['props'], publishing=False)
        from benefits.page_coupons import page_coupon_cards
        return response(request, {'coupons': page_coupon_cards(values['props'])})
    except PageConfigError as exc:
        return error(request, exc.status, exc.code, str(exc))
