#!/usr/bin/env python3
"""Private same-origin console gateway; keys never enter source or HTTP logs."""

import argparse
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import stat
import threading
from urllib.parse import urlsplit

HOME = Path.home()
PROXY = HOME / '.local/share/ranton-proxy'
RUNTIME = HOME / '.local/share/ranton-console'
FLEET = HOME / '.local/share/codex-fleet/runtime'
PRIVATE_HOST = 'macbook-pro.tail9ad173.ts.net:8443'


def private_key(path):
    if not path.is_absolute():
        raise RuntimeError('Absolute credential path required')
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    descriptor = None
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            previous, directory = directory, child
            os.close(previous)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        meta = os.fstat(descriptor)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) != 0o600:
            raise RuntimeError('Credential permissions must be owner-only')
        key = os.read(descriptor, 4096).decode().strip()
        if not key or len(key) >= 4096:
            raise RuntimeError('A nonempty bounded credential is required')
        return key
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def upstream(port, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=60)
    connection.request(method, path, body=body, headers=headers or {})
    return connection, connection.getresponse()


def usage_summary(payload):
    # Credential counters are non-destructive; the upstream usage queue is not.
    files = payload.get('files')
    if not isinstance(files, list):
        return {'available': False}
    success = failed = 0
    for entry in files:
        if not isinstance(entry, dict) or entry.get('provider', entry.get('type')) != 'codex':
            continue
        for key in ('success', 'failed'):
            value = entry.get(key)
            if type(value) is not int or value < 0:
                return {'available': False}
        success += entry['success']
        failed += entry['failed']
    return {'available': True, 'requests': success + failed, 'success': success, 'failed': failed}


class Handler(BaseHTTPRequestHandler):
    server_version = 'RantonConsole'

    def log_message(self, *_):
        pass

    def reply(self, code, body, content_type='application/json'):
        raw = json.dumps(body, allow_nan=False).encode() if content_type == 'application/json' else body
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(raw)

    def allowed(self):
        host = self.headers.get('Host', '')
        if host not in ('127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port), PRIVATE_HOST):
            return False
        origin = self.headers.get('Origin')
        expected = ('https://' if host == PRIVATE_HOST else 'http://') + host
        return origin is None or origin == expected

    def authenticated(self):
        auth = self.headers.get('Authorization', '')
        return auth.startswith('Bearer ') and hmac.compare_digest(auth[7:], self.server.management_key)

    def body(self):
        if self.headers.get('Transfer-Encoding'):
            raise ValueError('Chunked request bodies are not accepted by the management bridge')
        length = int(self.headers.get('Content-Length', 0))
        if not 0 <= length <= 16 * 1024 * 1024:
            raise ValueError('Request body is too large')
        return self.rfile.read(length) if length else None

    def forward(self, port, path, body, key):
        headers = {name: value for name, value in self.headers.items() if name.lower() not in
                   ('host', 'connection', 'content-length', 'transfer-encoding', 'cookie', 'origin',
                    'authorization', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto')}
        headers['Authorization'] = 'Bearer ' + key
        connection, response = upstream(port, self.command, path, body, headers)
        try:
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in ('connection', 'transfer-encoding', 'content-length', 'set-cookie', 'access-control-allow-origin', 'content-security-policy'):
                    self.send_header(name, value)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            while True:
                chunk = response.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            connection.close()

    def handle_request(self):
        if not self.allowed():
            self.reply(403, {'error': 'Request origin or host is not allowed'})
            return
        path = urlsplit(self.path).path
        if path in ('/', '/management.html') and self.command == 'GET':
            self.reply(200, self.server.asset.read_bytes(), 'text/html; charset=utf-8')
            return
        if path == '/health' and self.command == 'GET':
            self.reply(200, {'status': 'ok', 'console': 'ranton-fleet-console'})
            return
        if not self.authenticated():
            self.reply(401, {'error': 'Management key required'})
            return
        body = self.body()
        prefix = '/v8/management/fleet'
        if path == prefix + '/status' and self.command == 'GET':
            connection, response = upstream(8765, 'GET', '/api/observe', headers={'Authorization': 'Bearer ' + self.server.fleet_key})
            try:
                if response.status != 200:
                    self.reply(503, {'error': 'Coordinator is unavailable'})
                    return
                payload = json.loads(response.read(8 * 1024 * 1024))
            finally:
                connection.close()
            payload['gateway'] = {'available': False}
            configured = False
            try:
                connection, response = upstream(8317, 'GET', '/v8/management/credentials', headers={'Authorization': 'Bearer ' + self.server.management_key})
                try:
                    if response.status == 200:
                        payload['gateway'] = usage_summary(json.loads(response.read(8 * 1024 * 1024)))
                finally:
                    connection.close()
                connection, response = upstream(8317, 'GET', '/v8/management/plugins', headers={'Authorization': 'Bearer ' + self.server.management_key})
                try:
                    if response.status == 200:
                        configured = any(item.get('id') == 'ranton-router' and item.get('effective_enabled') is True for item in json.loads(response.read(65536)).get('plugins', []))
                finally:
                    connection.close()
            except (OSError, ValueError, http.client.HTTPException):
                pass
            payload['routing']['gatewayConfigured'] = payload['routing']['gatewayConfigured'] and configured
            self.reply(200, payload)
            return
        if path.startswith(prefix + '/'):
            suffix = path[len(prefix):]
            if self.command == 'GET' and suffix.startswith('/tasks/') and len(suffix.split('/')) == 3:
                target = '/api/task/' + suffix.split('/')[-1]
            elif self.command == 'POST' and (suffix in ('/tasks', '/projects', '/dispatch', '/refresh', '/policy') or
                    (len(suffix.split('/')) == 4 and suffix.startswith('/tasks/') and suffix.split('/')[-1] in ('pause', 'resume', 'complete', 'followup'))):
                target = '/api' + suffix
            else:
                self.reply(404, {'error': 'Unknown fleet operation'})
                return
            self.forward(8765, target, body, self.server.fleet_key)
        elif path.startswith('/v8/management/'):
            self.forward(8317, self.path, body, self.server.management_key)
        else:
            self.reply(404, {'error': 'Unknown management path. Use the native proxy endpoint for inference.'})

    def do_GET(self):
        try:
            self.handle_request()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except ValueError:
            self.reply(400, {'error': 'Invalid request'})
        except (OSError, http.client.HTTPException):
            self.reply(503, {'error': 'Local service is unavailable; state was preserved'})

    do_POST = do_GET
    do_PUT = do_GET
    do_PATCH = do_GET
    do_DELETE = do_GET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8318)
    parser.add_argument('--asset', type=Path, default=RUNTIME / 'management.html')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    server.management_key = private_key(PROXY / 'management.key')
    server.fleet_key = private_key(FLEET / 'admin.key')
    server.asset = args.asset
    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
