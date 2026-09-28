#!/usr/bin/env python3
"""Standard-library launcher safety tests. Mock processes/DB; create only private temporary files."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).with_name('run-browser-integration.py').resolve()
LOCAL_PYTHON = SCRIPT.parents[2].with_name('product_mall_0924') / '.venv/bin/python'
if importlib.util.find_spec('psycopg') is None:
    if LOCAL_PYTHON.is_file() and Path(sys.executable).resolve() != LOCAL_PYTHON.resolve():
        os.execv(str(LOCAL_PYTHON), [str(LOCAL_PYTHON), __file__, *sys.argv[1:]])
    raise SystemExit('Use the existing backend Python environment (psycopg is required).')
spec = importlib.util.spec_from_file_location('browser_launcher', SCRIPT)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherSafetyTests(unittest.TestCase):
    def setUp(self):
        self.control = Mock()
        self.control.__enter__ = Mock(return_value=self.control)
        self.control.__exit__ = Mock(return_value=False)
        self.output = io.StringIO()

    @contextlib.contextmanager
    def isolated_mocks(self, *, preview_wait=None, stop_effect=None):
        original_read, original_is_file = Path.read_text, Path.is_file
        fixed_seed = SCRIPT.with_name('seed-visual-preview.py')
        def read(path, *args, **kwargs):
            return '# fixed visual preview seed' if path == fixed_seed else original_read(path, *args, **kwargs)
        def is_file(path):
            return True if path == fixed_seed else original_is_file(path)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {
                'POSTGRES_HOST': '127.0.0.1', 'POSTGRES_PASSWORD': 'synthetic-unit-password',
                'MALL_E2E_PREVIEW': 'untrusted-inherited-value', 'WECHAT_MINI_APP_SECRET': 'inherited-secret',
            }))
            stack.enter_context(patch.object(launcher.psycopg, 'connect', return_value=self.control))
            run = stack.enter_context(patch.object(launcher.subprocess, 'run', return_value=Mock(returncode=0)))
            stack.enter_context(patch.object(launcher.subprocess, 'Popen', side_effect=[Mock(pid=101), Mock(pid=102)]))
            stack.enter_context(patch.object(launcher, 'wait_ready'))
            stop = stack.enter_context(patch.object(launcher, 'stop_owned', side_effect=stop_effect))
            stack.enter_context(patch.object(launcher, 'keep_preview_alive', side_effect=preview_wait))
            stack.enter_context(patch.object(Path, 'read_text', read))
            stack.enter_context(patch.object(Path, 'is_file', is_file))
            stack.enter_context(contextlib.redirect_stdout(self.output))
            yield run, stop

    def assert_exact_database_cleanup(self):
        statements = [call.args[0].as_string() for call in self.control.execute.call_args_list]
        self.assertEqual(len(statements), 2)
        self.assertTrue(statements[0].startswith('CREATE DATABASE "test_health_e2e_'))
        self.assertTrue(statements[1].startswith('DROP DATABASE '))
        self.assertEqual(statements[0].split('"')[1], statements[1].split('"')[1])

    def test_preview_is_private_and_interruption_cleans_owned_resources(self):
        access_files = []
        def preview_wait(processes):
            marker = 'Private preview access file: '
            access_file = Path(next(line[len(marker):] for line in self.output.getvalue().splitlines() if line.startswith(marker)))
            access_files.append(access_file)
            self.assertEqual(stat.S_IMODE(access_file.stat().st_mode), 0o600)
            details = json.loads(access_file.read_text())
            self.assertTrue(details['database']['name'].startswith('test_health_e2e_'))
            self.assertNotIn('password', details['database'])
            self.assertTrue(details['account']['password'])
            self.assertNotIn(details['account']['password'], self.output.getvalue())
            self.assertEqual(len(processes), 2)
            raise KeyboardInterrupt
        with self.isolated_mocks(preview_wait=preview_wait) as (run, stop):
            with self.assertRaises(KeyboardInterrupt):
                launcher.run(preview=True)
            self.assertEqual(run.call_count, 3)
            self.assertEqual(run.call_args_list[-1].kwargs['input'], '# fixed visual preview seed')
            self.assertEqual(run.call_args_list[-1].kwargs['env']['MALL_E2E_PREVIEW'], '1')
            self.assertNotIn('WECHAT_MINI_APP_SECRET', run.call_args_list[-1].kwargs['env'])
            self.assertFalse(any('playwright' in str(call.args[0]) for call in run.call_args_list))
            self.assertEqual(stop.call_count, 2)
        self.assertFalse(access_files[0].exists())
        self.assert_exact_database_cleanup()

    def test_preview_normal_return_never_falls_into_business_tests(self):
        with self.isolated_mocks() as (run, stop):
            self.assertEqual(launcher.run(preview=True), 0)
            self.assertEqual(run.call_count, 3)
            self.assertEqual(stop.call_count, 2)
        self.assert_exact_database_cleanup()

    def test_default_mode_keeps_test_and_cleanup_flow_without_inherited_preview(self):
        with self.isolated_mocks() as (run, stop):
            self.assertEqual(launcher.run(), 0)
            self.assertEqual(run.call_count, 4)
            self.assertIn('playwright', str(run.call_args_list[2].args[0]))
            self.assertNotIn('MALL_E2E_PREVIEW', run.call_args_list[2].kwargs['env'])
            self.assertEqual(stop.call_count, 2)
        self.assertNotIn('Private preview access file:', self.output.getvalue())
        self.assert_exact_database_cleanup()

    def test_cleanup_timeout_does_not_skip_other_server_or_drop(self):
        with self.isolated_mocks(stop_effect=[subprocess.TimeoutExpired('owned-server', 8), None]) as (_, stop):
            with self.assertRaisesRegex(RuntimeError, 'could not be stopped'):
                launcher.run()
            self.assertEqual(stop.call_count, 2)
        self.assert_exact_database_cleanup()

    def test_missing_fixed_seed_fails_before_database_access(self):
        with patch.object(Path, 'is_file', return_value=False), patch.object(launcher.psycopg, 'connect') as connect:
            with self.assertRaisesRegex(SystemExit, 'Preview seed is missing'):
                launcher.run(preview=True)
            connect.assert_not_called()

    def test_service_exit_and_sigterm_trigger_failure_or_interruption(self):
        with self.assertRaisesRegex(RuntimeError, 'exited unexpectedly'):
            launcher.keep_preview_alive([Mock(poll=Mock(return_value=1))])
        with self.assertRaises(KeyboardInterrupt):
            launcher.interrupted(None, None)

    def test_already_exited_owned_process_is_reaped(self):
        process = Mock(pid=101)
        with patch.object(launcher.os, 'killpg', side_effect=ProcessLookupError):
            launcher.stop_owned(process)
        process.wait.assert_called_once_with(timeout=8)

    def test_cli_help_and_unknown_seed_parameter(self):
        help_result = subprocess.run([sys.executable, str(SCRIPT), '--help'], capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn('--preview', help_result.stdout)
        unknown = subprocess.run([sys.executable, str(SCRIPT), '--seed', '/tmp/untrusted.py'], capture_output=True, text=True)
        self.assertEqual(unknown.returncode, 2)
        self.assertIn('unrecognized arguments', unknown.stderr)


if __name__ == '__main__':
    unittest.main()
