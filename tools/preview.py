"""Local preview of the apps/webnametag.py web page with a mock badge backend.
Host-side only (tools/ is not synced to the badge). Usage: tools/preview.py [port]"""
import ast, sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

SRC = Path(__file__).resolve().parent.parent / "apps" / "webnametag.py"
tree = ast.parse(SRC.open().read())
consts = {}
for n in tree.body:
    if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name):
        try: consts[n.targets[0].id] = ast.literal_eval(n.value)
        except Exception: pass
PAGE = consts["_PAGE"]
PHOTO_H = 640
name = ["Ada Lovelace"]

def render():
    return (PAGE.replace("%NAME%", name[0]).replace("%MAX%", "24")
            .replace("%H%", str(PHOTO_H)).replace("%QH%", str(PHOTO_H // 2))
            .replace("%HEIC_URL%", consts["HEIC_URL"]).replace("%HEIC_SRI%", consts["HEIC_SRI"])).encode()

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(render())
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/photo":
            ok = len(body) == 120 * PHOTO_H
            self.send_response(200 if ok else 400); self.end_headers()
            self.wfile.write(b"Photo received (mock)." if ok else b"Bad photo size")
        else:
            name[0] = parse_qs(body.decode()).get("name", [""])[0][:24]
            self.send_response(303); self.send_header("Location", "/"); self.end_headers()
    def log_message(self, *a): pass

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
print(f"http://localhost:{port}"); HTTPServer(("0.0.0.0", port), H).serve_forever()
