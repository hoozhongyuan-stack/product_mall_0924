"""Privacy-safe member lookup and locked identity resolution for staff binding."""
from uuid import UUID
from django.db.models import Q
from .models import Member


def search_staff_members(search):
    if not isinstance(search, str) or not 2 <= len(search.strip()) <= 100:
        raise ValueError('请输入 2–100 字的会员编号或昵称。')
    search = search.strip()
    filters = Q(member_no__icontains=search) | Q(nickname__icontains=search)
    try:
        filters |= Q(id=UUID(search))
    except ValueError:
        pass
    rows = Member.objects.filter(filters, enabled=True).order_by('member_no')[:20]
    return [{'id': str(row.id), 'memberNo': row.member_no, 'name': row.nickname or row.member_no} for row in rows]


def resolve_staff_member(identifier, *, by_number=False):
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError('请选择已注册的会员。')
    identifier = identifier.strip()
    if by_number:
        if len(identifier) > 18:
            raise ValueError('会员编号格式不正确，请重新查找会员。')
        filters = {'member_no': identifier}
    else:
        try:
            filters = {'id': UUID(identifier)}
        except ValueError as exc:
            raise ValueError('会员 ID 格式不正确，请通过会员编号查找并选择会员。') from exc
    member = Member.objects.select_for_update().filter(**filters, enabled=True).first()
    if member is None:
        raise ValueError('该会员不存在或已停用。')
    return member
