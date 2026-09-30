"""Authenticated profile editing with verified, bounded avatar storage."""
import logging
import os
import tempfile
import uuid
from pathlib import Path
from django.utils import timezone

from django.conf import settings
from django.db import transaction
from django.http import FileResponse
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.vary import vary_on_headers, vary_on_cookie
from common.http import method, response, error, parse_json_object
from accounts.security import require
from catalog.media import inspect_file, verify_decodable
from catalog.validation import CatalogError
from .auth import require_member, resolve_member
from .models import Member, MemberProfileQuota
from .views import member_data

logger = logging.getLogger(__name__)

MAX_AVATAR_BYTES = 2 * 1024 * 1024


def rate_limit(request, member, scope):
    limits = {'profile': 30, 'avatar': 6}
    window = timezone.now().replace(second=0, microsecond=0)
    with transaction.atomic():
        row, _ = MemberProfileQuota.objects.select_for_update().get_or_create(
            member=member, scope=scope, defaults={'window_start': window})
        count = row.count if row.window_start == window else 0
        if count >= limits[scope]:
            result = error(request, 429, 'RATE_LIMITED', '修改过于频繁，请稍后再试。')
            result['Retry-After'] = '60'
            return result
        MemberProfileQuota.objects.filter(pk=row.pk).update(window_start=window, count=count + 1)
    return None


def revision(value):
    if type(value) is not int or value < 1:
        raise ValueError('资料修订号不正确，请刷新后重试。')
    return value


def recheck(request, member, expected):
    current = resolve_member(request)
    if current is None or current.pk != member.pk:
        return error(request, 401, 'LOGIN_REQUIRED', '请重新登录。')
    if member.profile_revision != expected:
        return error(request, 409, 'REVISION_CONFLICT', '个人资料已变化，请刷新后重试。')
    return None


@csrf_exempt
@cache_control(private=True, no_store=True)
@vary_on_headers('Authorization')
def profile_view(request):
    bad = method(request, 'GET', 'PUT', 'PATCH')
    if bad:
        return bad
    member, bad = require_member(request)
    if bad:
        return bad
    if request.method == 'GET':
        return response(request, member_data(member))
    bad = rate_limit(request, member, 'profile')
    if bad:
        return bad
    try:
        body = parse_json_object(request, max_bytes=4096)
        if set(body) != {'nickname', 'expectedRevision'}:
            raise ValueError('请提交昵称和资料修订号。')
        expected = revision(body['expectedRevision'])
        nickname = body['nickname']
        if not isinstance(nickname, str) or not 1 <= len(nickname.strip()) <= 40:
            raise ValueError('昵称须为 1 至 40 个字符。')
        if any(ord(char) < 32 or char in '<>' for char in nickname):
            raise ValueError('昵称不能包含控制字符或 HTML 标签。')
    except ValueError as exc:
        return error(request, 400, 'VALIDATION_FAILED', str(exc))
    with transaction.atomic():
        locked = Member.objects.select_for_update().select_related('grade').get(pk=member.pk)
        bad = recheck(request, locked, expected)
        if bad:
            return bad
        locked.nickname = nickname.strip()
        locked.profile_revision += 1
        locked.save(update_fields=['nickname', 'profile_revision', 'updated_at'])
    return response(request, member_data(locked))


def remove_file(path):
    if path:
        try:
            (Path(settings.MEDIA_ROOT) / path).unlink(missing_ok=True)
        except OSError:
            logger.exception('Unable to remove retired member avatar')


@csrf_exempt
@cache_control(private=True, no_store=True)
@vary_on_headers('Authorization')
def avatar_view(request):
    bad = method(request, 'POST')
    if bad:
        return bad
    member, bad = require_member(request)
    if bad:
        return bad
    bad = rate_limit(request, member, 'avatar')
    if bad:
        return bad
    final = None
    temporary = None
    try:
        if int(request.META.get('CONTENT_LENGTH') or 0) > MAX_AVATAR_BYTES + 65536:
            raise ValueError('头像最大支持 2 MB。')
        if request.content_type != 'multipart/form-data':
            raise ValueError('请上传头像文件。')
        if set(request.POST) != {'expectedRevision'} or set(request.FILES) != {'file'}:
            raise ValueError('请提交单个头像文件和资料修订号。')
        expected = revision(int(request.POST['expectedRevision']))
        upload = request.FILES['file']
        if not 0 < upload.size <= MAX_AVATAR_BYTES:
            raise ValueError('头像最大支持 2 MB。')
        root = Path(settings.MEDIA_ROOT) / 'member-avatars'
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root, suffix='.tmp', delete=False) as target:
            temporary = Path(target.name)
            size = 0
            for chunk in upload.chunks():
                size += len(chunk)
                if size > MAX_AVATAR_BYTES:
                    raise ValueError('头像最大支持 2 MB。')
                target.write(chunk)
        content_type, extension, _, _ = inspect_file('IMAGE', temporary)
        verify_decodable(temporary, 'IMAGE')
        avatar_id = uuid.uuid4()
        relative = f'member-avatars/{avatar_id}.{extension}'
        final = Path(settings.MEDIA_ROOT) / relative
        with transaction.atomic():
            locked = Member.objects.select_for_update().select_related('grade').get(pk=member.pk)
            bad = recheck(request, locked, expected)
            if bad:
                return bad
            old_path = locked.avatar_path
            os.replace(temporary, final)
            locked.avatar_id = avatar_id
            locked.avatar_path = relative
            locked.avatar_content_type = content_type
            locked.profile_revision += 1
            locked.save(update_fields=['avatar_id', 'avatar_path', 'avatar_content_type', 'profile_revision', 'updated_at'])
            transaction.on_commit(lambda: remove_file(old_path))
        final = None
        return response(request, member_data(locked), 201)
    except (ValueError, CatalogError) as exc:
        return error(request, getattr(exc, 'status', 400), getattr(exc, 'code', 'VALIDATION_FAILED'), str(exc))
    except OSError:
        return error(request, 503, 'MEDIA_STORAGE_UNAVAILABLE', '头像存储暂不可用，请重试。')
    finally:
        if temporary:
            remove_file(temporary.relative_to(Path(settings.MEDIA_ROOT)))
        if final:
            remove_file(final.relative_to(Path(settings.MEDIA_ROOT)))


def avatar_file_view(request, avatar_id):
    bad = method(request, 'GET')
    if bad:
        return bad
    member = Member.objects.filter(avatar_id=avatar_id, enabled=True).first()
    if not member:
        return error(request, 404, 'NOT_FOUND', '头像不存在。')
    return avatar_file_response(request, member)


def avatar_file_response(request, member):
    try:
        source = (Path(settings.MEDIA_ROOT) / member.avatar_path).open('rb')
    except OSError:
        return error(request, 404, 'NOT_FOUND', '头像不存在。')
    result = FileResponse(source, content_type=member.avatar_content_type)
    result['X-Content-Type-Options'] = 'nosniff'
    result['Cache-Control'] = 'private, no-store'
    return result


@cache_control(private=True, no_store=True)
@vary_on_cookie
def admin_avatar_file_view(request, avatar_id):
    bad = method(request, 'GET')
    if bad:
        return bad
    _, bad = require(request, 'member.read')
    if bad:
        return bad
    member = Member.objects.filter(avatar_id=avatar_id).first()
    if not member:
        return error(request, 404, 'NOT_FOUND', '头像不存在。')
    return avatar_file_response(request, member)
