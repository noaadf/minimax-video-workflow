"""Narrow ingress for Dot: submit evidence ZIP / read receipt. Deploy behind TLS.

Loopback by default; the token is independent of model keys and SSH credentials.
This service never starts generation or executes supplied commands.
"""
import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from zipfile import BadZipFile

from .queue import Queue
from .research_inbox import MAX_BYTES, ResearchInbox, identifier


def handler(inbox, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not log authorization headers, evidence, or URLs.

        def reply(self, code, value):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(data)
            self.close_connection = True

        def authorized(self):
            value = self.headers.get('Authorization', '').encode()
            if hmac.compare_digest(value, ('Bearer ' + token).encode()):
                return True
            self.reply(401, {'error': 'unauthorized'})
            return False

        def do_POST(self):
            if not self.authorized(): return
            if self.path != '/v1/research-bundles':
                self.reply(404, {'error': 'not_found'}); return
            try:
                if self.headers.get('Content-Type') != 'application/zip':
                    self.reply(415, {'error': 'expected_application_zip'}); return
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BYTES or self.headers.get('Transfer-Encoding'):
                    self.reply(413, {'error': 'invalid_package_size'}); return
                self.connection.settimeout(30)
                blob = self.rfile.read(length)
                if len(blob) != length: raise ValueError('incomplete body')
                receipt = inbox.receive(blob)
                self.reply(200 if receipt['duplicate'] else 201, receipt)
            except (ValueError, KeyError, TypeError, BadZipFile, UnicodeError):
                self.reply(422, {'error': 'invalid_package_or_revision_conflict'})
            except TimeoutError:
                self.reply(408, {'error': 'upload_timeout'})

        def do_GET(self):
            if not self.authorized(): return
            prefix = '/v1/research-bundles/'
            path = urlsplit(self.path).path
            if not path.startswith(prefix):
                self.reply(404, {'error': 'not_found'}); return
            try:
                self.reply(200, inbox.status(identifier(path[len(prefix):])))
            except (KeyError, ValueError):
                self.reply(404, {'error': 'not_found'})
    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', default='output/production')
    p.add_argument('--bind', default='127.0.0.1')
    p.add_argument('--port', default=8771, type=int)
    a = p.parse_args()
    token = os.environ.get('RESEARCH_INBOX_TOKEN', '')
    if len(token) < 32: p.error('RESEARCH_INBOX_TOKEN needs at least 32 characters')
    from pathlib import Path
    inbox = ResearchInbox(Queue(Path(a.root) / 'queue.sqlite'), a.root)
    ThreadingHTTPServer((a.bind, a.port), handler(inbox, token)).serve_forever()


if __name__ == '__main__': main()
