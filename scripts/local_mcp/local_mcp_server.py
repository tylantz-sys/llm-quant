#!/usr/bin/env python3
"""
Minimal local MCP stub server for development.
Usage: python3 scripts/local_mcp/local_mcp_server.py --port 8080

Endpoints:
- GET /mcp -> {"status":"ok","server":"local-mcp"}
- POST /mcp -> echoes back JSON body with metadata

This intentionally has no external dependencies.
"""
import argparse
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

class MCPHandler(BaseHTTPRequestHandler):
    def _set_json(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length:
            return self.rfile.read(length)
        return b''

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/mcp':
            resp = {'status': 'ok', 'server': 'local-mcp'}
            self._set_json(200)
            self.wfile.write(json.dumps(resp).encode())
        else:
            self.send_error(404, 'Not Found')

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/mcp':
            raw = self._read_body()
            try:
                body = json.loads(raw.decode()) if raw else None
            except Exception:
                body = raw.decode(errors='replace')
            resp = {
                'status': 'ok',
                'server': 'local-mcp',
                'headers': {k: v for k, v in self.headers.items()},
                'body': body,
            }
            self._set_json(200)
            self.wfile.write(json.dumps(resp).encode())
        else:
            self.send_error(404, 'Not Found')

    def log_message(self, format, *args):
        # prefix logs to make them easy to spot
        super().log_message("[local-mcp] %s", format % args)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()

    server_address = ('', args.port)
    httpd = HTTPServer(server_address, MCPHandler)
    print(f'Local MCP stub running on http://localhost:{args.port}/mcp')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down local MCP stub')
        httpd.server_close()

if __name__ == '__main__':
    main()
