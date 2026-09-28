#!/bin/sh
# Host-scheduled quiesced backup. Never mount the Docker socket in app containers.
set -eu

: "${E5_BACKUP_HOST_DIR:?Set the private backup directory}"
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
test -d "$root/.git" || { echo 'Use a standalone release checkout.' >&2; exit 1; }
checkout_revision=$(git -C "$root" rev-parse --verify 'HEAD^{commit}')
if [ -n "${MALL_RELEASE_REVISION:-}" ] && [ "$MALL_RELEASE_REVISION" != "$checkout_revision" ]; then
    echo 'Scheduled backup checkout is not the expected revision.' >&2; exit 1
fi
MALL_RELEASE_REVISION=$checkout_revision
export MALL_RELEASE_REVISION
test -z "$(git -C "$root" status --porcelain --untracked-files=all)" || {
    echo 'Scheduled backup checkout is not clean.' >&2; exit 1;
}
case "$E5_BACKUP_HOST_DIR" in
    /*) ;;
    *) echo 'Backup directory must be absolute.' >&2; exit 2 ;;
esac
test -d "$E5_BACKUP_HOST_DIR" || { echo 'Backup directory is unavailable.' >&2; exit 1; }
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

. "$root/scripts/e5-compose.sh"
compose config --quiet
running_web=$(compose ps -q web)
test -n "$running_web" || { echo 'Web service is not running.' >&2; exit 1; }
running_revision=$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$running_web")
[ "$running_revision" = "$MALL_RELEASE_REVISION" ] || {
    echo 'Running revision differs from the backup checkout.' >&2; exit 1;
}

operation_lock="$E5_BACKUP_HOST_DIR/.e5-operation-lock"
mkdir -m 700 "$operation_lock" 2>/dev/null || {
    echo 'Another E5 backup/deployment may be running; inspect the operation lock.' >&2
    exit 1
}
paused=0
resume_writers() {
    if [ "$paused" = 1 ]; then
        compose up --wait -d web export-worker scheduler admin
        paused=0
    fi
}
cleanup() {
    status=$?
    trap - EXIT
    if [ "$paused" = 1 ]; then
        resume_writers || status=1
    fi
    rmdir "$operation_lock" || status=1
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

. "$root/scripts/e5-preflight.sh"
e5_preflight compose
paused=1
compose stop admin web export-worker scheduler
compose --profile ops run --rm --no-deps -e E5_WRITERS_PAUSED=1 backup
resume_writers
echo "Quiesced backup completed for $MALL_RELEASE_REVISION."
