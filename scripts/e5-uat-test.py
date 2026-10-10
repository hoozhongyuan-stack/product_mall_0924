"""UAT composition and orchestration regressions; no application/database startup."""
import importlib.util
import json
import os
import re
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('preflight_tests', ROOT / 'scripts/e5-preflight-test.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class UatCompositionTests(unittest.TestCase):
    def test_page_runtime_schema_defaults_to_one_and_can_be_explicitly_raised(self):
        with patch.dict(os.environ, {'PATH': os.environ.get('PATH', '')}, clear=True):
            defaults = self.config()['services']
            for row in defaults.values():
                if 'DJANGO_SECRET_KEY_FILE' in row.get('environment', {}):
                    self.assertEqual(row['environment'].get('PAGE_RUNTIME_SCHEMA_VERSION'), '1')
        with patch.dict(os.environ, {'PAGE_RUNTIME_SCHEMA_VERSION': '4'}):
            explicit = self.config()['services']
            for row in explicit.values():
                if 'DJANGO_SECRET_KEY_FILE' in row.get('environment', {}):
                    self.assertEqual(row['environment'].get('PAGE_RUNTIME_SCHEMA_VERSION'), '4')
        example = (ROOT / 'deploy/env.production.example').read_text()
        self.assertIn('PAGE_RUNTIME_SCHEMA_VERSION=1', example)

    def config(self, recovery=False, code_release=False):
        env = {**os.environ, 'MALL_RELEASE_REVISION': 'a' * 40, 'MALL_ALLOWED_HOSTS': 'mall.example.com',
               'MALL_CSRF_TRUSTED_ORIGINS': 'https://mall.example.com', 'POSTGRES_DB': 'synthetic',
               'POSTGRES_USER': 'synthetic', 'E5_BACKUP_HOST_DIR': '/tmp/synthetic-backups',
               'E5_WECHAT_CREDENTIAL_KEY_FILE': '/tmp/synthetic-key', 'E5_DEPLOY_PROFILE': 'uat',
               'MALL_EDGE_NETWORK': 'synthetic-mall-edge', 'MALL_EDGE_ALIAS': 'synthetic-mall',
               'WECHAT_CODE_UPLOAD_EGRESS_IP': '8.152.204.21',
               'MALL_MINIPROGRAM_API_BASE_URL': 'https://uat.example.com'}
        profiles = ['--profile', 'ops'] + (['--profile', 'code-release'] if code_release else [])
        return json.loads(subprocess.check_output(['docker', 'compose', *profiles, '-f',
                          str(ROOT / 'compose.production.yaml'), '-f', str(ROOT / 'compose.uat.yaml'), '-f',
                          str(ROOT / ('compose.recovery.yaml' if recovery else 'compose.uat-edge.yaml')),
                          'config', '--format', 'json'], env=env, text=True))

    def test_resource_limits_and_single_worker_preserve_transaction_gates(self):
        rows = self.config()['services']
        web = rows['web']
        self.assertEqual(web['command'][web['command'].index('--workers') + 1], '1')
        self.assertEqual(web['command'][web['command'].index('--threads') + 1], '2')
        resident = ['db', 'web', 'export-worker', 'scheduler', 'admin']
        self.assertLessEqual(sum(int(rows[name]['mem_limit']) for name in resident), 1792 * 1024 * 1024)
        for name in resident:
            self.assertGreater(float(rows[name]['cpus']), 0)
            self.assertEqual(rows[name]['logging']['options']['max-size'], '10m')
        self.assertEqual(web['environment']['EXCHANGE_ORDER_ENABLED'], '0')
        self.assertEqual(web['environment']['WECHAT_REFUND_ENABLED'], '0')
        self.assertEqual(web['environment']['WECHAT_CODE_UPLOAD_EGRESS_IP'], '8.152.204.21')
        self.assertEqual(web['environment']['MALL_MINIPROGRAM_API_BASE_URL'], 'https://uat.example.com')
        cache = next(row for row in rows['admin']['tmpfs'] if row.startswith('/var/cache/nginx:'))
        self.assertGreaterEqual(int(re.search(r'size=(\d+)', cache).group(1)), 60 * 1024 * 1024)
        self.assertGreaterEqual(int(rows['admin']['mem_limit']), 96 * 1024 * 1024)

    def test_only_admin_joins_edge_and_backend_remains_private(self):
        cfg = self.config()
        self.assertTrue(cfg['networks']['edge']['external'])
        self.assertEqual(cfg['networks']['edge']['name'], 'synthetic-mall-edge')
        self.assertTrue(cfg['networks']['private']['internal'])
        for name, row in cfg['services'].items():
            self.assertEqual('edge' in row.get('networks', {}), name == 'admin')
        self.assertFalse(cfg['services']['db'].get('ports'))
        self.assertFalse(cfg['services']['web'].get('ports'))
        self.assertNotIn('code-upload-worker', cfg['services'])
        self.assertNotIn('mini-ci-adapter', cfg['services'])
        self.assertEqual(cfg['services']['web']['environment']['DJANGO_TRUST_PROXY_HTTPS'], '1')
        self.assertTrue(any(v['target'] == '/etc/nginx/mall-proxy-scheme.conf'
                            and v['read_only'] for v in cfg['services']['admin']['volumes']))

    def test_optional_ci_adapter_cannot_read_backend_secrets_or_reach_private_network(self):
        rows = self.config(code_release=True)['services']
        node = rows['mini-ci-adapter']
        python = rows['code-upload-worker']
        self.assertEqual(set(node['networks']), {'ci-link', 'egress'})
        self.assertEqual(set(python['networks']), {'ci-link', 'private', 'egress'})
        self.assertFalse(node.get('ports'))
        self.assertEqual([item['source'] for item in node['secrets']], ['mini_ci_dispatch_token'])
        self.assertTrue(node['read_only'])
        self.assertTrue(node['security_opt'])
        self.assertTrue(any(item['target'] == '/var/lib/mini-ci-stage' and item['read_only']
                            for item in node['volumes']))

    def test_recovery_has_resource_limits_but_no_edge_or_web_egress(self):
        cfg = self.config(recovery=True)
        self.assertNotIn('edge', cfg['networks'])
        self.assertEqual(set(cfg['services']['web']['networks']), {'private'})
        self.assertNotIn('DJANGO_TRUST_PROXY_HTTPS', cfg['services']['web']['environment'])
        self.assertFalse(cfg['services']['admin'].get('volumes'))
        self.assertGreater(int(cfg['services']['web']['mem_limit']), 0)

    def test_django_ignores_forwarded_scheme_by_default_and_accepts_only_opted_in_https(self):
        from django.test import RequestFactory, override_settings
        from django.conf import settings
        if not settings.configured:
            settings.configure(SECRET_KEY='synthetic', DEFAULT_CHARSET='utf-8')
        for enabled, header, expected in [('0', 'https', False), ('1', 'https', True), ('1', 'http', False)]:
            env = {'DJANGO_DEBUG': '0', 'DJANGO_SECRET_KEY': 'synthetic', 'MALL_MEDIA_ROOT': '/tmp/synthetic',
                   'DJANGO_TRUST_PROXY_HTTPS': enabled}
            with patch.dict(os.environ, env):
                row = runpy.run_path(str(ROOT / 'backend/config/settings.py'))
            with override_settings(SECURE_PROXY_SSL_HEADER=row.get('SECURE_PROXY_SSL_HEADER')):
                request = RequestFactory().get('/', HTTP_X_FORWARDED_PROTO=header)
                self.assertEqual(request.is_secure(), expected)

    def test_https_proxy_setting_preserves_csrf_origin_checks(self):
        from django.test import RequestFactory, override_settings
        from django.conf import settings
        from django.http import HttpResponse
        from django.middleware.csrf import CsrfViewMiddleware, _get_new_csrf_string
        if not settings.configured:
            settings.configure(SECRET_KEY='synthetic', DEFAULT_CHARSET='utf-8')
        token = _get_new_csrf_string()
        with override_settings(USE_I18N=False, SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
                               ALLOWED_HOSTS=['mall.example.com'], CSRF_TRUSTED_ORIGINS=['https://mall.example.com']):
            for origin, allowed in [('https://mall.example.com', True), ('https://untrusted.invalid', False)]:
                request = RequestFactory().post('/api/synthetic', HTTP_HOST='mall.example.com',
                    HTTP_X_FORWARDED_PROTO='https', HTTP_ORIGIN=origin, HTTP_X_CSRFTOKEN=token)
                request.COOKIES['csrftoken'] = token
                middleware = CsrfViewMiddleware(lambda _: HttpResponse())
                middleware.process_request(request)
                response = middleware.process_view(request, lambda _: HttpResponse(), (), {})
                self.assertEqual(response is None, allowed)


class UatOrchestrationTests(unittest.TestCase):
    setUp = fixtures.PreflightOrderTests.setUp
    tool = fixtures.PreflightOrderTests.tool
    run_script = fixtures.PreflightOrderTests.run_script

    def test_profile_file_is_authoritative_without_executing_its_other_content(self):
        env_file = self.root / 'release.env'
        marker = self.root / 'must-not-exist'
        env_file.write_text(f'E5_DEPLOY_PROFILE=uat\nUNRELATED=$(touch {marker})\n')
        self.env['E5_ENV_FILE'] = str(env_file)
        self.env.pop('E5_DEPLOY_PROFILE', None)
        result, calls = self.run_script('e5-backup-cycle.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertTrue(all('compose.uat.yaml' in c for c in calls if c.startswith('compose ')))
        self.env['E5_DEPLOY_PROFILE'] = 'production'
        rejected, calls = self.run_script('e5-backup-cycle.sh')
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse(calls)

    def test_profile_file_rejects_duplicate_invalid_or_malformed_entries_and_defaults_missing(self):
        env_file = self.root / 'release.env'
        self.env['E5_ENV_FILE'] = str(env_file)
        self.env.pop('E5_DEPLOY_PROFILE', None)
        for content in ['E5_DEPLOY_PROFILE=uat\nE5_DEPLOY_PROFILE=uat\n',
                        'E5_DEPLOY_PROFILE=unknown\n', 'E5_DEPLOY_PROFILE\n']:
            env_file.write_text(content)
            result, calls = self.run_script('e5-deploy.sh', ['initial'])
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(calls)
        env_file.write_text('UNRELATED=synthetic\n')
        result, calls = self.run_script('e5-backup-cycle.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any('compose.uat.yaml' in c for c in calls))

    def test_all_compose_calls_share_uat_overlay_including_recovery_and_backup(self):
        self.env['E5_DEPLOY_PROFILE'] = 'uat'
        for script, args in [('e5-deploy.sh', ['upgrade']), ('e5-backup-cycle.sh', []),
                             ('e5-recover.sh', ['e5-2026-09-28-synthetic'])]:
            result, calls = self.run_script(script, args)
            self.assertEqual(result.returncode, 0, result.stderr)
            for call in calls:
                if call.startswith('compose '): self.assertIn('compose.uat.yaml', call)

    def test_file_enabled_code_release_is_paused_for_backup_and_preflight_checks_token(self):
        token = self.root / 'mini-ci-token'
        token.write_text('A' * 48)
        token.chmod(0o600)
        env_file = self.root / 'release.env'
        env_file.write_text('E5_DEPLOY_PROFILE=uat\nE5_CODE_RELEASE_ENABLED=1\n'
                            f'E5_MINI_CI_DISPATCH_TOKEN_FILE={token}\n')
        self.env['E5_ENV_FILE'] = str(env_file)
        self.env.pop('E5_CODE_RELEASE_ENABLED', None)
        self.env.pop('E5_MINI_CI_DISPATCH_TOKEN_FILE', None)
        result, calls = self.run_script('e5-backup-cycle.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any('--profile code-release' in call for call in calls))
        self.assertTrue(any(' stop admin web export-worker mini-ci-adapter code-upload-worker scheduler' in call
                            for call in calls))
        self.assertTrue(any('mini-ci-adapter' in call and '--entrypoint sh' in call for call in calls))
        refused, calls = self.run_script('e5-deploy.sh', ['upgrade'], fail='token')
        self.assertNotEqual(refused.returncode, 0)
        self.assertFalse(any(' stop ' in call or ' up ' in call for call in calls))

    def test_prebuilt_mode_checks_only_enabled_release_images_and_never_builds(self):
        self.env['E5_IMAGE_MODE'] = 'prebuilt'
        for script, args in [('e5-deploy.sh', ['upgrade']), ('e5-recover.sh', ['e5-2026-09-28-synthetic'])]:
            result, calls = self.run_script(script, args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(any(' build ' in c for c in calls))
            images = [c for c in calls if c.startswith('image inspect ')]
            self.assertEqual(len(images), 3)
            for image in ['backend', 'admin', 'ops']:
                self.assertTrue(any('product-mall-' + image + ':' + fixtures.REVISION in c for c in images))

    def test_code_release_opt_in_requires_private_token_and_checks_isolated_image(self):
        token = self.root / 'mini-ci-token'
        token.write_text('A' * 48)
        token.chmod(0o600)
        self.env.update({'E5_CODE_RELEASE_ENABLED': '1', 'E5_MINI_CI_DISPATCH_TOKEN_FILE': str(token),
                         'E5_IMAGE_MODE': 'prebuilt'})
        result, calls = self.run_script('e5-deploy.sh', ['upgrade'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any('product-mall-mini-ci:' + fixtures.REVISION in c for c in calls))
        self.assertTrue(any('--profile code-release' in c for c in calls if c.startswith('compose ')))
        self.assertTrue(any('mini-ci-adapter code-upload-worker' in c for c in calls))
        token.chmod(0o644)
        refused, calls = self.run_script('e5-deploy.sh', ['upgrade'])
        self.assertNotEqual(refused.returncode, 0)
        self.assertFalse(any(' stop ' in c or ' up ' in c for c in calls))

    def test_recovery_builds_ops_via_the_service_that_defines_its_build(self):
        result, calls = self.run_script('e5-recover.sh', ['e5-2026-09-28-synthetic'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any('--profile ops build backup' in c for c in calls))
        self.assertFalse(any('--profile ops build restore' in c for c in calls))

    def test_bad_profile_or_prebuilt_revision_fails_before_pausing_services(self):
        for changes in [{'E5_DEPLOY_PROFILE': 'untrusted'}, {'E5_IMAGE_MODE': 'invalid'},
                        {'E5_IMAGE_MODE': 'prebuilt', 'E5_TEST_IMAGE_REVISION': 'b' * 40},
                        {'E5_IMAGE_MODE': 'prebuilt', 'E5_TEST_IMAGE_PLATFORM': 'linux/arm64'}]:
            self.env.update(changes)
            result, calls = self.run_script('e5-deploy.sh', ['upgrade'])
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(any(' stop ' in c or ' up ' in c for c in calls))
            for name in changes: self.env.pop(name)

    def test_recovery_never_uses_live_edge_or_starts_workers_by_default(self):
        self.env['E5_DEPLOY_PROFILE'] = 'uat'
        result, calls = self.run_script('e5-recover.sh', ['e5-2026-09-28-synthetic'])
        self.assertEqual(result.returncode, 0, result.stderr)
        target_calls = [c for c in calls if '-p isolated-recovery' in c]
        self.assertTrue(target_calls)
        for call in target_calls:
            self.assertNotIn('compose.uat-edge.yaml', call)
            self.assertIn('compose.recovery.yaml', call)
        started = next(c for c in target_calls if 'up --wait -d web' in c)
        self.assertNotIn('export-worker', started)
        self.assertNotIn('code-upload-worker', started)
        self.assertNotIn('scheduler', started)

    def test_initial_and_recovery_refuse_existing_project_or_unknown_inventory_before_any_build(self):
        for flag in ['E5_TEST_EXISTING_PROJECT', 'E5_TEST_PROJECT_QUERY_FAIL']:
            self.env[flag] = '1'
            for script, args in [('e5-deploy.sh', ['initial']),
                                 ('e5-recover.sh', ['e5-2026-09-28-synthetic'])]:
                with self.subTest(flag=flag, script=script):
                    result, calls = self.run_script(script, args)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertTrue(any('label=com.docker.compose.project=' in c for c in calls))
                    self.assertFalse(any(' build ' in c or ' run ' in c or ' up ' in c or ' stop ' in c for c in calls))
            self.env.pop(flag)


if __name__ == '__main__': unittest.main()
