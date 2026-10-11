"""Read-only scan context for ordinary catalog and published page endpoints."""
from uuid import UUID
from functools import wraps
from django.utils.cache import patch_vary_headers
from customers.auth import resolve_member
from .access import DiningError, checkout_context, sale_product_ids


def private_scanned_response(view):
    """Keep all supplied scan responses, including errors, out of HTTP caches."""
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        response=view(request,*args,**kwargs)
        if 'diningSessionId' in request.GET:
            response['Cache-Control']='private, no-store'
            patch_vary_headers(response,['Authorization'])
        return response
    return wrapped


def resolve_browsing_context(request):
    """Absent context is ordinary browsing; supplied invalid context never falls back."""
    if 'diningSessionId' not in request.GET:
        return None
    if len(request.GET.getlist('diningSessionId'))!=1 or 'storeId' in request.GET:
        raise DiningError('餐饮扫码参数不可重复或与前置仓参数混用。')
    try:
        identity=UUID(request.GET['diningSessionId'])
    except (ValueError,TypeError,AttributeError):
        raise DiningError('餐饮扫码会话格式不正确。') from None
    member=resolve_member(request)
    if member is None:
        raise DiningError('请先完成微信登录。','LOGIN_REQUIRED',401)
    store,snapshot=checkout_context(identity,member)
    return store,sale_product_ids(snapshot)
