"""Run as root in an isolated Linux container; uses only temporary synthetic files."""
import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('e5-preflight.py')


class LinuxSecretTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(sys.platform.startswith('linux') and os.geteuid() == 0,
                        'Use the documented isolated Linux container command')
        self.assertTrue(SCRIPT.is_file(), 'preflight implementation missing')
        spec = importlib.util.spec_from_file_location('e5_preflight', SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.secret = self.root / 'credential'
        self.secret.write_bytes(base64.urlsafe_b64encode(bytes(range(32))) + b'\n')
        os.chown(self.secret, 10001, 10001)
        self.secret.chmod(0o400)

    def test_root_host_0400_application_file_passes(self):
        self.module.host_secret(self.secret, key=True)

    def test_root_readability_does_not_hide_wrong_owner_or_wide_permissions(self):
        for owner, mode in [(0, 0o400), (1000, 0o600), (10001, 0o444), (10001, 0o600)]:
            os.chown(self.secret, owner, owner)
            self.secret.chmod(mode)
            self.assertTrue(os.access(self.secret, os.R_OK))
            with self.subTest(owner=owner, mode=mode), self.assertRaises(ValueError):
                self.module.host_secret(self.secret, key=True)

    def test_rejects_directory_symlink_empty_wrong_key_and_unprotected_parent(self):
        link = self.root / 'linked'
        link.symlink_to(self.secret)
        for path in [link, self.root]:
            with self.assertRaises(ValueError): self.module.host_secret(path, key=True)
        self.secret.write_bytes(b'')
        with self.assertRaises(ValueError): self.module.host_secret(self.secret, key=True)
        self.secret.write_bytes(b'invalid-sensitive-key')
        with self.assertRaises(ValueError) as error: self.module.host_secret(self.secret, key=True)
        self.assertNotIn('invalid-sensitive-key', str(error.exception))
        self.secret.write_bytes(base64.urlsafe_b64encode(bytes(range(32))))
        self.root.chmod(0o755)
        with self.assertRaises(ValueError): self.module.host_secret(self.secret, key=True)

    def test_runtime_uid_can_read_bound_file_but_other_uid_cannot(self):
        # A bind-mounted file has the container's parent permissions, not its host parent's.
        self.root.chmod(0o755)
        env = {**os.environ, 'POSTGRES_PASSWORD_FILE': str(self.secret),
               'DJANGO_SECRET_KEY_FILE': str(self.secret),
               'MALL_WECHAT_CREDENTIAL_KEY_FILE': str(self.secret), 'DJANGO_DEBUG': '0',
               'DJANGO_ALLOWED_HOSTS': 'mall.example.com',
               'DJANGO_CSRF_TRUSTED_ORIGINS': 'https://mall.example.com'}
        def as_uid(uid):
            def change():
                os.setgroups([])
                os.setgid(uid)
                os.setuid(uid)
            return change
        good = subprocess.run([sys.executable, str(SCRIPT), 'runtime'], env=env,
                              preexec_fn=as_uid(10001), text=True, capture_output=True)
        self.assertEqual(good.returncode, 0, good.stderr)
        denied = subprocess.run([sys.executable, '-c', 'import os; assert not os.access(os.environ["POSTGRES_PASSWORD_FILE"],os.R_OK)'],
                                env=env, preexec_fn=as_uid(10002), capture_output=True)
        self.assertEqual(denied.returncode, 0)
        os.chown(self.secret, 1000, 1000)
        bad = subprocess.run([sys.executable, str(SCRIPT), 'runtime'], env=env,
                             preexec_fn=as_uid(10001), text=True, capture_output=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertNotIn('AAECAwQF', bad.stdout + bad.stderr)

    def test_host_cli_validates_all_three_sources_without_exposing_values(self):
        config = {'secrets': {name: {'file': str(self.secret)} for name in self.module.SECRET_NAMES}}
        self.module.host(config)
        for args, content, expected in [(['host'], json.dumps(config), 0),
                                        (['host'], 'sensitive-invalid-json', 1),
                                        (['invalid'], '', 1), (['runtime'], '', 1)]:
            output = io.StringIO()
            with patch.object(sys, 'argv', [str(SCRIPT), *args]), \
                    patch.object(sys, 'stdin', io.StringIO(content)), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(self.module.main(), expected)
            self.assertNotIn('sensitive-invalid-json', output.getvalue())
        good = subprocess.run([sys.executable, str(SCRIPT), 'host'], input=json.dumps(config),
                              text=True, capture_output=True)
        self.assertEqual(good.returncode, 0, good.stderr)
        for content in ['sensitive-not-json', '{}', '[]', json.dumps({'secrets': {'postgres_password': {}}})]:
            bad = subprocess.run([sys.executable, str(SCRIPT), 'host'], input=content,
                                 text=True, capture_output=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertNotIn('sensitive-not-json', bad.stdout + bad.stderr)
        config['secrets']['wechat_credential_key']['file'] = str(self.root / 'missing')
        bad = subprocess.run([sys.executable, str(SCRIPT), 'host'], input=json.dumps(config),
                             text=True, capture_output=True)
        self.assertNotEqual(bad.returncode, 0)

    def test_oversized_empty_and_relative_sources_are_rejected(self):
        with self.assertRaises(ValueError): self.module.host_secret(Path('relative'))
        for value in [b' \n', b'x' * 4097]:
            self.secret.write_bytes(value)
            with self.assertRaises(ValueError): self.module.host_secret(self.secret)


if __name__ == '__main__': unittest.main()
