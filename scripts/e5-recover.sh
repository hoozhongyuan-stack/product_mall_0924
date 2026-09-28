#!/bin/sh
# Restore a paired bundle into a separate Compose project and fresh volumes.
set -eu

: "${MALL_RELEASE_REVISION:?Set the full target Git revision}"
: "${E5_BACKUP_HOST_DIR:?Set the private backup directory}"
: "${E5_RECOVERY_PROJECT:?Set a new, isolated Compose project name}"
: "${E5_RECOVERY_DB:?Set a new recovery database name}"
: "${E5_RECOVERY_HTTP_PORT:?Set a new localhost port for the recovery admin}"
[ "$#" -eq 1 ] || { echo 'Usage: e5-recover.sh BUNDLE_NAME' >&2; exit 2; }
bundle=$1
case "$bundle" in
    e5-*) ;;
    *) echo 'Expected an E5 paired backup bundle name.' >&2; exit 2 ;;
esac
printf '%s' "$bundle" | LC_ALL=C grep -Eq '^e5-[0-9]{4}-[0-9]{2}-[0-9]{2}-[A-Za-z0-9]+$' || {
    echo 'Invalid backup bundle name.' >&2; exit 2;
}
printf '%s' "$E5_RECOVERY_PROJECT" | LC_ALL=C grep -Eq '^[a-z][a-z0-9_-]{2,39}$' || {
    echo 'Invalid recovery project name.' >&2; exit 2;
}
printf '%s' "$E5_RECOVERY_DB" | LC_ALL=C grep -Eq '^[A-Za-z_][A-Za-z0-9_]*$' || {
    echo 'Invalid recovery database name.' >&2; exit 2;
}
case "$E5_RECOVERY_HTTP_PORT" in
    ''|*[!0-9]*) echo 'Invalid recovery HTTP port.' >&2; exit 2 ;;
esac
if [ "$E5_RECOVERY_HTTP_PORT" -lt 1024 ] || [ "$E5_RECOVERY_HTTP_PORT" -gt 65535 ]; then
    echo 'Recovery HTTP port must be 1024-65535.' >&2; exit 2
fi
[ -f "$E5_BACKUP_HOST_DIR/$bundle/manifest.txt" ] || {
    echo 'Backup manifest is unavailable.' >&2; exit 1;
}
bundle_revision=$(awk -F= '$1 == "release_revision" { n++; value=$2 } END { if (n != 1) exit 1; print value }' \
    "$E5_BACKUP_HOST_DIR/$bundle/manifest.txt") || {
    echo 'Backup has no unambiguous source revision.' >&2; exit 1;
}
printf '%s' "$bundle_revision" | LC_ALL=C grep -Eq '^[0-9a-f]{40}$' || {
    echo 'Backup source revision is invalid.' >&2; exit 1;
}
if [ "$bundle_revision" != "$MALL_RELEASE_REVISION" ] && \
        [ "${E5_ALLOW_FORWARD_MIGRATION:-0}" != 1 ]; then
    echo 'Recovery defaults to the exact backup revision; forward migration requires E5_ALLOW_FORWARD_MIGRATION=1.' >&2
    exit 1
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
case "$E5_BACKUP_HOST_DIR" in
    /*) ;;
    *) echo 'Backup directory must be an absolute path.' >&2; exit 2 ;;
esac
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
test -d "$root/.git" || { echo 'Recover from a standalone Git checkout.' >&2; exit 1; }
test "$(git -C "$root" rev-parse --verify 'HEAD^{commit}')" = "$MALL_RELEASE_REVISION" || {
    echo 'Target revision does not match HEAD.' >&2; exit 1;
}
test -z "$(git -C "$root" status --porcelain --untracked-files=all)" || {
    echo 'Target checkout is not clean.' >&2; exit 1;
}
if [ "$bundle_revision" != "$MALL_RELEASE_REVISION" ]; then
    git -C "$root" merge-base --is-ancestor "$bundle_revision" "$MALL_RELEASE_REVISION" || {
        echo 'Recovery target is not a descendant of the backup revision.' >&2; exit 1;
    }
fi

. "$root/scripts/e5-compose.sh"
source_project=$(compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
source_port=$(compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["admin"]["ports"][0]["published"])')
[ "$E5_RECOVERY_PROJECT" != "$source_project" ] || {
    echo 'Recovery project must differ from the source project.' >&2; exit 1;
}
if [ "$E5_RECOVERY_HTTP_PORT" -eq "$source_port" ]; then
    echo 'Recovery HTTP port must differ from the source port.' >&2; exit 1;
fi
e5_assert_new_project "$E5_RECOVERY_PROJECT"
for volume in "${E5_RECOVERY_PROJECT}_pgdata" "${E5_RECOVERY_PROJECT}_media"; do
    if docker volume inspect "$volume" >/dev/null 2>&1; then
        echo "Recovery volume already exists: $volume" >&2
        exit 1
    fi
done

recovery() {
    E5_ISOLATED_RECOVERY=1 MALL_HTTP_PORT="$E5_RECOVERY_HTTP_PORT" POSTGRES_INIT_DB=postgres \
        POSTGRES_DB="$E5_RECOVERY_DB" E5_RESTORE_DB="$E5_RECOVERY_DB" \
        compose -p "$E5_RECOVERY_PROJECT" "$@"
}

recovery config --quiet
e5_prepare_images recovery restore
. "$root/scripts/e5-preflight.sh"
e5_preflight recovery
recovery up --wait -d db
recovery up --no-deps --force-recreate --exit-code-from media-init media-init
recovery --profile ops run --rm --no-deps restore "/backups/$bundle"
# tar restoration may preserve host ownership; make the isolated media mount app-owned.
recovery up --no-deps --force-recreate --exit-code-from media-init media-init
recovery up --no-deps --force-recreate --exit-code-from release release
recovery up --wait -d web admin
echo "Recovered $bundle in isolated project $E5_RECOVERY_PROJECT."
echo 'Scheduler and export worker remain stopped during recovery verification.'
