"""Local preview of the apps/a3_webnametag.py web page with a mock badge backend.
Host-side only (tools/ is not synced to the badge). Usage: tools/preview.py [port]

The mock mimics the badge: a submission keeps it "busy" for BUSY_S seconds, during which
POSTs get 503 and GET /status reports busy. Uploads are read slowly so progress is visible."""
import ast, json, sys, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

SRC = Path(__file__).resolve().parent.parent / "apps" / "a3_webnametag.py"
tree = ast.parse(SRC.open().read())
consts = {}
for n in tree.body:
    if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name):
        try: consts[n.targets[0].id] = ast.literal_eval(n.value)
        except Exception: pass
PAGE = consts["_PAGE"]
PHOTO_H = 672
BUSY_S = 8
state = {"name": "Ada Lovelace", "mode": "name", "busy_until": 0.0}

def busy():
    return time.time() < state["busy_until"]

def render():
    return (PAGE.replace("%NAME%", state["name"]).replace("%HOST%", "willboard.local").replace("%MAX%", "24")
            .replace("%H%", str(PHOTO_H)).replace("%QH%", str(PHOTO_H // 2))
            .replace("%HEIC_URL%", consts["HEIC_URL"]).replace("%HEIC_SRI%", consts["HEIC_SRI"])).encode()

class H(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype="text/plain"):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path.startswith("/status"):
            return self.reply(200, json.dumps({"busy": int(busy()), "mode": state["mode"], "name": state["name"]}).encode(), "application/json")
        self.reply(200, render(), "text/html")
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
