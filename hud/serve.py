#!/usr/bin/env python3
# ============================================================
#  Audi kiTT — serveur de dev du HUD               hud/serve.py
# ============================================================
#  Sert les fichiers statiques du HUD (comme http.server) et accepte
#  en plus POST /capture : le HUD peut y pousser une capture du canvas
#  WebGL (data URL) qui est écrite dans capture.jpg à côté de ce script.
#  Utile pour vérifier le rendu quand la fenêtre de préview est masquée
#  (le canvas ne composite plus, mais il peut toujours se rendre offscreen).
#
#  Usage : python serve.py [port]        (défaut : 8420)
# ============================================================
import base64
import http.server
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8420


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def do_POST(self):
        if self.path != "/capture":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length).decode("ascii", errors="ignore")
        # data = "data:image/jpeg;base64,...."
        b64 = data.split(",", 1)[1] if "," in data else data
        out = os.path.join(HERE, "capture.jpg")
        with open(out, "wb") as f:
            f.write(base64.b64decode(b64))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")
        print(f"[capture] {out} ({length} octets recus)", flush=True)


if __name__ == "__main__":
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as srv:
        print(f"kiTT HUD: http://localhost:{PORT}", flush=True)
        srv.serve_forever()
