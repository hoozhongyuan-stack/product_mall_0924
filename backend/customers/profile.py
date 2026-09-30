"""Public member profile fields, independent of internal identity and relations."""
import secrets
import string
from zoneinfo import ZoneInfo

from django.utils import timezone


def member_number(at=None):
    stamp = (at or timezone.now()).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d%H%M%S')
    suffix = [secrets.choice(string.ascii_letters), secrets.choice(string.digits),
              secrets.choice(string.ascii_letters + string.digits)]
    secrets.SystemRandom().shuffle(suffix)
    return 'm' + stamp + ''.join(suffix)


def member_profile_data(member, *, admin=False):
    channel = "admin" if admin else "app"
    return {'memberNo': member.member_no, 'nickname': member.nickname, 'phone': member.phone,
            'profileRevision': member.profile_revision,
            'avatarUrl': f'/api/v1/{channel}/member-avatars/{member.avatar_id}/file' if member.avatar_id else ''}
