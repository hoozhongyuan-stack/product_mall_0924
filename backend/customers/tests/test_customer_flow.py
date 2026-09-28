import json
from io import BytesIO
from unittest.mock import patch

from django.test import Client, RequestFactory, TestCase, override_settings
from django.core.management import call_command
from django.utils import timezone
from datetime import timedelta

from catalog.models import MemberGrade
from customers.auth import InvalidWechatCode, exchange_code, resolve_member
from customers.models import CustomerAddress, Member, MemberSession, WechatCodeUse
from customers.models import WechatLoginAttempt
from customers.views import addresses_view, address_detail_view, logout_view, me_view, wechat_login_view


def request(method, path, payload=None, token=None):
    kwargs = {"content_type": "application/json"}
    if token:
        kwargs["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    if method == "GET":
        return RequestFactory().get(path, **kwargs)
    return getattr(RequestFactory(), method.lower())(path, data=json.dumps(payload or {}), **kwargs)


def data(response):
    return json.loads(response.content)


@override_settings(WECHAT_MINI_APP_ID="wx-app", WECHAT_MINI_APP_SECRET="private-secret")
class CustomerFlowTests(TestCase):
    def setUp(self):
        self.grade = MemberGrade.objects.get(code="normal")

    @override_settings(WECHAT_MINI_APP_ID="", WECHAT_MINI_APP_SECRET="")
    def test_missing_wechat_configuration_fails_closed(self):
        result = wechat_login_view(request("POST", "/auth/wechat", {"code": "valid-code"}))
        self.assertEqual(result.status_code, 503)
        self.assertEqual(data(result)["error"]["code"], "WECHAT_UNAVAILABLE")
        self.assertEqual(Member.objects.count(), 0)

    @override_settings(WECHAT_MINI_APP_ID="wx-app", WECHAT_MINI_APP_SECRET="private-secret")
    @patch("customers.views.exchange_code", return_value="openid-one")
    def test_login_token_replay_and_disabled_member(self, exchange):
        payload = {"code": "fresh-login-code"}
        first = wechat_login_view(request("POST", "/auth/wechat", payload))
        self.assertEqual(first.status_code, 200, first.content)
        token = data(first)["data"]["accessToken"]
        self.assertEqual(data(first)["data"]["member"]["grade"]["code"], "normal")
        self.assertEqual(WechatCodeUse.objects.count(), 1)
        self.assertEqual(MemberSession.objects.count(), 1)
        self.assertNotIn(token, MemberSession.objects.get().token_digest)
        self.assertEqual(me_view(request("GET", "/me", token=token)).status_code, 200)
        self.assertIsNotNone(resolve_member(request("GET", "/me", token=token)))
        self.assertEqual(me_view(request("GET", "/me", token="bogus")).status_code, 401)
        replay = wechat_login_view(request("POST", "/auth/wechat", payload))
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(exchange.call_count, 1)
        member = Member.objects.get()
        member.enabled = False
        member.save(update_fields=["enabled"])
        self.assertEqual(me_view(request("GET", "/me", token=token)).status_code, 401)

    @override_settings(WECHAT_MINI_APP_ID="wx-app", WECHAT_MINI_APP_SECRET="private-secret")
    @patch("customers.views.exchange_code", side_effect=InvalidWechatCode("invalid"))
    def test_bad_wechat_code_does_not_create_identity(self, _exchange):
        result = wechat_login_view(request("POST", "/auth/wechat", {"code": "bad-code"}))
        self.assertEqual(result.status_code, 401)
        self.assertEqual(Member.objects.count(), 0)
        self.assertEqual(WechatCodeUse.objects.count(), 0)

    def test_addresses_are_owned_versioned_and_have_one_default(self):
        member = Member.objects.create(wechat_app_id="wx-app", wechat_openid="one", grade=self.grade)
        another = Member.objects.create(wechat_app_id="wx-app", wechat_openid="two", grade=self.grade)
        token = MemberSession.issue(member)[0]
        other_token = MemberSession.issue(another)[0]
        base = {"recipientName": "张女士", "phone": "13800001234", "province": "上海市",
                "city": "上海市", "district": "徐汇区", "detail": "漕溪北路 88 号", "isDefault": True}
        first = addresses_view(request("POST", "/addresses", base, token))
        self.assertEqual(first.status_code, 201, first.content)
        first_id = data(first)["data"]["id"]
        self.assertEqual(CustomerAddress.objects.filter(member=member, active=True, is_default=True).count(), 1)
        second = addresses_view(request("POST", "/addresses", {**base, "detail": "示例路 108 号"}, token))
        self.assertEqual(second.status_code, 201, second.content)
        second_id = data(second)["data"]["id"]
        self.assertEqual(CustomerAddress.objects.filter(member=member, active=True, is_default=True).count(), 1)
        self.assertEqual(CustomerAddress.objects.get(pk=second_id).is_default, True)
        self.assertEqual(data(addresses_view(request("GET", "/addresses", token=other_token)))["data"], [])
        denied = address_detail_view(request("PUT", "/addresses/one", {**base, "expectedRevision": 1}, other_token), first_id)
        self.assertEqual(denied.status_code, 404)
        stale = address_detail_view(request("PUT", "/addresses/one", {**base, "expectedRevision": 999}, token), first_id)
        self.assertEqual(stale.status_code, 409)
        remove = address_detail_view(request("DELETE", "/addresses/two", {"expectedRevision": 1}, token), second_id)
        self.assertEqual(remove.status_code, 200)
        self.assertTrue(CustomerAddress.objects.get(pk=first_id).is_default)
        self.assertFalse(CustomerAddress.objects.get(pk=second_id).active)
        self.assertEqual(address_detail_view(request("DELETE", "/addresses/two", {"expectedRevision": 1}, token), second_id).status_code, 404)

    def test_default_can_be_demoted_without_constraint_violation(self):
        member = Member.objects.create(wechat_app_id="wx-app", wechat_openid="one", grade=self.grade)
        token = MemberSession.issue(member)[0]
        base = {"recipientName": "张女士", "phone": "13800001234", "province": "上海市",
                "city": "上海市", "district": "徐汇区", "detail": "漕溪北路 88 号", "isDefault": True}
        first_id = data(addresses_view(request("POST", "/addresses", base, token)))["data"]["id"]
        second_id = data(addresses_view(request("POST", "/addresses", {**base, "detail": "示例路 108 号"}, token)))["data"]["id"]
        updated = address_detail_view(request("PUT", "/addresses/second", {
            **base, "detail": "示例路 108 号", "isDefault": False, "expectedRevision": 1}, token), second_id)
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertTrue(CustomerAddress.objects.get(pk=first_id).is_default)
        self.assertFalse(CustomerAddress.objects.get(pk=second_id).is_default)
        self.assertEqual(CustomerAddress.objects.filter(member=member, active=True, is_default=True).count(), 1)

    def test_expired_revoked_and_version_changed_tokens_do_not_authenticate(self):
        member = Member.objects.create(wechat_app_id="wx-app", wechat_openid="one", grade=self.grade)
        token = MemberSession.issue(member)[0]
        session = MemberSession.objects.get()
        session.expires_at = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=["expires_at"])
        self.assertIsNone(resolve_member(request("GET", "/me", token=token)))
        session.expires_at = timezone.now() + timedelta(days=1)
        session.revoked_at = timezone.now()
        session.save(update_fields=["expires_at", "revoked_at"])
        self.assertIsNone(resolve_member(request("GET", "/me", token=token)))
        session.revoked_at = None
        session.save(update_fields=["revoked_at"])
        member.auth_version += 1
        member.save(update_fields=["auth_version"])
        self.assertIsNone(resolve_member(request("GET", "/me", token=token)))

    def test_logout_revokes_current_token_only(self):
        member = Member.objects.create(wechat_app_id="wx-app", wechat_openid="one", grade=self.grade)
        first_token = MemberSession.issue(member)[0]
        second_token = MemberSession.issue(member)[0]
        self.assertEqual(logout_view(request("POST", "/auth/logout", token=first_token)).status_code, 200)
        self.assertIsNone(resolve_member(request("GET", "/me", token=first_token)))
        self.assertIsNotNone(resolve_member(request("GET", "/me", token=second_token)))

    @patch("customers.auth.urlopen")
    def test_code_exchange_reads_only_openid(self, urlopen):
        stream = BytesIO(json.dumps({"openid": "openid-one", "session_key": "never-store-this"}).encode())
        urlopen.return_value.__enter__.return_value = stream
        self.assertEqual(exchange_code("wx-code"), "openid-one")
        self.assertIn("grant_type=authorization_code", urlopen.call_args.args[0])
        self.assertEqual(Member.objects.count(), 0)

    def test_address_validation_and_authentication(self):
        member = Member.objects.create(wechat_app_id="wx-app", wechat_openid="one", grade=self.grade)
        token = MemberSession.issue(member)[0]
        self.assertEqual(addresses_view(request("GET", "/addresses")).status_code, 401)
        invalid = addresses_view(request("POST", "/addresses", {"recipientName": "", "phone": "1"}, token))
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(CustomerAddress.objects.count(), 0)

    def test_mapped_api_accepts_bearer_token_without_admin_csrf_cookie(self):
        member = Member.objects.create(wechat_app_id="wx-app", wechat_openid="one", grade=self.grade)
        token = MemberSession.issue(member)[0]
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get("/api/v1/app/me", HTTP_AUTHORIZATION=f"Bearer {token}").status_code, 200)
        payload = {"recipientName": "张女士", "phone": "13800001234", "province": "上海市",
                   "city": "上海市", "district": "徐汇区", "detail": "漕溪北路 88 号", "isDefault": True}
        result = client.post("/api/v1/app/addresses", data=json.dumps(payload),
                             content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(result.status_code, 201, result.content)
        self.assertEqual(client.get("/api/v1/app/addresses").status_code, 401)

    def test_member_token_is_scoped_to_current_wechat_app(self):
        member = Member.objects.create(wechat_app_id="wx-app", wechat_openid="one", grade=self.grade)
        token = MemberSession.issue(member)[0]
        with override_settings(WECHAT_MINI_APP_ID="another-app"):
            self.assertIsNone(resolve_member(request("GET", "/me", token=token)))

    def test_auth_retention_command_removes_old_records(self):
        member = Member.objects.create(wechat_app_id="wx-app", wechat_openid="one", grade=self.grade)
        code = WechatCodeUse.objects.create(code_digest="a" * 64, member=member)
        attempt = WechatLoginAttempt.objects.create(source_digest="b" * 64)
        token = MemberSession.issue(member)[0]
        WechatCodeUse.objects.filter(pk=code.pk).update(created_at=timezone.now() - timedelta(days=31))
        WechatLoginAttempt.objects.filter(pk=attempt.pk).update(created_at=timezone.now() - timedelta(days=2))
        MemberSession.objects.update(expires_at=timezone.now() - timedelta(days=31))
        call_command("purge_customer_auth", verbosity=0)
        self.assertEqual(WechatCodeUse.objects.count(), 0)
        self.assertEqual(WechatLoginAttempt.objects.count(), 0)
        self.assertEqual(MemberSession.objects.count(), 0)
        self.assertIsNone(resolve_member(request("GET", "/me", token=token)))
