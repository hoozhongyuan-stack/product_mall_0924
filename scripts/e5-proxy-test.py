"""Two disposable, network-disabled Nginx containers; no host ports or real upstream."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent.parent
IMAGE = 'nginx:stable-alpine3.24-slim@sha256:32463212baf0e7d91aded2e9b843a4f2b9e017804b8c9d5bae7b51dcef64389c'
HARNESS = r'''
set -eu
nginx -t
mkfifo /tmp/response
printf 'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok' >/tmp/response &
nc -l -p 8000 </tmp/response >/tmp/request &
upstream=$!
nginx -g 'daemon off;' &
proxy=$!
trap 'kill "$proxy" 2>/dev/null || true; kill "$upstream" 2>/dev/null || true' EXIT
for attempt in 1 2 3 4 5; do
    if wget -q -O /dev/null http://127.0.0.1:8080/; then break; fi
    sleep 1
done
wget -q -O /dev/null --header='X-Forwarded-Proto: https,http' \
    --header='X-Forwarded-SSL: on' --header='X-Forwarded-Protocol: ssl' \
    http://127.0.0.1:8080/api/synthetic
wait "$upstream"
sed -n '/^X-Forwarded-/p' /tmp/request
'''


class ProxyBoundaryTests(unittest.TestCase):
    def test_client_forwarding_headers_are_overwritten_at_the_only_proxy_boundary(self):
        for file, expected in [('nginx-proxy-default.conf', 'http'), ('nginx-proxy-https.conf', 'https')]:
            with self.subTest(file=file):
                result = subprocess.run(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none',
                    '--read-only', '--add-host', 'web:127.0.0.1',
                    '--tmpfs', '/tmp:rw,nosuid,size=16777216',
                    '--tmpfs', '/var/cache/nginx:rw,nosuid,size=16777216',
                    '--tmpfs', '/var/run:rw,nosuid,size=1048576',
                    '--mount', f'type=bind,source={ROOT}/deploy/nginx.conf,target=/etc/nginx/conf.d/default.conf,readonly',
                    '--mount', f'type=bind,source={ROOT}/deploy/{file},target=/etc/nginx/mall-proxy-scheme.conf,readonly',
                    '--entrypoint', 'sh', IMAGE, '-c', HARNESS], text=True, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = result.stdout.splitlines()
                self.assertEqual([line for line in lines if line.startswith('X-Forwarded-Proto:')],
                                 ['X-Forwarded-Proto: ' + expected])
                self.assertFalse(any(line.startswith(('X-Forwarded-SSL:', 'X-Forwarded-Protocol:')) for line in lines))


if __name__ == '__main__': unittest.main()
