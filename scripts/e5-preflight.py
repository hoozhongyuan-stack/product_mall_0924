"""Read-only deployment checks. Never import Django, connect to a DB or call WeChat."""
import base64
import binascii
import json
import os
from pathlib import Path
import re
import stat
import sys

SECRET_NAMES = ('postgres_password', 'django_secret_key', 'wechat_credential_key')
SECRET_ENV = ('POSTGRES_PASSWORD_FILE', 'DJANGO_SECRET_KEY_FILE', 'MALL_WECHAT_CREDENTIAL_KEY_FILE')


def validate_key(value):
    value = value.rstrip(b'\r\n')
    try:
        if not re.fullmatch(rb'[A-Za-z0-9_-]{43}=', value):
            raise ValueError
        if len(base64.b64decode(value, altchars=b'-_', validate=True)) != 32:
            raise ValueError
    except (ValueError, binascii.Error):
        raise ValueError('WeChat encryption key must be a URL-safe base64 encoded 32-byte key.') from None


def read_secret(path, *, key=False):
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 10001 or
            metadata.st_gid != 10001 or stat.S_IMODE(metadata.st_mode) != 0o400):
        raise ValueError('Secret must be a regular UID/GID 10001 file with mode 0400.')
    with path.open('rb') as stream:
        value = stream.read(4097)
    if not value.strip() or len(value) > 4096:
        raise ValueError('Required secret is empty or exceeds the supported size.')
    if key:
        validate_key(value)


def host_secret(path, *, key=False):
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('Secret source must be an absolute canonical path without symlinks.')
    parent = path.parent.stat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or
            stat.S_IMODE(parent.st_mode) != 0o700):
        raise ValueError('Secret directory must be owned by root with mode 0700.')
    read_secret(path, key=key)


def host(config):
    if os.geteuid() != 0:
        raise ValueError('E5 production operations require the root deployment account.')
    secrets = config.get('secrets', {})
    for name in SECRET_NAMES:
        source = secrets.get(name, {}).get('file')
        if not isinstance(source, str) or not source:
            raise ValueError('All three production secret files must be configured.')
        host_secret(Path(source), key=name == 'wechat_credential_key')


def runtime():
    if os.geteuid() != 10001 or os.getegid() != 10001:
        raise ValueError('Application preflight must run as UID/GID 10001.')
    for name in SECRET_ENV:
        source = os.environ.get(name)
        if not source:
            raise ValueError('A required runtime secret path is missing.')
        read_secret(Path(source), key=name == 'MALL_WECHAT_CREDENTIAL_KEY_FILE')
    if (os.environ.get('DJANGO_DEBUG') != '0' or not os.environ.get('DJANGO_ALLOWED_HOSTS')
            or not os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS')):
        raise ValueError('Production DEBUG, host and CSRF settings are required.')


def main():
    try:
        if sys.argv[1:] == ['host']:
            host(json.load(sys.stdin))
        elif sys.argv[1:] == ['runtime']:
            runtime()
        else:
            raise ValueError('Usage: e5-preflight.py host|runtime')
    except (OSError, ValueError, TypeError, AttributeError):
        # File contents, parser input and platform errors may contain secrets.
        print('E5 preflight failed: check secret ownership/mode/content and production configuration.', file=sys.stderr)
        return 1
    print('E5 secret preflight passed; no database or platform request was made.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
