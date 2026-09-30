"""Bounded direct CI upload worker. Remote results never rewrite CodeVersion."""
import hashlib
import http.client
import json
import os
import re
import stat
import tempfile
import uuid
import zipfile
import zlib
from datetime import timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from catalog.storage import LocalStorage
from wechat_integration.credentials import CredentialsUnavailable, effective_credentials, integration_row
from wechat_open_platform.service import PlatformStateError, authorization_status, developer_app_id
from .models import CodeUploadKey, DeveloperUploadKey, ReleaseUploadJob
from .release_service import UploadKeyUnavailable, decrypt_developer_upload_key, decrypt_upload_key
from .service import object_status


ADAPTER_URL = os.environ.get('MINI_CI_ADAPTER_URL', 'http://mini-ci-adapter:8787/upload')
STAGING_ROOT = Path(os.environ.get('MINI_CI_ALLOWED_ROOT', '/var/lib/mini-ci-stage'))
TOKEN_FILE = os.environ.get('MINI_CI_DISPATCH_TOKEN_FILE', '')
LEASE = timedelta(minutes=5)
MAX_FILES = 10_000
MAX_RESPONSE = 4096
LOCAL_FAILURES = {'INPUT_INVALID', 'PROJECT_INVALID', 'PROJECT_APPID_MISMATCH',
                  'TARGET_CONFIG_INVALID', 'WORKER_MISCONFIGURED'}
EXPLICIT_FAILURE_STAGES = {
    'ENVIRONMENT_FAILED': 'ENVIRONMENT', 'SIGNATURE_FAILED': 'VALIDATION',
    'COMPILE_FAILED': 'COMPILE', 'WECHAT_REJECTED': 'UPLOAD',
    **{code: 'ENVIRONMENT' if code == 'WORKER_MISCONFIGURED' else 'VALIDATION'
       for code in LOCAL_FAILURES},
}
STAGES = frozenset({'ENVIRONMENT', 'VALIDATION', 'COMPILE', 'UPLOAD', 'RESPONSE'})
SAFE_SDK_CODE = re.compile(r'[A-Za-z0-9_+.-]{1,48}\Z')


class UploadFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def recover_expired(now=None):
    now = now or timezone.now()
    with transaction.atomic():
        rows = ReleaseUploadJob.objects.select_for_update().filter(status='RUNNING', lease_until__lte=now)
        for job in rows:
            if job.call_started_at:
                job.status, job.failure_code, job.completed_at = 'UNKNOWN', 'RESULT_UNKNOWN', now
                job.failure_stage = 'RESPONSE'
            else:
                job.status, job.failure_code = 'PENDING', ''
                job.failure_stage = ''
            job.sdk_code, job.platform_error_code = '', None
            job.lease_token = job.lease_until = None
            job.save(update_fields=['status', 'failure_code', 'failure_stage', 'sdk_code',
                                    'platform_error_code', 'completed_at', 'lease_token', 'lease_until'])


def claim_next(now=None):
    now = now or timezone.now()
    with transaction.atomic():
        job = (ReleaseUploadJob.objects.select_for_update(skip_locked=True)
               .filter(status='PENDING').order_by('created_at', 'id').first())
        if job:
            job.status, job.lease_until, job.lease_token = 'RUNNING', now + LEASE, uuid.uuid4()
            job.save(update_fields=['status', 'lease_until', 'lease_token'])
        return job


def _finish(job, status, code='', *, failure_stage='', sdk_code='', platform_error_code=None):
    with transaction.atomic():
        live = ReleaseUploadJob.objects.select_for_update().get(pk=job.pk)
        if live.status != 'RUNNING' or live.lease_token != job.lease_token:
            return
        live.status, live.failure_code = status, code
        live.failure_stage, live.sdk_code = failure_stage, sdk_code
        live.platform_error_code = platform_error_code
        live.lease_token, live.lease_until, live.completed_at = None, None, timezone.now()
        live.save(update_fields=['status', 'failure_code', 'failure_stage', 'sdk_code',
                                 'platform_error_code', 'lease_token', 'lease_until', 'completed_at'])


def _adapter_outcome(result):
    """Classify only bounded adapter facts, never arbitrary SDK text or codes."""
    if not isinstance(result, dict):
        return 'UNKNOWN', 'RESULT_UNKNOWN', 'RESPONSE', '', None
    sdk_code = result.get('sdkCode')
    sdk_code = sdk_code if isinstance(sdk_code, str) and SAFE_SDK_CODE.fullmatch(sdk_code) else ''
    stage = result.get('failureStage')
    stage = stage if isinstance(stage, str) and stage in STAGES else 'RESPONSE'
    code = result.get('code')
    code = code if isinstance(code, str) else ''
    if result.get('ok') is True and code == 'UPLOADED':
        return 'SUCCEEDED', '', '', '', None
    if result.get('ok') is not False:
        return 'UNKNOWN', 'RESULT_UNKNOWN', 'RESPONSE', sdk_code, None
    if code == 'WECHAT_REJECTED':
        platform_code = result.get('platformErrorCode')
        if type(platform_code) is int and 0 < abs(platform_code) <= 99999999 and stage == 'UPLOAD':
            return 'FAILED', code, stage, sdk_code, platform_code
    elif code in EXPLICIT_FAILURE_STAGES and code != 'WECHAT_REJECTED':
        return 'FAILED', code, EXPLICIT_FAILURE_STAGES[code], sdk_code, None
    return 'UNKNOWN', 'RESULT_UNKNOWN', stage, sdk_code, None


def _mark_call_started(job, staged_digest):
    with transaction.atomic():
        live = ReleaseUploadJob.objects.select_for_update().get(pk=job.pk)
        if live.status != 'RUNNING' or live.lease_token != job.lease_token or live.call_started_at:
            raise UploadFailure('JOB_STATE_CHANGED')
        live.staged_digest = staged_digest
        live.call_started_at = timezone.now()
        live.save(update_fields=['staged_digest', 'call_started_at'])
        job.call_started_at = live.call_started_at


def _key_for(job):
    key_model = DeveloperUploadKey if job.channel == 'DIRECT_COMMIT' else CodeUploadKey
    row = key_model.objects.filter(pk=1).first()
    if not row or row.revision != job.key_revision or row.app_id != job.developer_app_id:
        raise UploadFailure('UPLOAD_KEY_CHANGED')
    try:
        return (decrypt_developer_upload_key if job.channel == 'DIRECT_COMMIT' else decrypt_upload_key)(row)
    except UploadKeyUnavailable as exc:
        raise UploadFailure('UPLOAD_KEY_UNAVAILABLE') from exc


def _check_identity(job):
    try:
        if effective_credentials(integration_row()).app_id != job.app_id:
            raise UploadFailure('APP_ID_CHANGED')
        if job.channel == 'DIRECT_COMMIT':
            if developer_app_id() != job.developer_app_id:
                raise UploadFailure('DEVELOPER_APP_ID_CHANGED')
            if authorization_status(job.app_id).get('status') != 'PASS':
                raise UploadFailure('AUTHORIZATION_NOT_READY')
    except (CredentialsUnavailable, PlatformStateError) as exc:
        raise UploadFailure('CONFIGURATION_UNAVAILABLE') from exc


def _safe_member(info):
    name = info.filename
    if (not name or '\\' in name or '\x00' in name or name.startswith('/') or
            name != PurePosixPath(name).as_posix() or
            any(part in {'', '.', '..'} for part in name.split('/')) or info.is_dir()):
        raise UploadFailure('PACKAGE_INVALID')
    mode = (info.external_attr >> 16) & 0o170000
    if mode not in {0, stat.S_IFREG}:
        raise UploadFailure('PACKAGE_INVALID')
    if len(name.encode('utf-8')) > 200 or info.file_size < 0:
        raise UploadFailure('PACKAGE_INVALID')
    return name


def _write_file(root, name, content):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(content)


def _extract(job, root):
    version = job.version
    if (version.package_sha256 != job.package_sha256 or
            object_status(version, verify_digest=True) != 'READY'):
        raise UploadFailure('PACKAGE_UNAVAILABLE')
    path = LocalStorage().path(version.object_key)
    max_bytes = min(settings.STORAGE_CODE_MAX_BYTES, 200 * 1024 * 1024)
    names = set()
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= MAX_FILES or len(entries) != version.file_count:
                raise UploadFailure('PACKAGE_INVALID')
            total = 0
            for info in entries:
                name = _safe_member(info)
                total += info.file_size
                if name in names or total > max_bytes:
                    raise UploadFailure('PACKAGE_INVALID')
                names.add(name)
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                     getattr(os, 'O_NOFOLLOW', 0), 0o600)
                actual = 0
                with os.fdopen(descriptor, 'wb') as output, archive.open(info) as stream:
                    while chunk := stream.read(1024 * 1024):
                        actual += len(chunk)
                        if actual > info.file_size:
                            raise UploadFailure('PACKAGE_INVALID')
                        output.write(chunk)
                if actual != info.file_size:
                    raise UploadFailure('PACKAGE_INVALID')
    except (OSError, ValueError, RuntimeError, EOFError, NotImplementedError,
            zlib.error, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise UploadFailure('PACKAGE_INVALID') from exc
    if not {'project.config.json', 'app.js', 'app.json'} <= names:
        raise UploadFailure('PACKAGE_INVALID')
    if job.channel == 'DIRECT_COMMIT':
        if 'ext.json' in names:
            raise UploadFailure('PACKAGE_INVALID')
        try:
            config_path = root / 'project.config.json'
            if config_path.stat().st_size > 65536:
                raise ValueError()
            config = json.loads(config_path.read_text())
            if not isinstance(config, dict) or config.get('appid') != job.app_id:
                raise ValueError()
            config = {**config, 'appid': job.developer_app_id}
            config_path.write_bytes(json.dumps(config, sort_keys=True,
                separators=(',', ':'), ensure_ascii=False).encode())
            _write_file(root, 'ext.json', json.dumps({'extEnable': True, 'extAppid': job.app_id,
                'directCommit': True}, sort_keys=True, separators=(',', ':')).encode())
            names.add('ext.json')
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise UploadFailure('PACKAGE_INVALID') from exc
    elif 'ext.json' in names:
        raise UploadFailure('PACKAGE_INVALID')
    digest = hashlib.sha256()
    for name in sorted(names):
        target = root / name
        length = target.stat().st_size
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(4, 'big'))
        digest.update(encoded)
        digest.update(length.to_bytes(8, 'big'))
        with target.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def _adapter_settings():
    url = urlsplit(ADAPTER_URL)
    if (url.scheme != 'http' or url.hostname != 'mini-ci-adapter' or url.port != 8787 or
            url.path != '/upload' or url.query or url.fragment or url.username or url.password):
        raise UploadFailure('WORKER_MISCONFIGURED')
    if not TOKEN_FILE:
        raise UploadFailure('WORKER_MISCONFIGURED')
    try:
        with open(TOKEN_FILE, encoding='ascii') as stream:
            token = stream.read(256).strip()
    except (OSError, UnicodeError) as exc:
        raise UploadFailure('WORKER_MISCONFIGURED') from exc
    if not 32 <= len(token) <= 128 or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise UploadFailure('WORKER_MISCONFIGURED')
    return token


def _invoke_adapter(job, project_path, private_key):
    token = _adapter_settings()
    payload = {'mode': job.channel, 'appId': job.developer_app_id,
               'privateKey': private_key, 'projectPath': str(project_path),
               'version': job.upload_version, 'description': job.description}
    if job.channel == 'DIRECT_COMMIT':
        payload['targetAppId'] = job.app_id
    encoded = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode()
    if len(encoded) > 32768:
        raise UploadFailure('INPUT_INVALID')
    client = http.client.HTTPConnection('mini-ci-adapter', 8787, timeout=180)
    try:
        client.request('POST', '/upload', body=encoded,
                       headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'})
        result = client.getresponse()
        raw = result.read(MAX_RESPONSE + 1)
        status = result.status
    finally:
        client.close()
    if len(raw) > MAX_RESPONSE or status != 200:
        raise UploadFailure('RESULT_UNKNOWN')
    try:
        data = json.loads(raw.decode('utf-8'))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise UploadFailure('RESULT_UNKNOWN') from exc
    if not isinstance(data, dict) or type(data.get('ok')) is not bool:
        raise UploadFailure('RESULT_UNKNOWN')
    return data


def run_one(job):
    try:
        STAGING_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
        _check_identity(job)
        private_key = _key_for(job)
        with tempfile.TemporaryDirectory(prefix=f'{job.id.hex}-', dir=STAGING_ROOT) as directory:
            project_path = Path(directory)
            staged_digest = _extract(job, project_path)
            _check_identity(job)
            _key_for(job)
            _adapter_settings()
            _mark_call_started(job, staged_digest)
            try:
                result = _invoke_adapter(job, project_path, private_key)
            except (TimeoutError, OSError, http.client.HTTPException):
                _finish(job, 'UNKNOWN', 'RESULT_UNKNOWN', failure_stage='RESPONSE')
                return
            status, code, stage, sdk_code, platform_code = _adapter_outcome(result)
            _finish(job, status, code, failure_stage=stage, sdk_code=sdk_code,
                    platform_error_code=platform_code)
    except (UploadFailure, OSError) as exc:
        code = exc.code if isinstance(exc, UploadFailure) else 'STORAGE_UNAVAILABLE'
        state = 'UNKNOWN' if job.call_started_at or code == 'RESULT_UNKNOWN' else 'FAILED'
        stage = 'RESPONSE' if state == 'UNKNOWN' else ('VALIDATION' if code in {
            'PACKAGE_INVALID', 'PACKAGE_UNAVAILABLE', 'UPLOAD_KEY_CHANGED', 'UPLOAD_KEY_UNAVAILABLE',
            'APP_ID_CHANGED', 'DEVELOPER_APP_ID_CHANGED', 'AUTHORIZATION_NOT_READY'} else 'ENVIRONMENT')
        _finish(job, state, code if state == 'FAILED' else 'RESULT_UNKNOWN', failure_stage=stage)
