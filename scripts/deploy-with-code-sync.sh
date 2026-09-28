#!/bin/sh
# Deployment gate. The caller pins MALL_RELEASE_REVISION to the checked-out commit.
set -eu

: "${MALL_RELEASE_REVISION:?Set MALL_RELEASE_REVISION to the full release commit SHA}"
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
python_bin=${MALL_PYTHON:-python3}

release_git() {
  env -i PATH="${PATH:-/usr/bin:/bin}" GIT_NO_REPLACE_OBJECTS=1 \
    git --no-replace-objects -C "$project_root" "$@"
}

# Run this before loading Django settings or migrations from the release tree.
actual_root=$(release_git rev-parse --show-toplevel)
actual_revision=$(release_git rev-parse --verify 'HEAD^{commit}')
if [ "$actual_root" != "$project_root" ] || \
   [ "$actual_revision" != "$MALL_RELEASE_REVISION" ]; then
  echo 'Deployment source revision mismatch.' >&2
  exit 1
fi
checkout_changes=$(release_git status --porcelain --untracked-files=all)
if [ -n "$checkout_changes" ]; then
  echo 'Deployment source checkout is not clean.' >&2
  exit 1
fi

"$python_bin" "$project_root/backend/manage.py" migrate --noinput
"$python_bin" "$project_root/backend/manage.py" sync_deployed_miniprogram \
  --expected-revision "$MALL_RELEASE_REVISION"

if [ "$#" -gt 0 ]; then
  exec "$@"
fi
