import json
import re
import struct
import tempfile
import zlib
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from catalog.models import MemberGrade
from customers.models import Member, MemberSession, MemberProfileQuota
from django.db import IntegrityError
from customers.views import me_view, logout_view
from customers.profile_views import profile_view, avatar_view, avatar_file_view
from customers.profile import member_profile_data
from .test_customer_flow import request, data


def png():
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b'')


@override_settings(WECHAT_MINI_APP_ID='wx-app', WECHAT_MINI_APP_SECRET='secret')
class ProfileTests(TestCase):
    def setUp(self):
        self.member = Member.objects.create(wechat_app_id='wx-app', wechat_openid='one', grade=MemberGrade.objects.get(code='normal'))
        self.token = MemberSession.issue(self.member)[0]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = override_settings(MEDIA_ROOT=Path(self.temp.name))
        self.settings.enable()
        self.addCleanup(self.settings.disable)

    def test_member_number_and_public_dto_preserve_private_identity(self):
        number = self.member.member_no
        self.assertRegex(number, r'^m\d{14}[A-Za-z0-9]{3}$')
        self.assertTrue(re.search('[A-Za-z]', number[-3:]))
        self.assertTrue(re.search('[0-9]', number[-3:]))
        dto = member_profile_data(self.member)
        self.assertEqual(dto['memberNo'], number)
        self.assertNotIn('wechat_openid', dto)
        self.assertEqual(data(me_view(request('GET', '/me', token=self.token)))['data']['memberNo'], number)

    def test_profile_auth_validation_and_revision(self):
        self.assertEqual(profile_view(request('GET', '/profile')).status_code, 401)
        first = profile_view(request('PATCH', '/profile', {'nickname': ' 小王 ', 'expectedRevision': 1}, self.token))
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(data(first)['data']['nickname'], '小王')
        self.assertEqual(data(first)['data']['profileRevision'], 2)
        stale = profile_view(request('PATCH', '/profile', {'nickname': '小张', 'expectedRevision': 1}, self.token))
        self.assertEqual(stale.status_code, 409)
        for nickname in ('', 'x' * 41, '<script>', 'a\nline'):
            self.assertEqual(profile_view(request('PATCH', '/profile', {'nickname': nickname, 'expectedRevision': 2}, self.token)).status_code, 400)
        self.assertEqual(profile_view(request('PATCH', '/profile', {'nickname': '名字', 'phone': '13800000000', 'expectedRevision': 2}, self.token)).status_code, 400)

    def upload(self, content, revision='1'):
        return avatar_view(RequestFactory().post('/avatar', {'file': SimpleUploadedFile('avatar.png', content, content_type='image/png'), 'expectedRevision': revision}, HTTP_AUTHORIZATION=f'Bearer {self.token}'))

    @patch('customers.profile_views.verify_decodable')
    def test_avatar_verified_stored_served_and_replacement_hidden(self, decoder):
        first = self.upload(png())
        self.assertEqual(first.status_code, 201, first.content)
        decoder.assert_called_once()
        url = data(first)['data']['avatarUrl']
        avatar_id = url.split('/')[-2]
        served = avatar_file_view(RequestFactory().get(url), avatar_id)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served['Content-Type'], 'image/png')
        self.assertEqual(served['X-Content-Type-Options'], 'nosniff')
        for closer in served._resource_closers:
            closer()
        self.assertEqual(self.upload(png(), '1').status_code, 409)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.upload(png(), '2').status_code, 201)
        self.assertEqual(avatar_file_view(RequestFactory().get(url), avatar_id).status_code, 404)
        self.assertEqual(len(list(Path(self.temp.name).rglob('*.png'))), 1)

    def test_avatar_rejects_invalid_oversize_and_guest(self):
        self.assertEqual(self.upload(b'<svg>bad</svg>').status_code, 400)
        self.assertEqual(self.upload(b'x' * (2 * 1024 * 1024 + 1)).status_code, 400)
        self.assertEqual(avatar_view(RequestFactory().post('/avatar', {'file': SimpleUploadedFile('x.png', png())})).status_code, 401)
        self.assertEqual(list(Path(self.temp.name).rglob('*.png')), [])

    def test_real_http_avatar_decode_and_profile_update(self):
        from django.test import Client
        client = Client()
        result = client.put('/api/v1/app/member/profile', json.dumps({'nickname': '用户昵称', 'expectedRevision': 1}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(result.status_code, 200, result.content)
        uploaded = client.post('/api/v1/app/member/avatar', {
            'file': SimpleUploadedFile('avatar.png', png(), content_type='application/octet-stream'),
            'expectedRevision': '2'}, HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        self.assertTrue(uploaded.json()['data']['avatarUrl'].startswith('/api/v1/app/member-avatars/'))
        self.assertEqual(client.get('/api/v1/app/member/profile', HTTP_AUTHORIZATION=f'Bearer {self.token}').json()['data']['nickname'], '用户昵称')

    def test_member_number_collision_retries_without_hiding_identity_constraint(self):
        with patch('customers.models.member_number', return_value='m20260930130512A7x'):
            other = Member.objects.create(member_no=self.member.member_no, wechat_app_id='wx-app',
                wechat_openid='other', grade=self.member.grade)
        self.assertEqual(other.member_no, 'm20260930130512A7x')
        with self.assertRaises(IntegrityError):
            Member.objects.create(wechat_app_id='wx-app', wechat_openid='one', grade=self.member.grade)

    def test_existing_member_number_migration_uses_creation_time_preserves_relations(self):
        from datetime import datetime, timezone as datetime_timezone
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        Member.objects.filter(pk=self.member.pk).update(created_at=datetime(2026, 9, 30, 5, 5, 12, tzinfo=datetime_timezone.utc))
        migration = import_module('customers.migrations.0007_member_profile')
        migration.populate_numbers(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))
        self.member.refresh_from_db()
        self.assertTrue(self.member.member_no.startswith('m20260930130512'))
        self.assertEqual(MemberSession.objects.get().member_id, self.member.id)

    def test_profile_and_avatar_write_quotas_are_separate_and_bounded(self):
        from django.utils import timezone
        window = timezone.now().replace(second=0, microsecond=0)
        MemberProfileQuota.objects.create(member=self.member, scope='profile', window_start=window, count=30)
        result = profile_view(request('PATCH', '/profile', {'nickname': '小王', 'expectedRevision': 1}, self.token))
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result['Retry-After'], '60')
        MemberProfileQuota.objects.create(member=self.member, scope='avatar', window_start=window, count=6)
        self.assertEqual(self.upload(png()).status_code, 429)
        self.assertEqual(list(Path(self.temp.name).rglob('*.png')), [])

    @patch('customers.profile_views.verify_decodable')
    @patch('customers.profile_views.resolve_member', return_value=None)
    def test_revoked_session_during_decode_prevents_avatar_commit(self, resolver, decoder):
        # require_member uses customers.auth.resolve_member; the write recheck is isolated.
        self.assertEqual(self.upload(png()).status_code, 401)
        self.member.refresh_from_db()
        self.assertIsNone(self.member.avatar_id)
        self.assertEqual(list(Path(self.temp.name).rglob('*')), [Path(self.temp.name) / 'member-avatars'])

    def test_logout_revokes_session_profile_access(self):
        self.assertEqual(logout_view(request('POST', '/logout', token=self.token)).status_code, 200)
        self.assertEqual(profile_view(request('GET', '/profile', token=self.token)).status_code, 401)
