#!/usr/bin/env python3
"""Run browser tests or a visual preview in a newly owned localhost database."""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
ADMIN = ROOT / 'pc-admin'
LOCAL_MAIN = ROOT.with_name('product_mall_0924')
LOCAL_PYTHON = LOCAL_MAIN / '.venv/bin/python'
if importlib.util.find_spec('psycopg') is None:
    if LOCAL_PYTHON.is_file() and Path(sys.executable).resolve() != LOCAL_PYTHON.resolve():
        os.execv(str(LOCAL_PYTHON), [str(LOCAL_PYTHON), __file__, *sys.argv[1:]])
    raise SystemExit('Install the existing backend requirements in this Python environment first.')
import psycopg
from psycopg import sql


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def wait_ready(url, process):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('An isolated test server exited before readiness.')
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.HTTPError):
            time.sleep(0.2)
    raise RuntimeError('Isolated test server readiness timed out.')


def stop_owned(process):
    # Signal the owned group even when npm exited first and left its Vite child alive.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def interrupted(signum, frame):
    raise KeyboardInterrupt


def keep_preview_alive(processes):
    """Keep owned services available until Ctrl-C/SIGTERM, or fail if one exits."""
    while True:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError('An isolated preview server exited unexpectedly.')
        time.sleep(1)


def run(*, preview=False):
    preview_seed = ADMIN / 'scripts/seed-visual-preview.py'
    if preview and not preview_seed.is_file():
        raise SystemExit('Preview seed is missing: pc-admin/scripts/seed-visual-preview.py')
    host = os.environ.get('POSTGRES_HOST', '127.0.0.1')
    if host not in ('127.0.0.1', 'localhost'):
        raise SystemExit('Integration tests only accept a localhost PostgreSQL host.')
    port = int(os.environ.get('POSTGRES_PORT', '15432'))
    user = os.environ.get('POSTGRES_USER', 'product_mall_dev')
    password = os.environ.get('POSTGRES_PASSWORD')
    if not password:
        secret_file = LOCAL_MAIN / 'secrets/postgres_password'
        if not secret_file.is_file():
            raise SystemExit('Provide POSTGRES_PASSWORD for the local isolated test database.')
        password = secret_file.read_text().strip()
    database = 'test_health_e2e_' + secrets.token_hex(8)
    assert re.fullmatch(r'test_health_e2e_[0-9a-f]{16}', database)
    owner = 'e2e-owner-' + secrets.token_hex(5)
    owner_password = secrets.token_urlsafe(32)
    api_port, web_port = free_port(), free_port()
    while web_port == api_port:
        web_port = free_port()
    processes = []
    created = False
    # Connect to postgres solely to CREATE/DROP the unique DB owned by this invocation.
    with psycopg.connect(host=host, port=port, user=user, password=password,
                         dbname='postgres', autocommit=True) as control:
        try:
            control.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
            created = True
            with tempfile.TemporaryDirectory(prefix='mall-health-e2e-') as temporary:
                # Each isolated DB owns its own synthetic encryption key. Never inherit deployment keys.
                credential_key = Path(temporary) / 'wechat-credential-key'
                descriptor = os.open(credential_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
                with os.fdopen(descriptor, 'wb') as key_file:
                    key_file.write(base64.urlsafe_b64encode(secrets.token_bytes(32)))
                env = {key: value for key, value in os.environ.items()
                       if not key.startswith(('POSTGRES_', 'DJANGO_', 'WECHAT_', 'KDNIAO_', 'EXCHANGE_', 'MALL_'))}
                env.update(POSTGRES_HOST=host, POSTGRES_PORT=str(port), POSTGRES_USER=user,
                    POSTGRES_PASSWORD=password, POSTGRES_DB=database, DJANGO_DEBUG='1',
                    DJANGO_SECRET_KEY=secrets.token_urlsafe(48), DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost',
                    DJANGO_CSRF_TRUSTED_ORIGINS=f'http://127.0.0.1:{web_port}',
                    MALL_MEDIA_ROOT=str(Path(temporary) / 'media'), EXCHANGE_ORDER_ENABLED='0',
                    MALL_WECHAT_CREDENTIAL_KEY_FILE=str(credential_key),
                    # Synthetic integration clients support v4; never change deployment defaults.
                    PAGE_RUNTIME_SCHEMA_VERSION='1' if preview else '4',
                    WECHAT_REFUND_ENABLED='0', KDNIAO_ENABLED='0',
                    MALL_E2E_OWNER=owner, MALL_E2E_OWNER_PASSWORD=owner_password,
                    MALL_E2E_REAL='1', MALL_E2E_EXTERNAL_SERVER='1',
                    MALL_E2E_BASE_URL=f'http://127.0.0.1:{web_port}',
                    MALL_API_PROXY_TARGET=f'http://127.0.0.1:{api_port}')
                subprocess.run([sys.executable, 'manage.py', 'migrate', '--noinput', '--verbosity', '0'],
                               cwd=ROOT / 'backend', env=env, check=True)
                # Existing data migrations seed permissions/rules; only an isolated owner is needed.
                seed = """import os
from accounts.models import AdminAccount
from django.conf import settings
assert settings.DATABASES['default']['NAME'].startswith('test_health_e2e_')
assert not any(settings.ORDER_PAYMENT_METHODS_ENABLED.values())
assert not settings.EXCHANGE_ORDER_ENABLED and not settings.WECHAT_REFUND_ENABLED
assert not settings.WECHAT_MINI_APP_ID and not settings.WECHAT_MINI_APP_SECRET
AdminAccount.objects.create_user(os.environ['MALL_E2E_OWNER'], os.environ['MALL_E2E_OWNER_PASSWORD'], display_name='隔离集成测试', kind='OWNER')
"""
                subprocess.run([sys.executable, 'manage.py', 'shell', '--no-imports'], input=seed,
                               text=True, cwd=ROOT / 'backend', env=env, check=True)
                if preview:
                    env['MALL_E2E_PREVIEW'] = '1'
                    subprocess.run([sys.executable, 'manage.py', 'shell', '--no-imports'],
                                   input=preview_seed.read_text(), text=True,
                                   cwd=ROOT / 'backend', env=env, check=True)
                with (Path(temporary) / 'servers.log').open('w+') as log:
                    for command, cwd in (
                        ([sys.executable, 'manage.py', 'runserver', f'127.0.0.1:{api_port}', '--noreload'], ROOT / 'backend'),
                        (['npm', 'run', 'dev', '--', '--port', str(web_port), '--strictPort'], ADMIN),
                    ):
                        processes.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                                                          stderr=subprocess.STDOUT, start_new_session=True))
                    wait_ready(f'http://127.0.0.1:{api_port}/api/v1/app/categories', processes[0])
                    wait_ready(env['MALL_E2E_BASE_URL'], processes[1])
                    if preview:
                        access_file = Path(temporary) / 'preview-access.json'
                        details = {
                            'adminUrl': env['MALL_E2E_BASE_URL'],
                            'apiUrl': env['MALL_API_PROXY_TARGET'],
                            'database': {'host': host, 'port': port, 'user': user, 'name': database},
                            'account': {'loginName': owner, 'password': owner_password},
                            'mediaRoot': env['MALL_MEDIA_ROOT'],
                        }
                        descriptor = os.open(access_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(descriptor, 'w') as credentials:
                            json.dump(details, credentials, ensure_ascii=False, indent=2)
                        print(f"Preview admin: {details['adminUrl']}", flush=True)
                        print(f"Preview API: {details['apiUrl']}", flush=True)
                        print(f'Private preview access file: {access_file}', flush=True)
                        print('Preview stays open until Ctrl-C/SIGTERM; only this run’s resources are removed.', flush=True)
                        keep_preview_alive(processes)
                        return 0
                    print('Isolated Django/PostgreSQL and Vue ready; external platform credentials absent.', flush=True)
                    result = subprocess.run([str(ADMIN / 'node_modules/.bin/playwright'), 'test'], cwd=ADMIN, env=env)
                    if result.returncode:
                        return result.returncode
                verify = """from benefits.models import CouponCampaign
from orders.models import Order
from customers.models import Member, MemberSession, WechatCodeUse
from wechat_integration.credentials import effective_credentials
from wechat_integration.models import MiniProgramIntegration
assert CouponCampaign.objects.filter(code__startswith='E2E_').count() == 2
assert not CouponCampaign.objects.exclude(status='DRAFT').exists()
assert not Order.objects.exists()
configuration = MiniProgramIntegration.objects.get(pk=1)
assert configuration.managed and configuration.revision == 4
assert 'synthetic-only-' not in configuration.encrypted_payload
assert effective_credentials(configuration).secret.startswith('synthetic-only-')
assert configuration.last_check_at is None
assert not Member.objects.exists() and not MemberSession.objects.exists() and not WechatCodeUse.objects.exists()
print('Verified persisted drafts, encrypted synthetic credentials, zero orders and zero WeChat identities.')
"""
                subprocess.run([sys.executable, 'manage.py', 'shell', '--no-imports'], input=verify,
                               text=True, cwd=ROOT / 'backend', env=env, check=True)
                return 0
        finally:
            cleanup_failed = False
            for process in reversed(processes):
                try:
                    stop_owned(process)
                except (OSError, subprocess.TimeoutExpired):
                    cleanup_failed = True
            if created:
                control.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
                print('Removed only this run’s isolated database and stopped owned servers.', flush=True)
            if cleanup_failed:
                raise RuntimeError('An owned server could not be stopped; the isolated database was still removed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', action='store_true',
                        help='Seed an isolated visual preview and keep it open until Ctrl-C/SIGTERM.')
    arguments = parser.parse_args()
    signal.signal(signal.SIGTERM, interrupted)
    try:
        raise SystemExit(run(preview=arguments.preview))
    except KeyboardInterrupt:
        raise SystemExit(130)
