import hashlib
import re
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from common.http import response as success, error as failure, method as prepare, parse_json_object
from catalog.models import MemberGrade
from wechat_integration.credentials import CredentialsUnavailable, effective_credentials, integration_row

from .auth import (InvalidWechatCode, WechatExchangeUnavailable, code_digest,
                   exchange_code, login_source_allowed, require_member)
from .profile import member_profile_data
from .models import CustomerAddress, Member, MemberSession, WechatCodeUse


def parse_body(request):
    return parse_json_object(request, max_bytes=8192)


def grade_data(grade):
    return {"id": str(grade.id), "code": grade.code, "name": grade.name}


def member_data(member):
    return {"id": str(member.id), "grade": grade_data(member.grade), **member_profile_data(member)}


def address_data(item):
    return {"id": str(item.id), "recipientName": item.recipient_name, "phone": item.phone,
            "province": item.province, "city": item.city, "district": item.district,
            "detail": item.detail, "isDefault": item.is_default, "revision": item.revision,
            'latitude': str(item.latitude) if item.latitude is not None else None,
            'longitude': str(item.longitude) if item.longitude is not None else None}


def address_fields(body, *, update=False):
    expected = {"recipientName", "phone", "province", "city", "district", "detail", "isDefault"}
    allowed = expected | ({"expectedRevision"} if update else set())
    if not allowed.issubset(body) or set(body) - allowed - {'latitude', 'longitude'}:
        raise ValueError("请填写完整的收货地址，且不要添加未支持的字段。")
    limits = {"recipientName": 40, "phone": 20, "province": 40, "city": 40,
              "district": 40, "detail": 200}
    result = {}
    for key, limit in limits.items():
        value = body[key]
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
            raise ValueError(f"{key} 格式不正确。")
        result[key] = value.strip()
    if not re.fullmatch(r"1[3-9]\d{9}", result["phone"]):
        raise ValueError("请填写有效的中国大陆手机号码。")
    if type(body["isDefault"]) is not bool:
        raise ValueError("isDefault 必须为布尔值。")
    result["isDefault"] = body["isDefault"]
    latitude, longitude = body.get('latitude'), body.get('longitude')
    if (latitude is None) != (longitude is None):
        raise ValueError('请填写完整的地址定位。')
    for key, value, limit in [('latitude', latitude, 90), ('longitude', longitude, 180)]:
        try:
            coordinate = Decimal(str(value)) if value is not None else None
            if coordinate is not None and (not coordinate.is_finite() or abs(coordinate) > limit or coordinate.as_tuple().exponent < -6):
                raise ValueError('地址经纬度不正确。')
        except InvalidOperation as exc:
            raise ValueError('地址经纬度不正确。') from exc
        result[key] = coordinate
    return result


def expected_revision(body):
    revision = body.get("expectedRevision")
    if type(revision) is not int or revision < 1:
        raise ValueError("expectedRevision 格式不正确。")
    return revision


def assign_address(item, fields):
    item.recipient_name = fields["recipientName"]
    item.phone = fields["phone"]
    item.province = fields["province"]
    item.city = fields["city"]
    item.district = fields["district"]
    item.detail = fields["detail"]
    item.latitude = fields['latitude']
    item.longitude = fields['longitude']


@csrf_exempt
def wechat_login_view(request):
    bad = prepare(request, "POST")
    if bad:
        return bad
    try:
        body = parse_body(request)
    except ValueError as exc:
        return failure(request, 400, "VALIDATION_FAILED", str(exc))
    code = body.get("code")
    if set(body) != {"code"} or not isinstance(code, str) or not 6 <= len(code) <= 256 or not code.isascii():
        return failure(request, 400, "VALIDATION_FAILED", "微信登录凭证格式不正确。")
    try:
        credentials = effective_credentials()
    except CredentialsUnavailable:
        return failure(request, 503, "WECHAT_UNAVAILABLE", "微信登录配置暂不可用。")
    app_id, secret = credentials.app_id, credentials.secret
    if not app_id or not secret:
        return failure(request, 503, "WECHAT_UNAVAILABLE", "微信登录尚未配置。")
    digest = code_digest(app_id, code)
    if WechatCodeUse.objects.filter(code_digest=digest).exists():
        return failure(request, 409, "WECHAT_CODE_USED", "微信登录凭证已使用，请重新登录。")
    if not login_source_allowed(request):
        return failure(request, 429, "RATE_LIMITED", "登录尝试过多，请稍后再试。")
    try:
        openid = exchange_code(code, credentials=credentials)
    except InvalidWechatCode:
        return failure(request, 401, "WECHAT_CODE_INVALID", "微信登录凭证已失效，请重新登录。")
    except WechatExchangeUnavailable:
        return failure(request, 503, "WECHAT_UNAVAILABLE", "微信身份校验暂时不可用，请重试。")
    try:
        with transaction.atomic():
            # All first-member writes and credential updates serialize on the same singleton.
            if effective_credentials(integration_row(lock=True)) != credentials:
                return failure(request, 503, "WECHAT_UNAVAILABLE", "微信登录配置已变化，请重新登录。")
            grade = MemberGrade.objects.filter(code="normal", enabled=True).first()
            if grade is None:
                return failure(request, 503, "MEMBER_GRADE_UNAVAILABLE", "普通会员等级尚未配置。")
            member, _ = Member.objects.select_related("grade").get_or_create(
                wechat_app_id=app_id, wechat_openid=openid, defaults={"grade": grade})
            if not member.enabled:
                return failure(request, 403, "MEMBER_DISABLED", "当前会员账号不可使用。")
            WechatCodeUse.objects.create(code_digest=digest, member=member)
            token, expires_at = MemberSession.issue(member)
    except CredentialsUnavailable:
        return failure(request, 503, "WECHAT_UNAVAILABLE", "微信登录配置暂不可用。")
    except IntegrityError:
        return failure(request, 409, "WECHAT_CODE_USED", "微信登录凭证已使用，请重新登录。")
    return success(request, {"accessToken": token, "expiresAt": expires_at.isoformat(),
                             "member": member_data(member)})


@csrf_exempt
def me_view(request):
    bad = prepare(request, "GET")
    if bad:
        return bad
    member, bad = require_member(request)
    return bad or success(request, member_data(member))


@csrf_exempt
def logout_view(request):
    bad = prepare(request, "POST")
    if bad:
        return bad
    member, bad = require_member(request)
    if bad:
        return bad
    token = request.META["HTTP_AUTHORIZATION"].removeprefix("Bearer ")
    MemberSession.objects.filter(member=member, token_digest=hashlib.sha256(token.encode()).hexdigest(),
                                 revoked_at__isnull=True).update(revoked_at=timezone.now())
    return success(request, {"loggedOut": True})


@csrf_exempt
def addresses_view(request):
    bad = prepare(request, "GET", "POST")
    if bad:
        return bad
    member, bad = require_member(request)
    if bad:
        return bad
    if request.method == "GET":
        items = CustomerAddress.objects.filter(member=member, active=True).order_by("-is_default", "-updated_at")
        return success(request, [address_data(item) for item in items])
    try:
        fields = address_fields(parse_body(request))
    except ValueError as exc:
        return failure(request, 400, "VALIDATION_FAILED", str(exc))
    with transaction.atomic():
        locked_member = Member.objects.select_for_update().get(pk=member.pk)
        if not locked_member.enabled:
            return failure(request, 401, "LOGIN_REQUIRED", "请先完成微信登录。")
        if CustomerAddress.objects.filter(member=member, active=True).count() >= 20:
            return failure(request, 409, "ADDRESS_LIMIT", "最多保存 20 个收货地址。")
        make_default = fields["isDefault"] or not CustomerAddress.objects.filter(member=member, active=True).exists()
        if make_default:
            CustomerAddress.objects.filter(member=member, active=True, is_default=True).update(
                is_default=False, revision=F("revision") + 1)
        item = CustomerAddress(member=member, is_default=make_default)
        assign_address(item, fields)
        item.save()
    return success(request, address_data(item), 201)


@csrf_exempt
def address_detail_view(request, address_id):
    bad = prepare(request, "PUT", "DELETE")
    if bad:
        return bad
    member, bad = require_member(request)
    if bad:
        return bad
    try:
        body = parse_body(request)
        revision = expected_revision(body)
        fields = address_fields(body, update=True) if request.method == "PUT" else None
        if request.method == "DELETE" and set(body) != {"expectedRevision"}:
            raise ValueError("删除地址只接受 expectedRevision。")
    except ValueError as exc:
        return failure(request, 400, "VALIDATION_FAILED", str(exc))
    with transaction.atomic():
        locked_member = Member.objects.select_for_update().get(pk=member.pk)
        if not locked_member.enabled:
            return failure(request, 401, "LOGIN_REQUIRED", "请先完成微信登录。")
        item = CustomerAddress.objects.select_for_update().filter(pk=address_id, member=member, active=True).first()
        if item is None:
            return failure(request, 404, "NOT_FOUND", "收货地址不存在。")
        if item.revision != revision:
            return failure(request, 409, "REVISION_CONFLICT", "收货地址已被修改，请刷新后重试。")
        if request.method == "PUT":
            assign_address(item, fields)
            if fields["isDefault"]:
                CustomerAddress.objects.filter(member=member, active=True, is_default=True).exclude(pk=item.pk).update(
                    is_default=False, revision=F("revision") + 1)
                item.is_default = True
                item.revision += 1
                item.save()
            elif item.is_default:
                replacement = CustomerAddress.objects.filter(member=member, active=True).exclude(pk=item.pk).order_by("-updated_at").first()
                if replacement:
                    item.is_default = False
                    item.revision += 1
                    item.save()
                    replacement.is_default = True
                    replacement.revision += 1
                    replacement.save(update_fields=["is_default", "revision", "updated_at"])
                else:
                    item.revision += 1
                    item.save()
            else:
                item.revision += 1
                item.save()
            return success(request, address_data(item))
        was_default = item.is_default
        item.active = False
        item.is_default = False
        item.revision += 1
        item.save(update_fields=["active", "is_default", "revision", "updated_at"])
        if was_default:
            replacement = CustomerAddress.objects.filter(member=member, active=True).order_by("-updated_at").first()
            if replacement:
                replacement.is_default = True
                replacement.revision += 1
                replacement.save(update_fields=["is_default", "revision", "updated_at"])
    return success(request, {"deleted": True})
