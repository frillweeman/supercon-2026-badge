"""Local preview of web/webnametag (index.html, codes.html) with a mock BLE badge (tools/mock_ble.js injected
into every page). Desktop Chrome only; real Bluetooth needs the HTTPS-hosted page.
Host-side only (tools/ is not synced to the badge). Usage: tools/preview.py [port]
Try http://localhost:8080/codes.html?type=qrcode&content=hello&known=1"""
import mimetypes, sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOLS = Path(__file__).resolve().parent
WEB = TOOLS.parent / "web" / "webnametag"

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        name = self.path.split("?")[0].lstrip("/") or "index.html"
        f = TOOLS / name if name == "mock_ble.js" else (WEB / name).resolve()
        if not f.is_file() or (f.parent != WEB and f.parent != TOOLS):
            self.send_response(404); self.end_headers(); return
        body = f.read_bytes()
        if f.suffix == ".html":  # inject the mock before the page's own scripts run
            body = body.replace(b"<script src=", b"<script src=mock_ble.js></script><script src=", 1)
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
print(f"http://localhost:{port}"); ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
