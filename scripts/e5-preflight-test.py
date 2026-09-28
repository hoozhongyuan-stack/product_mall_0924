"""No database or running Docker service is used by these orchestration tests."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
REVISION = 'a' * 40


class PreflightOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.checkout = self.root / 'checkout'
        shutil.copytree(SCRIPTS, self.checkout / 'scripts')
        (self.checkout / '.git').mkdir()
        self.backups = self.root / 'backups'
        self.backups.mkdir(mode=0o700)
        bundle = self.backups / 'e5-2026-09-28-synthetic'
        bundle.mkdir()
        (bundle / 'manifest.txt').write_text('release_revision=' + REVISION + '\n')
        self.tools = self.root / 'tools'
        self.tools.mkdir()
        self.log = self.root / 'calls'
        self.tool('git', '#!/bin/sh\ncase "$*" in *rev-parse*) printf "%s\\n" "$MALL_RELEASE_REVISION";; esac\n')
        self.tool('python3', f'''#!{sys.executable}
import os,sys
if any(x.endswith('e5-preflight.py') for x in sys.argv):
    with open(os.environ['E5_TEST_LOG'],'a') as f:f.write('host-preflight\\n')
    sys.stdin.read()
    sys.exit(17 if os.environ.get('E5_TEST_FAIL')=='host' else 0)
os.execv({sys.executable!r},[{sys.executable!r},*sys.argv[1:]])
''')
        self.tool('docker', f'''#!{sys.executable}
import json,os,sys
a=sys.argv[1:]
with open(os.environ['E5_TEST_LOG'],'a') as f:f.write(' '.join(a)+'\\n')
if a[:2]==['volume','inspect']:sys.exit(1)
if a and a[0]=='inspect':print(os.environ['MALL_RELEASE_REVISION'])
elif 'config' in a and 'json' in a:print(json.dumps({{'name':'synthetic-source','services':{{'admin':{{'ports':[{{'published':'18080'}}]}}}}}}))
elif 'ps' in a:print('synthetic-web')
elif any(x.endswith('e5-preflight.py') for x in a):sys.exit(19 if os.environ.get('E5_TEST_FAIL')=='runtime' else 0)
''')
        self.env = {**os.environ, 'PATH': str(self.tools) + ':' + os.environ['PATH'],
                    'MALL_RELEASE_REVISION': REVISION, 'E5_BACKUP_HOST_DIR': str(self.backups),
                    'E5_TEST_LOG': str(self.log), 'E5_RECOVERY_PROJECT': 'isolated-recovery',
                    'E5_RECOVERY_DB': 'isolated_recovery', 'E5_RECOVERY_HTTP_PORT': '18081'}

    def tool(self, name, content):
        path = self.tools / name
        path.write_text(content)
        path.chmod(0o700)

    def run_script(self, script, args=(), fail=''):
        self.log.write_text('')
        result = subprocess.run(['sh', str(self.checkout / 'scripts' / script), *args],
                                env={**self.env, 'E5_TEST_FAIL': fail}, text=True, capture_output=True)
        return result, self.log.read_text().splitlines()

    def test_preflight_failure_never_stops_writers_or_runs_release_or_backup(self):
        for fail in ['host', 'runtime']:
            for script, args in [('e5-deploy.sh', ['upgrade']), ('e5-deploy.sh', ['initial']),
                                 ('e5-backup-cycle.sh', []),
                                 ('e5-recover.sh', ['e5-2026-09-28-synthetic'])]:
                with self.subTest(fail=fail, script=script, args=args):
                    result, calls = self.run_script(script, args, fail)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn('host-preflight', calls)
                    self.assertFalse(any(' stop ' in c or ' up ' in c or
                                         (' run ' in c and c.endswith(' backup')) or ' restore /backups/' in c for c in calls), calls)

    def test_success_preflights_before_stop_and_retains_backup_before_release(self):
        result, calls = self.run_script('e5-deploy.sh', ['upgrade'])
        self.assertEqual(result.returncode, 0, result.stderr)
        host = calls.index('host-preflight')
        runtime = next(i for i, c in enumerate(calls) if 'e5-preflight.py runtime' in c)
        stop = next(i for i, c in enumerate(calls) if ' stop ' in c)
        backup = next(i for i, c in enumerate(calls) if ' run ' in c and c.endswith(' backup'))
        release = next(i for i, c in enumerate(calls) if '--exit-code-from release release' in c)
        self.assertLess(host, runtime)
        self.assertLess(runtime, stop)
        self.assertLess(stop, backup)
        self.assertLess(backup, release)


class SecretValidationTests(unittest.TestCase):
    def setUp(self):
        script = SCRIPTS / 'e5-preflight.py'
        self.assertTrue(script.is_file(), 'preflight implementation missing')
        spec = importlib.util.spec_from_file_location('e5_preflight', script)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_rejects_invalid_key_without_echoing_content(self):
        module = self.module
        valid = base64.urlsafe_b64encode(bytes(range(32)))
        module.validate_key(valid + b'\n')
        for value in [b'', b'sensitive-invalid-key', valid + b'x', base64.urlsafe_b64encode(b'x' * 31)]:
            with self.subTest(size=len(value)), self.assertRaises(ValueError) as error:
                module.validate_key(value)
            self.assertNotIn('sensitive-invalid-key', str(error.exception))

    def test_runtime_requires_service_identity_and_all_paths_and_production_settings(self):
        env = {name: '/synthetic/file' for name in self.module.SECRET_ENV}
        env.update(DJANGO_DEBUG='0', DJANGO_ALLOWED_HOSTS='mall.example.com',
                   DJANGO_CSRF_TRUSTED_ORIGINS='https://mall.example.com')
        with patch.object(self.module.os, 'geteuid', return_value=0):
            with self.assertRaises(ValueError): self.module.runtime()
        with patch.object(self.module.os, 'geteuid', return_value=10001), \
                patch.object(self.module.os, 'getegid', return_value=10001), \
                patch.object(self.module, 'read_secret') as read:
            with patch.dict(os.environ, env, clear=True):
                self.module.runtime()
                self.assertEqual(read.call_count, 3)
                self.assertTrue(read.call_args.kwargs['key'])
            for name in [*self.module.SECRET_ENV, 'DJANGO_ALLOWED_HOSTS', 'DJANGO_CSRF_TRUSTED_ORIGINS']:
                with self.subTest(name=name), patch.dict(os.environ, {**env, name: ''}, clear=True):
                    with self.assertRaises(ValueError): self.module.runtime()
            with patch.dict(os.environ, {**env, 'DJANGO_DEBUG': '1'}, clear=True):
                with self.assertRaises(ValueError): self.module.runtime()

    def test_host_requires_root_and_configured_secret_sources(self):
        with patch.object(self.module.os, 'geteuid', return_value=10001):
            with self.assertRaises(ValueError): self.module.host({})
        with patch.object(self.module.os, 'geteuid', return_value=0):
            for config in [{}, {'secrets': {'postgres_password': {'file': 123}}}]:
                with self.assertRaises(ValueError): self.module.host(config)

    def test_cli_rejects_unknown_mode_and_parser_errors_without_input_echo(self):
        for args, content in [(['other'], ''), (['host'], 'sensitive-input-not-json')]:
            result = subprocess.run([sys.executable, str(SCRIPTS / 'e5-preflight.py'), *args],
                                    input=content, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('sensitive-input-not-json', result.stderr)


class ComposePreflightTests(unittest.TestCase):
    def test_preflight_has_same_identity_and_secrets_but_no_network_or_business_volume(self):
        env = {**os.environ, 'MALL_RELEASE_REVISION': REVISION, 'MALL_ALLOWED_HOSTS': 'mall.example.com',
               'MALL_CSRF_TRUSTED_ORIGINS': 'https://mall.example.com', 'POSTGRES_DB': 'synthetic',
               'POSTGRES_USER': 'synthetic', 'E5_BACKUP_HOST_DIR': '/tmp/synthetic-backups',
               'E5_WECHAT_CREDENTIAL_KEY_FILE': '/tmp/synthetic-key'}
        data = json.loads(subprocess.check_output(['docker', 'compose', '--profile', 'ops', '-f',
                          str(SCRIPTS.parent / 'compose.production.yaml'), 'config', '--format', 'json'],
                          env=env, text=True))
        check = data['services']['preflight']
        release = data['services']['release']
        self.assertEqual(check['user'], release['user'])
        self.assertEqual(check['secrets'], release['secrets'])
        self.assertEqual(check['environment'], release['environment'])
        self.assertEqual(check['network_mode'], 'none')
        self.assertFalse(check.get('networks'))
        self.assertFalse(check.get('volumes'))
        self.assertFalse(check.get('depends_on'))
        self.assertTrue(check['read_only'])
        self.assertEqual(set(data['services']['web']['networks']), {'private', 'egress'})
        self.assertTrue(data['networks']['private']['internal'])


if __name__ == '__main__':
    unittest.main()
