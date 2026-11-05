#!/usr/bin/env python3
"""LibreTranslate-compatible front for Google Cloud Translation (v2, the standard NMT model).

Speaks just enough of LibreTranslate's API - POST /translate and GET /languages - for anything
written against LibreTranslate to work unchanged, and forwards each request to Google. It keeps no
state and no cache of its own: nginx in front of it caches every successful answer (nginx.conf).

Standard library only, so the image needs nothing beyond python3.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GOOGLE_URL = "https://translation.googleapis.com/language/translate/v2"
API_KEY = os.environ.get("GOOGLE_API_KEY", "").strip()
TIMEOUT = float(os.environ.get("GOOGLE_TIMEOUT", "20"))


class UpstreamError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def google(path, body=None, params=None):
    query = {"key": API_KEY, **(params or {})}
    url = f"{GOOGLE_URL}{path}?{urllib.parse.urlencode(query)}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)["data"]
    except urllib.error.HTTPError as e:
        try:
            message = json.load(e)["error"]["message"]
        except Exception:
            message = e.reason
        raise UpstreamError(e.code, f"Google Translate: {message}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise UpstreamError(502, f"Google Translate unreachable: {e}")


def translate(payload):
    q = payload.get("q")
    target = payload.get("target")
    if not q or not target:
        raise UpstreamError(400, "Invalid request: q and target are required")
    source = payload.get("source") or "auto"
    # Google defaults to html, which escapes entities in plain text; LibreTranslate defaults to text.
    fmt = "html" if payload.get("format") == "html" else "text"

    body = {"q": q if isinstance(q, list) else [q], "target": target, "format": fmt}
    if source != "auto":
        body["source"] = source
    translations = google("", body=body)["translations"]

    def detected(t):
        return {"confidence": 100, "language": t.get("detectedSourceLanguage", "")}

    if isinstance(q, list):
        result = {"translatedText": [t["translatedText"] for t in translations]}
        if source == "auto":
            result["detectedLanguage"] = [detected(t) for t in translations]
    else:
        result = {"translatedText": translations[0]["translatedText"]}
        if source == "auto":
            result["detectedLanguage"] = detected(translations[0])
    return result


def languages():
    langs = google("/languages", params={"target": "en"})["languages"]
    codes = {l["language"] for l in langs}
    # Google lists Hebrew twice, as "he" and the legacy "iw"; LibreTranslate clients expect "he".
    langs = [l for l in langs if not (l["language"] == "iw" and "he" in codes)]
    targets = [l["language"] for l in langs]
    return [{"code": l["language"], "name": l["name"], "targets": targets} for l in langs]


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_errors(self, fn):
        try:
            self.send_json(200, fn())
        except UpstreamError as e:
            self.send_json(e.status, {"error": e.message})
        except Exception as e:
            self.send_json(500, {"error": f"Internal error: {e}"})

    def path_only(self):
        return urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"

    def do_GET(self):
        path = self.path_only()
        if path == "/health":
            self.send_json(200, {"status": "ok"})
        elif path == "/languages":
            self.handle_errors(languages)
        else:
            self.send_json(404, {"error": "Not found"})

    def do_HEAD(self):
        # Health checkers such as wait-on probe with HEAD.
        self.send_response(200 if self.path_only() == "/health" else 404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        path = self.path_only()
        if path != "/translate":
            self.send_json(404, {"error": "Not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            if "application/json" in (self.headers.get("Content-Type") or ""):
                payload = json.loads(raw or b"{}")
            else:
                # LibreTranslate also accepts form posts.
                payload = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode()).items()}
        except (ValueError, UnicodeDecodeError):
            self.send_json(400, {"error": "Invalid request: body is not valid JSON"})
            return
        self.handle_errors(lambda: translate(payload))

    def log_request(self, code="-", size="-"):
        # nginx logs every request already; only failures are worth repeating here.
        if isinstance(code, int) and code >= 400:
            super().log_request(code, size)

    def log_message(self, fmt, *args):
        sys.stderr.write("adapter: " + (fmt % args) + "\n")


def main():
    if not API_KEY:
        sys.exit("adapter: GOOGLE_API_KEY is not set")
    host = os.environ.get("ADAPTER_HOST", "127.0.0.1")
    port = int(os.environ.get("ADAPTER_PORT", "5051"))
    server = ThreadingHTTPServer((host, port), Handler)
    sys.stderr.write(f"adapter: listening on {host}:{port}\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
