"""D4.0 real HTTP authorization, privacy and revisioned operations evidence."""
import json
from datetime import timedelta
from uuid import uuid4

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import AdminAccount, PermissionGroup, GroupPermission, AuditLog
from benefits.service import grant_points
from catalog.models import MemberGrade
from customers.models import Member, MemberSession, MemberRuleChange, ConsumptionEvent

PASSWORD = 'Synthetic D40 test passphrase 2026!'
RULES = '/api/v1/admin/member-rules'


@override_settings(WECHAT_MINI_APP_ID='wx-d40-api')
class D40ApiTests(TestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user('d40-api-owner', PASSWORD,
            display_name='合成管理员', kind=AdminAccount.Kind.OWNER)
        self.client = self.logged_client(self.owner.login_name)
        self.grade = MemberGrade.objects.get(code='normal')
        self.member = Member.objects.create(wechat_app_id='wx-d40-api',
            wechat_openid='synthetic-private-openid-A', grade=self.grade)
        self.other = Member.objects.create(wechat_app_id='wx-d40-api',
            wechat_openid='synthetic-private-openid-B', grade=self.grade)
        self.token = MemberSession.issue(self.member)[0]
        self.app = Client(enforce_csrf_checks=True)

    def logged_client(self, name):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get('/api/v1/admin/auth/csrf').status_code, 200)
        result = self.write(client, 'post', '/api/v1/admin/auth/login',
                            {'loginName': name, 'password': PASSWORD})
        self.assertEqual(result.status_code, 200)
        return client

    def write(self, client, verb, path, body, key=None, confirmation=None):
        headers = {'HTTP_X_CSRFTOKEN': client.cookies['csrftoken'].value}
        if key: headers['HTTP_IDEMPOTENCY_KEY'] = str(key)
        if confirmation: headers['HTTP_X_ACTION_CONFIRMATION'] = confirmation
        return getattr(client, verb)(path, data=json.dumps(body),
            content_type='application/json', **headers)

    def body(self):
        result = self.client.get(RULES)
        self.assertEqual(result.status_code, 200)
        data = result.json()['data']
        return {'expectedRevision': data['revision'], 'points': data['points'],
                'grades': [{'id': row['id'], 'minimumSpendFen': index*10000}
                           for index, row in enumerate(data['grades'])],
                'reason': '合成测试调整等级与积分规则'}

    def confirm(self, revision, object_id='member-rules', client=None):
        client = client or self.client
        response = self.write(client, 'post', '/api/v1/admin/auth/confirm',
            {'action': 'member.rules.update', 'password': PASSWORD,
             'objectId': object_id, 'revision': revision})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['data']['confirmationToken']

    def app_get(self, path, query=None):
        return self.app.get('/api/v1/app/member/' + path, query or {},
                            HTTP_AUTHORIZATION='Bearer ' + self.token)

    def test_actual_login_csrf_and_password_required_before_write(self):
        body = self.body()
        no_csrf = self.client.put(RULES, json.dumps(body), content_type='application/json',
                                  HTTP_IDEMPOTENCY_KEY=str(uuid4()))
        self.assertEqual(no_csrf.status_code, 403)
        no_password = self.write(self.client, 'put', RULES, body, uuid4())
        self.assertEqual(no_password.status_code, 403)
        self.assertEqual(MemberRuleChange.objects.count(), 0)

    def test_rule_revision_and_audit_committed_once_with_password(self):
        body = self.body(); key = uuid4(); confirmation = self.confirm(body['expectedRevision'])
        result = self.write(self.client, 'put', RULES, body, key, confirmation)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()['data']['revision'], body['expectedRevision'] + 1)
        self.assertEqual(MemberRuleChange.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action_code='member.rules.update', result='SUCCESS').count(), 1)
        self.assertEqual(MemberRuleChange.objects.get().reason, body['reason'])

    def test_identical_key_and_body_replay_after_consumed_confirmation(self):
        body = self.body(); key = uuid4(); confirmation = self.confirm(body['expectedRevision'])
        first = self.write(self.client, 'put', RULES, body, key, confirmation)
        second = self.write(self.client, 'put', RULES, body, key, confirmation)
        self.assertEqual(first.status_code, 200); self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()['data'], second.json()['data'])
        self.assertEqual(MemberRuleChange.objects.count(), 1)

    def test_same_key_different_body_conflicts_and_does_not_write(self):
        body = self.body(); key = uuid4(); confirmation = self.confirm(body['expectedRevision'])
        self.assertEqual(self.write(self.client, 'put', RULES, body, key, confirmation).status_code, 200)
        changed = {**body, 'reason': '不同操作的合成变更原因'}
        result = self.write(self.client, 'put', RULES, changed, key, confirmation)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()['error']['code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(MemberRuleChange.objects.count(), 1)

    def test_stale_revision_and_confirmation_target_binding_fail(self):
        body = self.body(); wrong = self.confirm(body['expectedRevision'], 'wrong-member-rules')
        result = self.write(self.client, 'put', RULES, body, uuid4(), wrong)
        self.assertEqual(result.status_code, 403)
        wrong_revision = self.confirm(body['expectedRevision'] + 1)
        self.assertEqual(self.write(self.client, 'put', RULES, body, uuid4(), wrong_revision).status_code, 403)
        confirmation = self.confirm(body['expectedRevision'])
        self.assertEqual(self.write(self.client, 'put', RULES, body, uuid4(), confirmation).status_code, 200)
        stale = self.write(self.client, 'put', RULES, body, uuid4(), confirmation)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['error']['code'], 'REVISION_CONFLICT')

    def test_readonly_actor_get_allowed_but_put_and_confirm_denied(self):
        group = PermissionGroup.objects.create(code='d40-reader', name='合成只读组')
        for code in ['member.read', 'member.rules.read']:
            GroupPermission.objects.create(group=group, code=code)
        actor = AdminAccount.objects.create_user('d40-reader', PASSWORD, display_name='合成只读员')
        actor.permission_groups.add(group)
        reader = self.logged_client(actor.login_name)
        self.assertEqual(reader.get(RULES).status_code, 200)
        self.assertEqual(reader.get('/api/v1/admin/members').status_code, 200)
        self.assertEqual(self.write(reader, 'put', RULES, self.body(), uuid4()).status_code, 403)
        denied = self.write(reader, 'post', '/api/v1/admin/auth/confirm',
            {'action': 'member.rules.update', 'password': PASSWORD,
             'objectId': 'member-rules', 'revision': 1})
        self.assertEqual(denied.status_code, 403)

    def test_anonymous_and_member_bearer_cannot_read_admin(self):
        self.assertEqual(Client().get('/api/v1/admin/members').status_code, 401)
        result = self.app.get('/api/v1/admin/members', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(result.status_code, 401)
        self.assertEqual(Client().get('/api/v1/app/member/overview').status_code, 401)

    def test_member_overview_no_identity_secrets_and_no_store(self):
        result = self.app_get('overview')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['data']['id'], str(self.member.id))
        text = result.content.decode()
        for secret in ['openid', 'wechat_app_id', 'wechat_openid', 'wx-d40-api', self.token,
                       'synthetic-private-openid-A', 'token_digest']:
            self.assertNotIn(secret, text)
        self.assertIn('no-store', result.get('Cache-Control', ''))
        self.assertIn('Authorization', result.get('Vary', ''))

    def test_member_points_filter_own_rows_and_safe_source(self):
        for member, source in [(self.member, 'private-grant-A'), (self.other, 'private-grant-B')]:
            grant_points(member, 10, timezone.now()+timedelta(days=1), source)
        result = self.app_get('points')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['data']['pagination']['total'], 1)
        for forbidden in ['private-grant-A', 'private-grant-B', str(self.other.id)]:
            self.assertNotIn(forbidden, result.content.decode())
        self.assertIn('no-store', result.get('Cache-Control', ''))

    def test_member_consumption_own_grade_history_only(self):
        for member in [self.member, self.other]:
            ConsumptionEvent.objects.create(member=member, order_id=uuid4(), amount_fen=100,
                balance_fen=100, grade_id_before=self.grade.id, grade_id_after=self.grade.id,
                source_ref='private-order-settlement-source')
        result = self.app_get('consumption')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['data']['pagination']['total'], 1)
        self.assertEqual(result.json()['data']['items'][0]['gradeAfter']['name'], self.grade.name)
        self.assertNotIn('private-order-settlement-source', result.content.decode())
        self.assertIn('no-store', result.get('Cache-Control', ''))

    def test_member_cannot_choose_other_identity_or_duplicate_pagination(self):
        for route in ['overview', 'points', 'consumption']:
            self.assertEqual(self.app_get(route, {'memberId': str(self.other.id)}).status_code, 400)
        self.assertEqual(self.app_get('points', {'page': ['1', '2']}).status_code, 400)

    def test_admin_pagination_default_twenty_max_hundred_and_invalid_bounds(self):
        Member.objects.bulk_create([Member(wechat_app_id='wx-d40-api', wechat_openid=f'synthetic-{i}', grade=self.grade) for i in range(23)])
        default = self.client.get('/api/v1/admin/members')
        self.assertEqual(default.status_code, 200)
        self.assertEqual(len(default.json()['data']['items']), 20)
        self.assertEqual(default.json()['data']['pagination']['total'], 25)
        maximum = self.client.get('/api/v1/admin/members', {'pageSize': 100})
        self.assertEqual(maximum.status_code, 200); self.assertEqual(len(maximum.json()['data']['items']), 25)
        for query in [{'pageSize': 101}, {'pageSize': 0}, {'page': 0}, {'page': 'abc'}, {'enabled': 'maybe'}]:
            self.assertEqual(self.client.get('/api/v1/admin/members', query).status_code, 400)

    def test_app_pagination_max_hundred_and_page_two_are_bounded(self):
        for i in range(21):
            grant_points(self.member, 1, timezone.now()+timedelta(days=1), f'private-batch-{i}')
        first = self.app_get('points'); second = self.app_get('points', {'page': 2})
        self.assertEqual(len(first.json()['data']['items']), 20)
        self.assertEqual(len(second.json()['data']['items']), 1)
        self.assertEqual(self.app_get('points', {'pageSize': 100}).status_code, 200)
        self.assertEqual(self.app_get('points', {'pageSize': 101}).status_code, 400)
        self.assertEqual(self.app_get('consumption', {'page': 0}).status_code, 400)

    def test_member_session_revocation_and_disable_deny_reads(self):
        self.member.enabled = False; self.member.save(update_fields=['enabled'])
        self.assertEqual(self.app_get('overview').status_code, 401)
        self.member.enabled = True; self.member.save(update_fields=['enabled'])
        MemberSession.objects.filter(member=self.member).update(revoked_at=timezone.now())
        self.assertEqual(self.app_get('points').status_code, 401)

    def test_invalid_rules_never_consume_revision_or_create_change(self):
        body = self.body()
        confirmation = self.confirm(body['expectedRevision'])
        invalid = {**body, 'points': {**body['points'], 'maxPercent': True}}
        result = self.write(self.client, 'put', RULES, invalid, uuid4(), confirmation)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(MemberRuleChange.objects.count(), 0)
        self.assertEqual(self.client.get(RULES).json()['data']['revision'], body['expectedRevision'])
        valid = self.write(self.client, 'put', RULES, body, uuid4(), confirmation)
        self.assertEqual(valid.status_code, 200)

    def test_wrong_password_never_authorizes_rule_write(self):
        body = self.body()
        denied = self.write(self.client, 'post', '/api/v1/admin/auth/confirm',
            {'action': 'member.rules.update', 'password': 'incorrect synthetic password',
             'objectId': 'member-rules', 'revision': body['expectedRevision']})
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn('confirmationToken', denied.content.decode())
        self.assertEqual(MemberRuleChange.objects.count(), 0)

    def test_private_admin_and_app_error_responses_disable_caching(self):
        for route in ['/api/v1/admin/members', RULES,
                      '/api/v1/admin/members/' + str(self.member.id)]:
            result = self.client.get(route)
            self.assertEqual(result.status_code, 200)
            self.assertIn('no-store', result.get('Cache-Control', ''))
        for result in [Client().get('/api/v1/app/member/overview'),
                       self.app_get('points', {'page': 0}),
                       self.app_get('consumption', {'memberId': str(self.other.id)})]:
            self.assertIn(result.status_code, [400, 401])
            self.assertIn('no-store', result.get('Cache-Control', ''))
            self.assertIn('Authorization', result.get('Vary', ''))
