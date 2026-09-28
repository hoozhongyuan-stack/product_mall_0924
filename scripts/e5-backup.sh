#!/bin/sh
# Run inside the pinned PostgreSQL tools image with a read-only media mount.
set -eu
umask 077

fail() { echo "E5 backup: $*" >&2; exit 1; }
require_directory() { [ -d "$1" ] && [ ! -L "$1" ] || fail "expected a real directory: $1"; }
require_no_clients() {
  active=$(psql -X -A -t -v ON_ERROR_STOP=1 --dbname="$PGDATABASE" \
    -c "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend'" ) || fail 'cannot inspect database clients'
  printf '%s' "$active" | LC_ALL=C grep -Eq '^[0-9]+$' || fail 'invalid database client count'
  [ "$active" = 0 ] || fail "database still has $active other client session(s)"
}

[ "${E5_WRITERS_PAUSED:-}" = 1 ] || fail 'writers must be paused by the orchestration gate'
: "${PGHOST:?PGHOST is required}"
: "${PGUSER:?PGUSER is required}"
: "${PGDATABASE:?PGDATABASE is required}"
: "${MALL_MEDIA_ROOT:?MALL_MEDIA_ROOT is required}"
: "${E5_BACKUP_ROOT:?E5_BACKUP_ROOT is required}"
: "${MALL_RELEASE_REVISION:?MALL_RELEASE_REVISION is required}"
if [ -n "${PGPASSWORD_FILE:-}" ]; then
  [ -f "$PGPASSWORD_FILE" ] && [ ! -L "$PGPASSWORD_FILE" ] || fail 'PostgreSQL secret file is unavailable'
  PGPASSWORD=$(cat "$PGPASSWORD_FILE")
  export PGPASSWORD
fi
: "${PGPASSWORD:?PostgreSQL password is required}"
[ "${#MALL_RELEASE_REVISION}" -eq 40 ] &&
  printf '%s' "$MALL_RELEASE_REVISION" | LC_ALL=C grep -Eq '^[0-9a-f]{40}$' || fail 'release revision must be a full SHA-1 commit'
printf '%s' "$PGDATABASE" | LC_ALL=C grep -Eq '^[A-Za-z_][A-Za-z0-9_]*$' || fail 'unsupported database name'
require_directory "$MALL_MEDIA_ROOT"
require_directory "$E5_BACKUP_ROOT"

# Storage keys are application-owned ASCII paths. Reject links and unusual nodes;
# fail the whole backup rather than silently omitting private content.
perl -MFile::Find -e '
  use strict; use warnings;
  my $root = shift; $root =~ s{/$}{};
  File::Find::find({ no_chdir => 1, follow => 0, wanted => sub {
    my $path = $File::Find::name;
    return if $path eq $root;
    my $relative = substr($path, length($root) + 1);
    die "unsafe media path\n" unless length($relative) <= 98
      && $relative =~ m{\A[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\z}
      && !grep { $_ eq q(.) || $_ eq q(..) } split m{/}, $relative;
    die "media link or special node\n" unless !-l $path && (-f $path || -d $path);
  } }, $root);
' "$MALL_MEDIA_ROOT" || fail 'private media preflight failed'

require_no_clients
started_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
temporary=$(mktemp -d "$E5_BACKUP_ROOT/.e5-incomplete.XXXXXXXX") || fail 'cannot reserve backup directory'
cleanup() { [ ! -d "$temporary" ] || rm -rf -- "$temporary"; }
trap cleanup EXIT HUP INT TERM

pg_dump --format=custom --no-owner --no-acl --file="$temporary/database.dump" "$PGDATABASE" || fail 'pg_dump failed'
tar -cf "$temporary/media.tar" -C "$MALL_MEDIA_ROOT" . || fail 'media archive failed'
require_no_clients
checked_after_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

database_sha256=$(sha256sum "$temporary/database.dump" | cut -d ' ' -f 1)
media_sha256=$(sha256sum "$temporary/media.tar" | cut -d ' ' -f 1)
database_bytes=$(wc -c < "$temporary/database.dump" | tr -d ' ')
media_bytes=$(wc -c < "$temporary/media.tar" | tr -d ' ')
completed_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
cat > "$temporary/manifest.txt" <<EOF
format=e5-paired-v1
consistent_at_utc=$started_at
completed_at_utc=$completed_at
release_revision=$MALL_RELEASE_REVISION
source_database=$PGDATABASE
database_client_sessions_before=0
database_client_sessions_after=0
database_clients_checked_after_utc=$checked_after_at
database_sha256=$database_sha256
database_bytes=$database_bytes
media_sha256=$media_sha256
media_bytes=$media_bytes
EOF
sync

bundle="$E5_BACKUP_ROOT/e5-${started_at%%T*}-$(basename "$temporary" | sed 's/^\.e5-incomplete\.//')"
[ ! -e "$bundle" ] || fail 'backup destination already exists'
mv -- "$temporary" "$bundle" || fail 'cannot publish completed backup'
sync
trap - EXIT HUP INT TERM
printf '%s\n' "$bundle"
