"""Local-only browser dashboard. Launch with the repository's Python environment."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import argparse
import gzip
import json
import sys
import threading

from market import Market

STATIC = Path(__file__).resolve().parent/'static'


def make_handler(market):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            # Custom non-simple header and no CORS keep unrelated websites from
            # stopping the local recorder. The launcher issues this locally.
            origin = self.headers.get('Origin')
            expected = f'http://127.0.0.1:{self.server.server_port}'
            if self.path != '/api/shutdown' or self.headers.get('X-Option-Dashboard') != 'local-stop' or origin not in (None, expected):
                self.respond(b'Forbidden', 'text/plain', 403)
                return
            self.respond(b'{"stopping":true}', 'application/json')
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def do_GET(self):
            url = urlparse(self.path)
            args = parse_qs(url.query)
            get = lambda k, default='': args.get(k, [default])[0]
            try:
                if url.path == '/api/catalog':
                    value = market.catalog()
                elif url.path == '/api/chain':
                    value = market.chain(get('product'))
                elif url.path == '/api/search':
                    value = market.search(get('q'))
                elif url.path == '/api/history':
                    value = market.history(get('code'), get('date'), get('refresh')=='1')
                elif url.path in ('/', '/index.html', '/app.js', '/style.css'):
                    filename = 'index.html' if url.path=='/' else url.path[1:]
                    content = (STATIC/filename).read_bytes()
                    self.respond(content, {'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8'}[Path(filename).suffix])
                    return
                else:
                    self.respond(b'Not found', 'text/plain', 404)
                    return
                self.respond(json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), 'application/json; charset=utf-8')
            except ValueError as exc:
                self.respond(json.dumps({'error':str(exc)}, ensure_ascii=False).encode(), 'application/json; charset=utf-8', 400)
            except Exception as exc:
                market.error(exc)
                self.respond(json.dumps({'error':str(exc)}, ensure_ascii=False).encode(), 'application/json; charset=utf-8', 500)

        def respond(self, content, mime, status=200):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            if len(content)>2048 and 'gzip' in self.headers.get('Accept-Encoding',''):
                content = gzip.compress(content, compresslevel=3)
                self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, fmt, *args):
            if len(args)>1 and str(args[1]) not in ('200','304'):
                super().log_message(fmt, *args)
    return Handler


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    market = Market()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(market))
    market.start()
    print(f'商品期权盘口 http://127.0.0.1:{args.port}  只读行情 / Ctrl+C 退出', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        market.close()
        server.server_close()


if __name__ == '__main__':
    main()
