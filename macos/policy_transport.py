"""Serve a fixed rule snapshot on loopback for the duration of one update."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading


@contextmanager
def serve(port, documents):
    documents = dict(documents)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            content = documents.get(self.path)
            self.send_response(200 if content is not None else 404)
            self.send_header('Content-Type', 'application/yaml')
            self.send_header('Content-Length', str(len(content) if content is not None else 0))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if content is not None:
                self.server.received.add(self.path)
                self.wfile.write(content)

        def log_message(self, *args):
            # Paths carry an unguessable token. Never put them in logs.
            pass

    class Server(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(5)
            return connection, address

        def handle_error(self, request, client_address):
            pass

    server = Server(('127.0.0.1', port), Handler)
    server.received = set()
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
