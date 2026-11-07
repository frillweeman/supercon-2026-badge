"""Local preview of the web/webnametag page with a mock BLE badge (fake navigator.bluetooth, pairing code 1234).
Desktop Chrome only; real Bluetooth needs the HTTPS-hosted page.
Host-side only (tools/ is not synced to the badge). Usage: tools/preview.py [port]

The mock mimics the badge: a submission keeps it "busy" for BUSY_S seconds, during which
POSTs get 503 and GET /status reports busy. Uploads are read slowly so progress is visible."""
import json, sys, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

WEB = Path(__file__).resolve().parent.parent / "web" / "webnametag"
PHOTO_H = 672
BUSY_S = 8
state = {"name": "Ada Lovelace", "mode": "name", "busy_until": 0.0}

def busy():
    return time.time() < state["busy_until"]

class H(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype="text/plain"):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path.startswith("/status"):
            return self.reply(200, json.dumps({"busy": int(busy()), "mode": state["mode"], "name": state["name"]}).encode(), "application/json")
        name = {"/": "index.html", "/app.js": "app.js", "/mock_ble.js": "../../tools/mock_ble.js"}.get(self.path.split("?")[0])
        if not name:
            return self.reply(404, b"Not found")
        body = (WEB / name).read_bytes()
        if name == "index.html":  # inject the mock before app.js runs
            body = body.replace(b"<script src=app.js", b"<script src=mock_ble.js></script><script src=app.js")
        self.reply(200, body, "text/html" if name.endswith(".html") else "application/javascript")
    def do_POST(self):
        n = int(self.headers["Content-Length"]); body = b""
        while len(body) < n:  # slow read so upload progress is visible
            body += self.rfile.read(min(8192, n - len(body))); time.sleep(0.03)
        if busy():
            return self.reply(503, b"Badge is busy updating its display. Wait a few seconds and try again.")
        if self.path == "/photo":
            if n != 120 * PHOTO_H and n != 240 * PHOTO_H:
                return self.reply(400, b"Bad photo size")
            state["mode"] = "photo"; msg = b"Photo received (mock)."
        else:
            state["name"] = parse_qs(body.decode()).get("name", [""])[0][:24]; state["mode"] = "name"
            msg = state["name"].encode()
        state["busy_until"] = time.time() + BUSY_S
        self.reply(200, msg)
    def log_message(self, *a): pass

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
print(f"http://localhost:{port}"); ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
