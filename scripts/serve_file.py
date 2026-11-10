"""Serve one sample PDF with CORS, so the Rebind page in the browser can fetch and upload it."""
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DATA = Path(sys.argv[1]).read_bytes()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()
        self.wfile.write(DATA)

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", int(sys.argv[2])), Handler).serve_forever()
