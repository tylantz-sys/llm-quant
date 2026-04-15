#!/usr/bin/env python3
"""
Simple MCP client that reads .vscode/mcp.json and does a GET and POST to the configured server.
"""
import json
import sys
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

CONFIG_PATH = '.vscode/mcp.json'

def load_config(path=CONFIG_PATH):
    with open(path, 'r') as f:
        return json.load(f)

def do_request(method, url, headers=None, data=None):
    req = Request(url, data=data, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            return resp.getcode(), body
    except HTTPError as e:
        return e.code, e.read().decode(errors='ignore')
    except URLError as e:
        return None, f'URLError: {e}'


def main():
    conf = load_config()
    # pick the first server entry
    servers = conf.get('servers', {})
    if not servers:
        print('No servers in config', file=sys.stderr)
        sys.exit(2)
    name, info = next(iter(servers.items()))
    url = info.get('url')
    headers = info.get('headers', {})

    print(f'Using server entry: {name} -> {url}')

    code, body = do_request('GET', url, headers=headers)
    print('GET:', 'status='+str(code), 'body=' + (body or ''))

    payload = json.dumps({'test': 'hello from test_mcp_client'}).encode()
    headers_with_ct = dict(headers)
    headers_with_ct['Content-Type'] = 'application/json'
    code, body = do_request('POST', url, headers=headers_with_ct, data=payload)
    print('POST:', 'status='+str(code), 'body=' + (body or ''))

if __name__ == '__main__':
    main()
