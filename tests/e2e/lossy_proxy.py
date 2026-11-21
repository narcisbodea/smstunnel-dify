#!/usr/bin/env python3
"""LOCAL ONLY. A proxy in front of the local API that can lose an answer.

For the number in LOSE_TO it forwards the send, so the text really goes out, then closes the connection without
answering. That is the case a retry would turn into a second text.

    python tests/e2e/lossy_proxy.py 3233 http://127.0.0.1:3231 +40712345699 requests.log
"""

import http.client
import json
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

PORT = int(sys.argv[1])
UPSTREAM = urlsplit(sys.argv[2])
LOSE_TO = sys.argv[3]
LOG = sys.argv[4]
if UPSTREAM.hostname not in ("127.0.0.1", "localhost"):
    sys.exit("The proxy only forwards to a local API.")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _forward(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection", "accept-encoding")}
        upstream = http.client.HTTPConnection(UPSTREAM.hostname, UPSTREAM.port, timeout=20)
        upstream.request(self.command, self.path, body=body, headers=headers)
        answer = upstream.getresponse()
        data = answer.read()

        target = ""
        if self.command == "POST" and self.path == "/api/v1/sms/send" and body:
            try:
                target = json.loads(body).get("to", "")
            except ValueError:
                target = ""
            with open(LOG, "a", encoding="utf-8") as log:
                log.write(f"POST {target}\n")
        if target == LOSE_TO:
            # The server did the work; the caller never hears about it.
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            upstream.close()
            return

        self.send_response(answer.status)
        for name, value in answer.getheaders():
            if name.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        upstream.close()

    do_GET = do_POST = _forward


ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
