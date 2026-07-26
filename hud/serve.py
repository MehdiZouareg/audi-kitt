#!/usr/bin/env python3
# ============================================================
#  Audi kiTT — serveur de dev du HUD               hud/serve.py
# ============================================================
#  Sert les fichiers statiques du HUD et joue les intermédiaires vers
#  les services OpenStreetMap (proxy même-origine + cache disque, pour
#  éviter le CORS côté navigateur et ménager les serveurs publics) :
#
#    GET /osm?lat=..&lon=..&r=500     bâtiments + routes (Overpass API)
#    GET /geocode?q=..                recherche d'adresse (Nominatim)
#    GET /route?flat&flon&tlat&tlon   itinéraire voiture (OSRM démo)
#    POST /capture                    capture du canvas WebGL (debug)
#
#  Usage : python serve.py [port]        (défaut : 8420)
# ============================================================
import base64
import hashlib
import http.server
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8420
UA = "audi-kitt-hud/0.1 (prototype perso; github.com/MehdiZouareg/audi-kitt)"

os.makedirs(CACHE, exist_ok=True)


def fetch_json(url: str, data: bytes | None = None, cache_key: str | None = None):
    """GET/POST une URL et renvoie le JSON (bytes), avec cache disque optionnel."""
    if cache_key:
        path = os.path.join(CACHE, cache_key + ".json")
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as resp:
        body = resp.read()
    if cache_key:
        with open(os.path.join(CACHE, cache_key + ".json"), "wb") as f:
            f.write(body)
    return body


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    # ---------- proxys OSM ----------
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        try:
            if url.path == "/osm":
                lat, lon = float(q["lat"][0]), float(q["lon"][0])
                r = min(1200, int(q.get("r", ["500"])[0]))
                # Clef de cache arrondie : ~11 m de résolution, largement assez.
                key = f"osm_{round(lat, 4)}_{round(lon, 4)}_{r}"
                ql = (
                    f'[out:json][timeout:30];('
                    f'way["building"](around:{r},{lat},{lon});'
                    f'way["highway"](around:{r},{lat},{lon});'
                    f');out geom;'
                )
                body = fetch_json(
                    "https://overpass-api.de/api/interpreter",
                    data=urllib.parse.urlencode({"data": ql}).encode(),
                    cache_key=key,
                )
                return self._json(body)

            if url.path == "/geocode":
                text = q["q"][0]
                key = "geo_" + hashlib.sha1(text.lower().encode()).hexdigest()[:16]
                body = fetch_json(
                    "https://nominatim.openstreetmap.org/search?"
                    + urllib.parse.urlencode({"q": text, "format": "json", "limit": 5}),
                    cache_key=key,
                )
                return self._json(body)

            if url.path == "/route":
                flat, flon = q["flat"][0], q["flon"][0]
                tlat, tlon = q["tlat"][0], q["tlon"][0]
                key = f"rt_{flat}_{flon}_{tlat}_{tlon}".replace(".", "p")
                body = fetch_json(
                    f"https://router.project-osrm.org/route/v1/driving/"
                    f"{flon},{flat};{tlon},{tlat}?overview=full&geometries=geojson",
                    cache_key=key,
                )
                return self._json(body)
        except Exception as e:  # remonte l'erreur au HUD plutôt que de planter
            return self._json(json.dumps({"error": str(e)}).encode(), status=502)

        return super().do_GET()

    def _json(self, body: bytes, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- capture de debug ----------
    def do_POST(self):
        if self.path != "/capture":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length).decode("ascii", errors="ignore")
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
