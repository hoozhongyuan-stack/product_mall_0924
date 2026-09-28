#!/bin/sh
# Restore only into a new database and an empty, isolated private-media mount.
set -eu
umask 077

fail() { echo "E5 restore: $*" >&2; exit 1; }
manifest_value() {
  awk -F= -v wanted="$1" '$1 == wanted { n++; value=substr($0, length($1)+2) } END { if (n != 1) exit 1; print value }' "$manifest"
}

[ "$#" -eq 1 ] || fail 'usage: e5-restore.sh BUNDLE_DIRECTORY'
: "${PGHOST:?PGHOST is required}"
: "${PGUSER:?PGUSER is required}"
: "${E5_RESTORE_DB:?E5_RESTORE_DB is required}"
: "${E5_RESTORE_MEDIA_ROOT:?E5_RESTORE_MEDIA_ROOT is required}"
if [ -n "${PGPASSWORD_FILE:-}" ]; then
  [ -f "$PGPASSWORD_FILE" ] && [ ! -L "$PGPASSWORD_FILE" ] || fail 'PostgreSQL secret file is unavailable'
  PGPASSWORD=$(cat "$PGPASSWORD_FILE")
  export PGPASSWORD
fi
: "${PGPASSWORD:?PostgreSQL password is required}"
printf '%s' "$E5_RESTORE_DB" | LC_ALL=C grep -Eq '^[A-Za-z_][A-Za-z0-9_]*$' || fail 'unsupported target database name'
[ "${E5_RESTORE_DB}" != "${PGDATABASE:-}" ] || fail 'target database must differ from the connected source'
bundle=$1
[ -d "$bundle" ] && [ ! -L "$bundle" ] || fail 'bundle must be a real directory'
manifest="$bundle/manifest.txt"
[ -f "$manifest" ] && [ ! -L "$manifest" ] || fail 'manifest is missing or linked'
[ -f "$bundle/database.dump" ] && [ ! -L "$bundle/database.dump" ] || fail 'database dump is missing or linked'
[ -f "$bundle/media.tar" ] && [ ! -L "$bundle/media.tar" ] || fail 'media archive is missing or linked'
[ "$(manifest_value format)" = e5-paired-v1 ] || fail 'unsupported bundle format'
for name in consistent_at_utc database_clients_checked_after_utc completed_at_utc; do
  value=$(manifest_value "$name") || fail "invalid $name"
  printf '%s' "$value" | LC_ALL=C grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' || fail "invalid $name"
done
[ "$(manifest_value database_client_sessions_before)" = 0 ] || fail 'source database was not quiescent before capture'
[ "$(manifest_value database_client_sessions_after)" = 0 ] || fail 'source database was not quiescent after capture'
revision=$(manifest_value release_revision) || fail 'missing release revision'
printf '%s' "$revision" | LC_ALL=C grep -Eq '^[0-9a-f]{40}$' || fail 'invalid release revision'
for pair in 'database.dump database_sha256 database_bytes' 'media.tar media_sha256 media_bytes'; do
  set -- $pair
  filename=$1; hash_field=$2; size_field=$3
  expected_hash=$(manifest_value "$hash_field") || fail "missing $hash_field"
  expected_bytes=$(manifest_value "$size_field") || fail "missing $size_field"
  printf '%s' "$expected_hash" | LC_ALL=C grep -Eq '^[0-9a-f]{64}$' || fail "invalid $hash_field"
  printf '%s' "$expected_bytes" | LC_ALL=C grep -Eq '^[0-9]+$' || fail "invalid $size_field"
  [ "$(wc -c < "$bundle/$filename" | tr -d ' ')" = "$expected_bytes" ] || fail "$filename size mismatch"
  [ "$(sha256sum "$bundle/$filename" | cut -d ' ' -f 1)" = "$expected_hash" ] || fail "$filename hash mismatch"
done

pg_restore --list "$bundle/database.dump" >/dev/null || fail 'invalid PostgreSQL custom dump'
# The archive is generated from application-owned ASCII paths. Check members
# before extraction, including type; no symlinks, links or special files.
listing=$(mktemp) || fail 'cannot create archive listing'
types=$(mktemp) || fail 'cannot create archive type listing'
trap 'rm -f "$listing" "$types"' EXIT HUP INT TERM
tar -tf "$bundle/media.tar" > "$listing" || fail 'invalid media archive'
tar -tvf "$bundle/media.tar" > "$types" || fail 'invalid media archive'
LC_ALL=C perl -ne '
  chomp; die "unsafe archive path\n" unless m{\A\./(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]*/?\z};
  my @parts = split m{/}; shift @parts;
  die "archive traversal\n" if grep { $_ eq q(..) || $_ eq q(.) } @parts;
  die "duplicate archive path\n" if $seen{$_}++;
' "$listing" || fail 'unsafe media archive paths'
LC_ALL=C perl -ne '
  die "unsafe archive node\n" unless /^[d-]/;
' "$types" || fail 'unsafe media archive node type'
[ "$(wc -l < "$listing")" = "$(wc -l < "$types")" ] || fail 'archive listing mismatch'

target=$E5_RESTORE_MEDIA_ROOT
[ ! -L "$target" ] || fail 'target media root is a link'
if [ -e "$target" ]; then
  [ -d "$target" ] || fail 'target media root is not a directory'
  [ -z "$(find "$target" -mindepth 1 -print -quit)" ] || fail 'target media root is not empty'
else
  [ -d "$(dirname "$target")" ] && [ ! -L "$(dirname "$target")" ] || fail 'target media parent is unavailable'
fi
if [ -n "${MALL_MEDIA_ROOT:-}" ] && [ -d "$MALL_MEDIA_ROOT" ]; then
  source_canonical=$(realpath "$MALL_MEDIA_ROOT")
  if [ -e "$target" ]; then
    target_canonical=$(realpath "$target")
  else
    target_canonical=$(realpath "$(dirname "$target")")/$(basename "$target")
  fi
  [ "$target_canonical" != "$source_canonical" ] || fail 'target media root must differ from source'
fi

# createdb fails when the database exists; pg_restore never uses --clean.
createdb "$E5_RESTORE_DB" || fail 'target database already exists or cannot be created'
pg_restore --exit-on-error --no-owner --no-acl --dbname="$E5_RESTORE_DB" "$bundle/database.dump" || fail 'pg_restore failed; isolated target remains for inspection'
if [ ! -e "$target" ]; then mkdir -m 700 -- "$target" || fail 'cannot create target media root'; fi
tar -xf "$bundle/media.tar" -C "$target" || fail 'media extraction failed; isolated target remains for inspection'
find "$target" -type d -exec chmod 700 {} +
find "$target" -type f -exec chmod 600 {} +
printf 'restored_revision=%s\nconsistent_at_utc=%s\n' "$revision" "$(manifest_value consistent_at_utc)"
