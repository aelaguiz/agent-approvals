"""The hub: one machine holds a workspace's records and its Slack connection, and serves the records to the
other machines over HTTP, so an agent on any of them can ask and have its answers typed into its session there.

Slack hands each button press to only one connected listener, so a workspace has one hub. Every other machine
posts to Slack itself and keeps the records here through these calls; its service asks every few seconds for
the answers waiting on its own sessions (`/due`).
"""
import hmac
import json
import logging
import re
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config, core, store

log = logging.getLogger("approvals")

RID = re.compile(r"^[A-Z]+-\d+$")
TIMEOUT = 30


class Client:
    def __init__(self):
        self.url = config.hub_url()
        self.token = config.hub_token()

    def call(self, method, path, body=None):
        """(HTTP status, JSON body). A missing record (404) and a changed one (409) are answers; anything else
        that isn't 200 is an error."""
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            if e.code in (404, 409):
                return e.code, json.loads(text or "{}")
            raise store.StoreError(f"the approvals hub on {config.hub()} refused {method} {path}: {e.code} {text[:200]}")
        except (urllib.error.URLError, OSError) as e:
            raise store.StoreError(f"the approvals hub on {config.hub()} ({self.url}) isn't answering: {e}")

    def create(self, rec):
        return self.call("POST", "/records", {"record": rec})[1]["record"]

    def read(self, rid):
        code, body = self.call("GET", f"/records/{rid}")
        if code == 404:
            raise FileNotFoundError(f"no request {rid}")
        return body["record"], body["rev"]

    def put(self, rec, rev=None):
        """True when written; False when the record changed since it was read at rev."""
        return self.call("PUT", f"/records/{rec['id']}", {"record": rec, "rev": rev})[0] == 200

    def records(self, waiting=False):
        return self.call("GET", "/records" + ("?waiting=1" if waiting else ""))[1]["records"]

    def delete(self, rid):
        self.call("DELETE", f"/records/{rid}")

    def due(self, machine):
        body = self.call("GET", f"/due?machine={machine}")[1]
        return body["due"], body["strays"]

    def health(self):
        return self.call("GET", "/health")[1]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def _handle(self, method):
        if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {self.server.token}"):
            return self._send(401, {"error": "this machine can't post as the workspace's bot"})
        url = urlparse(self.path)
        parts = url.path.strip("/").split("/")
        q = parse_qs(url.query)
        try:
            if method == "GET" and parts == ["health"]:
                return self._send(200, {"machine": config.machine(), "id_prefix": config.settings()["id_prefix"]})
            if method == "GET" and parts == ["due"]:
                due, strays = core.to_deliver(store.local_all(), q.get("machine", [""])[0])
                return self._send(200, {"due": due, "strays": strays})
            if parts == ["records"]:
                if method == "GET":
                    recs = store.local_all()
                    if q.get("waiting"):
                        recs = [r for r in recs if store.waiting(r)]
                    return self._send(200, {"records": recs})
                if method == "POST":
                    return self._send(200, {"record": store.local_create(self._body()["record"])})
            if len(parts) == 2 and parts[0] == "records" and RID.match(parts[1]):
                rid = parts[1]
                if method == "GET":
                    try:
                        rec, rev = store.local_read(rid)
                    except FileNotFoundError:
                        return self._send(404, {"error": f"no request {rid}"})
                    return self._send(200, {"record": rec, "rev": rev})
                if method == "PUT":
                    body = self._body()
                    if body["record"].get("id") != rid:
                        return self._send(400, {"error": "the record's id doesn't match the address"})
                    rev = store.local_put(body["record"], body.get("rev"))
                    return self._send(200, {"rev": rev}) if rev else self._send(409, {"error": f"{rid} changed"})
                if method == "DELETE":
                    store.local_delete(rid)
                    return self._send(200, {})
            return self._send(404, {"error": f"no {method} {url.path}"})
        except Exception as e:
            log.exception("hub %s %s failed", method, self.path)
            return self._send(500, {"error": str(e)[:300]})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def log_message(self, fmt, *args):
        log.debug("hub: " + fmt, *args)


def serve(port=None):
    """Serve this workspace's records on every interface, at the port its hub_url names. Returns the server."""
    port = urlparse(config.hub_url()).port if port is None else port
    srv = ThreadingHTTPServer(("", port), Handler)
    srv.daemon_threads = True
    srv.token = config.hub_token()
    threading.Thread(target=srv.serve_forever, daemon=True, name="hub").start()
    log.info("serving records to the other machines on port %s", srv.server_address[1])
    return srv
