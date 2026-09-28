#!/bin/sh
set -eu

: "${POSTGRES_PASSWORD_FILE:=/run/secrets/postgres_password}"
: "${DJANGO_SECRET_KEY_FILE:=/run/secrets/django_secret_key}"
test -r "$POSTGRES_PASSWORD_FILE" || { echo 'Database secret is unavailable.' >&2; exit 1; }
test -r "$DJANGO_SECRET_KEY_FILE" || { echo 'Django secret is unavailable.' >&2; exit 1; }

POSTGRES_PASSWORD=$(cat "$POSTGRES_PASSWORD_FILE")
DJANGO_SECRET_KEY=$(cat "$DJANGO_SECRET_KEY_FILE")
export POSTGRES_PASSWORD DJANGO_SECRET_KEY

test -n "$POSTGRES_PASSWORD" && test -n "$DJANGO_SECRET_KEY" || {
    echo 'A required secret is empty.' >&2
    exit 1
}
test "${DJANGO_DEBUG:-}" = 0 || { echo 'Production DEBUG must be disabled.' >&2; exit 1; }
test -n "${DJANGO_ALLOWED_HOSTS:-}" && test -n "${DJANGO_CSRF_TRUSTED_ORIGINS:-}" || {
    echo 'Production host and CSRF origin settings are required.' >&2
    exit 1
}

exec "$@"
