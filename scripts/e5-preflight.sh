#!/bin/sh
# Source from an E5 host entry point after defining its compose function and root.
e5_preflight() {
    e5_resolved_config=$("$1" config --format json) || return 1
    printf '%s\n' "$e5_resolved_config" | python3 "$root/scripts/e5-preflight.py" host || return 1
    unset e5_resolved_config
    # --no-deps prevents starting/recreating any existing application service.
    "$1" run --rm --no-deps --entrypoint python preflight /app/scripts/e5-preflight.py runtime || return 1
    if [ "${E5_CODE_RELEASE_ENABLED:-0}" = 1 ]; then
        "$1" run --rm --no-deps --entrypoint sh mini-ci-adapter -c \
            'test -r /run/secrets/mini_ci_dispatch_token' || {
            echo 'CI dispatch token is not readable by the adapter service user.' >&2
            return 1
        }
    fi
}
