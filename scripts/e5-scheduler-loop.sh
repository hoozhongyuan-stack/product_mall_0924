#!/bin/sh
set -eu

interval=${E5_MAINTENANCE_INTERVAL_SECONDS:-60}
case "$interval" in
    ''|*[!0-9]*) echo 'Invalid maintenance interval.' >&2; exit 1 ;;
esac
if [ "$interval" -lt 30 ] || [ "$interval" -gt 3600 ]; then
    echo 'Maintenance interval must be 30-3600 seconds.' >&2
    exit 1
fi

while :; do
    if ! timeout --signal=TERM --kill-after=5s 120s \
            python /app/backend/manage.py run_maintenance_tick --limit 100; then
        echo 'E5 maintenance tick failed; retrying after interval.' >&2
    fi
    sleep "$interval"
done
