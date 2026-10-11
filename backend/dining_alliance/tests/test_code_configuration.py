"""Deployment-owned code environment is explicit and validated before network."""
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from dining_alliance.code_views import generate_code
from dining_alliance.access import DiningError


class CodeConfigurationTests(SimpleTestCase):
    def payload(self, **configuration):
        class Reply:
            def __enter__(self):return io.BytesIO(b'\x89PNG\r\n\x1a\nfixture')
            def __exit__(self,*args):return False
        with override_settings(**configuration),patch('wechat_integration.credentials.effective_credentials',return_value=SimpleNamespace(app_id='wx-test',secret='secret')),patch('wechat_integration.probe.acquire_access_token',return_value=SimpleNamespace(status='SUCCESS',token='fixture-token')),patch('dining_alliance.code_views.build_opener') as opener:
            opener.return_value.open.return_value=Reply()
            generate_code('a'*32)
            return json.loads(opener.return_value.open.call_args.args[0].data)

    def test_release_default_validates_published_page(self):
        payload=self.payload(DINING_CODE_ENV_VERSION='release',DINING_CODE_CHECK_PATH=True)
        self.assertEqual(payload['env_version'],'release')
        self.assertIs(payload['check_path'],True)

    def test_uat_trial_supports_page_before_release(self):
        payload=self.payload(DINING_CODE_ENV_VERSION='trial',DINING_CODE_CHECK_PATH=False)
        self.assertEqual(payload['env_version'],'trial')
        self.assertIs(payload['check_path'],False)
        self.assertEqual(payload['page'],'pages/dining/shop')
        self.assertEqual(payload['scene'],'a'*32)

    def test_developer_codes_are_explicitly_supported(self):
        self.assertEqual(self.payload(DINING_CODE_ENV_VERSION='develop',DINING_CODE_CHECK_PATH=False)['env_version'],'develop')

    def test_invalid_settings_rejected_before_credential_network(self):
        for version,path in [('uat',True),('',True),('release','false'),('trial',0),('release',None),([],False),(None,True)]:
            with override_settings(DINING_CODE_ENV_VERSION=version,DINING_CODE_CHECK_PATH=path),patch('wechat_integration.probe.acquire_access_token') as token:
                with self.assertRaises(DiningError):generate_code('a'*32)
                token.assert_not_called()

    def test_settings_environment_validation_and_defaults(self):
        def load(values):
            env={**os.environ,'DJANGO_DEBUG':'1',**values}
            for key in ('DINING_CODE_ENV_VERSION','DINING_CODE_CHECK_PATH'):
                if key not in values:env.pop(key,None)
            code='import config.settings as s; print(s.DINING_CODE_ENV_VERSION, s.DINING_CODE_CHECK_PATH)'
            return subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).parents[2],env=env,capture_output=True,text=True,timeout=10)
        default=load({})
        self.assertEqual(default.returncode,0,default.stderr)
        self.assertEqual(default.stdout.strip(),'release True')
        trial=load({'DINING_CODE_ENV_VERSION':'trial','DINING_CODE_CHECK_PATH':'0'})
        self.assertEqual(trial.returncode,0,trial.stderr)
        self.assertEqual(trial.stdout.strip(),'trial False')
        for values in [{'DINING_CODE_ENV_VERSION':'uat'},{'DINING_CODE_CHECK_PATH':'false'},{'DINING_CODE_CHECK_PATH':'2'}]:
            result=load(values)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('DINING_CODE_',result.stderr)

    def test_compose_passes_code_settings_to_backend_services(self):
        compose=(Path(__file__).parents[3]/'compose.production.yaml').read_text()
        shared=compose.split('x-backend-env: &backend-env',1)[1].split('x-backend-service:',1)[0]
        self.assertIn('DINING_CODE_ENV_VERSION: ${DINING_CODE_ENV_VERSION:-release}',shared)
        self.assertIn('DINING_CODE_CHECK_PATH: ${DINING_CODE_CHECK_PATH:-1}',shared)
        self.assertIn('environment: *backend-env',compose)
