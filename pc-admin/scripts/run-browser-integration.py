#!/usr/bin/env python3
"""Run browser integration in a newly owned localhost database; never use an existing business DB."""
import importlib.util
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


def run():
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
                env = {key: value for key, value in os.environ.items()
                       if not key.startswith(('POSTGRES_', 'DJANGO_', 'WECHAT_', 'KDNIAO_', 'EXCHANGE_', 'MALL_'))}
                env.update(POSTGRES_HOST=host, POSTGRES_PORT=str(port), POSTGRES_USER=user,
                    POSTGRES_PASSWORD=password, POSTGRES_DB=database, DJANGO_DEBUG='1',
                    DJANGO_SECRET_KEY=secrets.token_urlsafe(48), DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost',
                    DJANGO_CSRF_TRUSTED_ORIGINS=f'http://127.0.0.1:{web_port}',
                    MALL_MEDIA_ROOT=str(Path(temporary) / 'media'), EXCHANGE_ORDER_ENABLED='0',
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
                with (Path(temporary) / 'servers.log').open('w+') as log:
                    for command, cwd in (
                        ([sys.executable, 'manage.py', 'runserver', f'127.0.0.1:{api_port}', '--noreload'], ROOT / 'backend'),
                        (['npm', 'run', 'dev', '--', '--port', str(web_port), '--strictPort'], ADMIN),
                    ):
                        processes.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                                                          stderr=subprocess.STDOUT, start_new_session=True))
                    wait_ready(f'http://127.0.0.1:{api_port}/api/v1/app/categories', processes[0])
                    wait_ready(env['MALL_E2E_BASE_URL'], processes[1])
                    print('Isolated Django/PostgreSQL and Vue ready; external platform credentials absent.', flush=True)
                    result = subprocess.run([str(ADMIN / 'node_modules/.bin/playwright'), 'test'], cwd=ADMIN, env=env)
                    if result.returncode:
                        return result.returncode
                verify = """from benefits.models import CouponCampaign
from orders.models import Order
assert CouponCampaign.objects.filter(code__startswith='E2E_').count() == 2
assert not CouponCampaign.objects.exclude(status='DRAFT').exists()
assert not Order.objects.exists()
print('Verified two persisted drafts and zero orders in isolated database.')
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
    signal.signal(signal.SIGTERM, interrupted)
    try:
        raise SystemExit(run())
    except KeyboardInterrupt:
        raise SystemExit(130)
