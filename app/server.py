"""Server dashboard StockForge (stdlib HTTP, tanpa dependensi tambahan)."""

import json
import mimetypes
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import config as cfgmod
from app import worklog
from app.pipeline import Pipeline

CFG = cfgmod.load_config()
cfgmod.ensure_dirs(CFG)
PIPE = Pipeline(CFG)
WEB_DIR = os.path.join(cfgmod.ROOT, "web")


def _safe_join(base: str, name: str) -> str:
    path = os.path.abspath(os.path.join(base, os.path.basename(name)))
    if not path.startswith(os.path.abspath(base)):
        raise ValueError("path keluar dari folder")
    return path


THUMB_DIR = os.path.join(cfgmod.resolve(CFG["output_dir"]), "_thumbs")


def _thumbnail(src: str) -> str:
    """Thumbnail cache 480px supaya dashboard tidak menarik file 9MB."""
    os.makedirs(THUMB_DIR, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0] + ".jpg"
    dest = os.path.join(THUMB_DIR, base)
    if os.path.exists(dest) and os.path.getmtime(dest) >= os.path.getmtime(src):
        return dest
    try:
        from PIL import Image

        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((480, 480), Image.LANCZOS)
            im.save(dest, format="JPEG", quality=78, optimize=True)
        return dest
    except Exception:
        return src


class Handler(BaseHTTPRequestHandler):
    server_version = "StockForge"

    def log_message(self, fmt, *args):
        pass

    # ---------- util ----------
    def _json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _file(self, path):
        if not os.path.isfile(path):
            self._json({"error": "tidak ditemukan"}, 404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        is_web = os.path.abspath(path).startswith(os.path.abspath(WEB_DIR))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache" if is_web else "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    # ---------- routes ----------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/state":
            return self._json(PIPE.snapshot())
        if path == "/api/config":
            return self._json(
                {
                    "sheet_url": CFG.get("sheet_url", ""),
                    "profiles": [p.get("name") for p in CFG.get("profiles", [])],
                    "active_profile": CFG.get("active_profile", 0),
                    "judges": [{"id": j["id"], "label": j["label"], "model": j["model"]} for j in CFG["judges"]],
                    "vote_threshold": CFG.get("vote_threshold", 3),
                    "max_per_concept": CFG.get("max_per_concept", 3),
                }
            )
        if path == "/api/log":
            q = parse_qs(parsed.query)
            day = (q.get("day") or [""])[0]
            try:
                limit = int((q.get("limit") or ["200"])[0])
            except ValueError:
                limit = 200
            return self._json(
                {"summary": worklog.summarize(day), "entries": worklog.read(day, limit=limit)}
            )
        if path == "/api/log/download":
            day = (parse_qs(parsed.query).get("day") or [""])[0]
            target_day = day or __import__("time").strftime("%Y-%m-%d")
            src = os.path.join(worklog.LOG_DIR, f"worklog-{target_day}.jsonl")
            if not os.path.isfile(src):
                return self._json({"error": "log hari itu belum ada"}, 404)
            with open(src, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="worklog-{target_day}.jsonl"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/image":
            q = parse_qs(parsed.query)
            name = (q.get("name") or [""])[0]
            want_thumb = (q.get("thumb") or ["0"])[0] in ("1", "true")
            for base_key in ("output_dir", "shortlist_dir", "rejected_dir"):
                try:
                    candidate = _safe_join(cfgmod.resolve(CFG[base_key]), name)
                except ValueError:
                    continue
                if os.path.isfile(candidate):
                    if want_thumb:
                        return self._file(_thumbnail(candidate))
                    return self._file(candidate)
            return self._json({"error": "gambar tidak ditemukan"}, 404)

        if path in ("/", "/index.html"):
            return self._file(os.path.join(WEB_DIR, "index.html"))
        target = os.path.abspath(os.path.join(WEB_DIR, os.path.basename(path)))
        if target.startswith(WEB_DIR) and os.path.isfile(target):
            return self._file(target)
        return self._json({"error": "rute tidak ada"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()

        if path == "/api/start":
            return self._json(PIPE.start(body.get("sheet_url", "")))
        if path == "/api/pause":
            return self._json(PIPE.pause())
        if path == "/api/resume":
            return self._json(PIPE.resume())
        if path == "/api/stop":
            return self._json(PIPE.stop())
        if path == "/api/profile":
            return self._json(PIPE.switch_profile(int(body.get("index", 0))))
        if path == "/api/decide":
            return self._json(PIPE.decide(body.get("filename", ""), body.get("action", "")))
        if path == "/api/settings":
            for key in ("max_per_concept", "vote_threshold", "sheet_url"):
                if key in body:
                    CFG[key] = body[key]
            cfgmod.save_config(CFG)
            return self._json({"ok": True})
        return self._json({"error": "rute tidak ada"}, 404)


def main():
    port = int(CFG.get("port", 8770))
    ThreadingHTTPServer.allow_reuse_address = False  # Windows: cegah dua server di port sama
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        print(f"[ERROR] Port {port} sudah dipakai. Tutup StockForge lain dulu "
              f"(atau ubah \"port\" di config.json).")
        return
    url = f"http://127.0.0.1:{port}/"
    print(f"StockForge berjalan di {url}")
    if os.environ.get("STOCKFORGE_NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDihentikan.")


if __name__ == "__main__":
    main()
