"""Temporary diagnostic entrypoint: run the app, but if anything raises at
import/startup, serve the traceback over HTTP instead of crashing so the
error can be read remotely."""
import traceback
import http.server
import socketserver
import os

try:
    import app.api.main
    import uvicorn

    uvicorn.run(app.api.main.app, host="0.0.0.0", port=int(os.environ["PORT"]))
except Exception:
    err = traceback.format_exc()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = err.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    socketserver.TCPServer(("0.0.0.0", int(os.environ["PORT"])), H).serve_forever()
