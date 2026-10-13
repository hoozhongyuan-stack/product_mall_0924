"""Loopback-only source preview for the native mini-program, with an isolated API proxy."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
UI = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--config', required=True, help='Private local fixture JSON outside the repository')
parser.add_argument('--port', type=int, default=8874)
args = parser.parse_args()
CONFIG = json.loads(Path(args.config).read_text())
ORIGIN = CONFIG['apiBaseUrl'].rstrip('/')
if urlsplit(ORIGIN).hostname not in ('localhost', '127.0.0.1', '::1'):
    raise SystemExit('Preview API must use an isolated loopback host.')

class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def handle_request(self):
        port = self.server.server_address[1]
        allowed_hosts = (f'127.0.0.1:{port}', f'localhost:{port}')
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        if host not in allowed_hosts or (origin and origin != f'http://{host}') or (self.command != 'GET' and not origin):
            self.send(403, b'{}'); return
        length = self.headers.get('Content-Length', '0')
        if not length.isdigit() or len(length) > 8 or int(length) > 1048576 or self.headers.get('Transfer-Encoding'):
            self.send(413, b'{}'); return
        if self.path.startswith('//') or urlsplit(self.path).scheme or urlsplit(self.path).netloc:
            self.send(400, b'{}'); return
        path = urlsplit(self.path).path
        if path.startswith('/api/'):
            body = self.rfile.read(int(length)) if self.command != 'GET' else None
            headers = {key: value for key, value in self.headers.items() if key.lower() in ('authorization', 'content-type', 'idempotency-key')}
            request = Request(ORIGIN+self.path, data=body, headers=headers, method=self.command)
            try:
                with urlopen(request, timeout=20) as response:
                    self.send(response.status, response.read(), response.headers.get('Content-Type', 'application/json'))
            except HTTPError as error:
                self.send(error.code, error.read(), error.headers.get('Content-Type', 'application/json'))
            except Exception:
                self.send(502, json.dumps({'success': False, 'error': {'message': '本地隔离接口连接失败。'}}).encode())
            return
        if self.command != 'GET':
            self.send(405, b'{}'); return
        if path == '/preview/config':
            try:
                current = json.loads(Path(args.config).read_text())
                config = {**CONFIG, 'orders': current.get('orders', CONFIG.get('orders', []))}
                self.send(200, json.dumps(config, ensure_ascii=False).encode())
            except (OSError, ValueError):
                self.send(500, b'{}')
            return
        target = (ROOT/path.removeprefix('/mini/')).resolve() if path.startswith('/mini/') else (UI/('index.html' if path == '/' else path.lstrip('/'))).resolve()
        allowed = target.is_relative_to(ROOT) if path.startswith('/mini/') else target.is_relative_to(UI)
        if not allowed or target.suffix not in ('.html', '.js', '.json', '.wxml', '.wxss', '.svg', '.png') or not target.is_file():
            self.send(404, b'{}'); return
        content_type = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png'}.get(target.suffix, 'text/plain; charset=utf-8')
        self.send(200, target.read_bytes(), content_type)

    do_GET = handle_request
    do_POST = handle_request
    do_PATCH = handle_request
    do_PUT = handle_request
    do_DELETE = handle_request

print(f'Native source preview: http://127.0.0.1:{args.port}', flush=True)
ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
