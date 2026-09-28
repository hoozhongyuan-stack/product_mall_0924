#!/bin/sh
set -eu

mode=${1:-}
case "$mode" in
    initial|upgrade) ;;
    *) echo 'Usage: e5-deploy.sh initial|upgrade' >&2; exit 2 ;;
esac

: "${MALL_RELEASE_REVISION:?Export the full target Git revision}"
: "${E5_BACKUP_HOST_DIR:?Set a private backup directory outside the checkout}"
case "$MALL_RELEASE_REVISION" in
    *[!0-9a-f]*|'') echo 'Release revision must be a full lowercase Git SHA.' >&2; exit 2 ;;
esac
if [ "${#MALL_RELEASE_REVISION}" -ne 40 ]; then
    echo 'Release revision must be a full Git SHA.' >&2
    exit 2
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
test -d "$root/.git" || { echo 'Deploy from a standalone clean Git checkout.' >&2; exit 1; }
test "$(git -C "$root" rev-parse --verify 'HEAD^{commit}')" = "$MALL_RELEASE_REVISION" || {
    echo 'Target revision does not match HEAD.' >&2
    exit 1
}
test -z "$(git -C "$root" status --porcelain --untracked-files=all)" || {
    echo 'Target checkout is not clean.' >&2
    exit 1
}
case "$E5_BACKUP_HOST_DIR" in
    /*) ;;
    *) echo 'Backup directory must be an absolute path.' >&2; exit 2 ;;
esac
case "$E5_BACKUP_HOST_DIR/" in
    "$root/"*) echo 'Backup directory must be outside the checkout.' >&2; exit 2 ;;
esac
test -d "$E5_BACKUP_HOST_DIR" || { echo 'Backup directory does not exist.' >&2; exit 1; }
backup_real=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$E5_BACKUP_HOST_DIR")
[ "$backup_real" = "$E5_BACKUP_HOST_DIR" ] || {
    echo 'Backup directory must be a canonical path without symlinks.' >&2; exit 1;
}
python3 -c 'import os,stat,sys; s=os.stat(sys.argv[1]); sys.exit(not stat.S_ISDIR(s.st_mode) or bool(s.st_mode & 0o077))' "$backup_real" || {
    echo 'Backup directory must be private (mode 0700 or stricter).' >&2; exit 1;
}
case "$backup_real/" in
    "$root/"*) echo 'Backup directory resolves inside the checkout.' >&2; exit 1 ;;
esac
operation_lock="$E5_BACKUP_HOST_DIR/.e5-operation-lock"
mkdir -m 700 "$operation_lock" 2>/dev/null || {
    echo 'Another E5 backup/deployment may be running; inspect the operation lock.' >&2
    exit 1
}
trap 'rmdir "$operation_lock"' EXIT

compose() {
    if [ -n "${E5_ENV_FILE:-}" ]; then
        docker compose --env-file "$E5_ENV_FILE" -f "$root/compose.production.yaml" "$@"
    else
        docker compose -f "$root/compose.production.yaml" "$@"
    fi
}

compose config --quiet
if [ "$mode" = initial ]; then
    project=$(compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
    for volume in "${project}_pgdata" "${project}_media"; do
        if docker volume inspect "$volume" >/dev/null 2>&1; then
            echo "Initial deployment requires a new volume: $volume" >&2
            exit 1
        fi
    done
fi
compose build release admin
compose --profile ops build backup
. "$root/scripts/e5-preflight.sh"
e5_preflight compose

if [ "$mode" = initial ]; then
    compose up --wait -d db
    compose up --no-deps --force-recreate --exit-code-from media-init media-init
else
    old_web=$(compose ps -q web)
    test -n "$old_web" || { echo 'No running web service to upgrade.' >&2; exit 1; }
    old_revision=$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$old_web")
    test -n "$old_revision" && test "$old_revision" != '<no value>' || {
        echo 'Running application has no release identity.' >&2
        exit 1
    }
    compose stop admin web export-worker scheduler
    if ! compose --profile ops run --rm --no-deps -e E5_WRITERS_PAUSED=1 \
            -e MALL_RELEASE_REVISION="$old_revision" backup; then
        echo 'Backup failed. Writers remain stopped; do not continue the upgrade.' >&2
        exit 1
    fi
fi

if ! compose up --no-deps --force-recreate --exit-code-from release release; then
    echo 'Release gate failed. Writers remain stopped. Migration may already be committed.' >&2
    echo 'Inspect the paired backup and use isolated restore before any rollback.' >&2
    exit 1
fi

compose up --wait -d web export-worker scheduler admin
echo "Release $MALL_RELEASE_REVISION is healthy in the Compose application stack."
