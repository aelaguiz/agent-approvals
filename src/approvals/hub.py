"""The hub: one machine holds a workspace's records and its Slack connection, and serves the records to the
other machines over HTTP, so an agent on any of them can ask and have its answers typed into its session there.

Slack hands each button press to only one connected listener, so a workspace has one hub. Every other machine
posts to Slack itself and keeps the records here through these calls; its service asks every few seconds for
the answers waiting on its own sessions (`/due`).

When the hub keeps its cards in the pod dashboard's record, the record takes every answer, and the hub's service
carries each out. An answer given on another machine (the deputy's call, one an agent records) comes here (`/answers`)
and is taken and carried out by that service; who may answer comes from the record (`/who`).
"""
import hmac
import json
import logging
import re
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from . import basecamp, config, core, store

log = logging.getLogger("approvals")

RID = re.compile(r"^[A-Z]+-\d+$")
TIMEOUT = 30


class Client:
    def __init__(self):
        self.url = config.hub_url()
        self.token = config.hub_token()

    def call(self, method, path, body=None, answers=()):
        """(HTTP status, JSON body). A missing record (404), a changed one (409) and any status in answers are
        answers; a write the record refused (422) raises Refused with the record's words; anything else that isn't
        200 is an error."""
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            if e.code in (404, 409, *answers):
                return e.code, json.loads(text or "{}")
            if e.code == 422:
                raise store.Refused(json.loads(text)["refused"])
            raise store.StoreError(f"the approvals hub on {config.hub()} refused {method} {path}: {e.code} {text[:200]}")
        except (urllib.error.URLError, OSError) as e:
            raise store.StoreError(f"the approvals hub on {config.hub()} ({self.url}) isn't answering: {e}")

    def create(self, rec, media=None):
        return self.call("POST", "/records", {"record": rec, **({"media": media} if media else {})})[1]["record"]

    def read(self, rid):
        code, body = self.call("GET", f"/records/{rid}")
        if code == 404:
            raise FileNotFoundError(f"no request {rid}")
        return body["record"], body["rev"]

    def put(self, rec, rev=None, media=None):
        """True when written; False when the record changed since it was read at rev."""
        body = {"record": rec, "rev": rev, **({"media": media} if media else {})}
        return self.call("PUT", f"/records/{rec['id']}", body)[0] == 200

    def put_file(self, name, data):
        """Hand one picture or video to the hub for the record; True when the record kept it."""
        req = urllib.request.Request(f"{self.url}/files/{name}", data=data, method="PUT",
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(req, timeout=store.FILE_TIMEOUT) as r:
                return r.status == 200
        except urllib.error.HTTPError as e:
            e.close()
            return False
        except (urllib.error.URLError, OSError) as e:
            raise store.StoreError(f"the approvals hub on {config.hub()} ({self.url}) isn't answering: {e}")

    def records(self, waiting=False):
        return self.call("GET", "/records" + ("?waiting=1" if waiting else ""))[1]["records"]

    def history(self, agent, type, since, limit, calls):
        q = {k: v for k, v in (("agent", agent), ("type", type), ("since", since), ("limit", limit), ("calls", 1 if calls else None)) if v}
        code, body = self.call("GET", "/history?" + urlencode(q))
        if code != 200:
            raise store.StoreError(f"the approvals hub on {config.hub()} runs a version without history; update it first")
        return body["records"]

    def names(self, days, *, channel=None, test=False, exclude=None):
        q = {k: v for k, v in (("days", days), ("channel", channel), ("test", 1 if test else None), ("exclude", exclude)) if v is not None}
        code, body = self.call("GET", "/names?" + urlencode(q))
        if code != 200:
            raise store.StoreError(f"the approvals hub on {config.hub()} runs a version without names; update it first")
        return body["names"]

    def delete(self, rid):
        self.call("DELETE", f"/records/{rid}")

    def due(self, machine):
        body = self.call("GET", f"/due?machine={machine}")[1]
        return body["due"], body["strays"], body.get("basecamp", [])

    def reviewer(self):
        return self.call("GET", "/reviewer")[1].get("reviewer")   # a hub from before reviewers has none

    def set_reviewer(self, rv):
        if self.call("PUT", "/reviewer", {"reviewer": rv})[0] != 200:
            raise store.StoreError(f"the approvals hub on {config.hub()} runs a version without reviewers; update it first")

    def deputy(self):
        return self.call("GET", "/deputy")[1].get("deputy")   # a hub from before the deputy has none

    def set_deputy(self, d):
        if self.call("PUT", "/deputy", {"deputy": d})[0] != 200:
            raise store.StoreError(f"the approvals hub on {config.hub()} runs a version without the deputy; update it first")

    def health(self):
        return self.call("GET", "/health")[1]

    def who(self):
        """Who decides, the team and whether the team may answer any card, as the hub holds them."""
        code, body = self.call("GET", "/who")
        if code != 200:
            raise store.StoreError(f"the approvals hub on {config.hub()} runs a version without /who; update it first")
        return body

    def corrections(self, card_type):
        code, body = self.call("GET", f"/corrections/{quote(card_type, safe='')}")
        if code != 200:
            raise store.StoreError(f"the approvals hub on {config.hub()} runs a version without corrections; update it first")
        return body["corrections"]

    def give(self, answer):
        """An answer for the record to take and the hub to carry out; 'ok' once it is carried out, or Refused."""
        code, body = self.call("POST", "/answers", answer, answers=(422,))
        if code == 422:
            raise store.Refused(body["refused"])
        if code != 200:
            raise store.StoreError(f"the approvals hub on {config.hub()} carries out no answers: {body.get('error')}")
        return body["result"]

    def forks(self):
        return self.call("GET", "/forks")[1]["forks"]

    def add_fork(self, fork, same_as=None):
        return self.call("PUT", "/forks", {"fork": fork, "same_as": same_as})[1]["family"]

    def inbox_count(self, session_id):
        return self.call("GET", "/inbox?" + urlencode({"session": session_id}))[1]["count"]


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
                return self._send(200, {"machine": config.machine(), "id_prefix": config.settings()["id_prefix"],
                                        "files": bool(store.record()), "record": bool(store.record())})
            if method == "GET" and parts == ["who"]:
                held = config.settings()
                team = {key: {"user": p["user"], "name": p["name"]} for key, p in held["team"].items()}
                return self._send(200, {**{k: held[k] for k in config.LISTS}, "team": team, "team_order": list(team)})
            if method == "POST" and parts == ["answers"]:
                if not self.server.carrier:
                    return self._send(404, {"error": "this hub keeps no answers in a record"})
                try:
                    return self._send(200, {"result": self.server.carrier(self._body())})
                except store.Refused as refused:
                    return self._send(422, {"refused": str(refused)})
            if method == "PUT" and len(parts) == 2 and parts[0] == "files":
                data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                kept = store.record() and store.record().put_file(parts[1], data)
                return self._send(200, {}) if kept else self._send(502, {"error": f"the record didn't keep {parts[1]}"})
            if method == "GET" and len(parts) == 2 and parts[0] == "corrections":
                return self._send(200, {"corrections": store.local_corrections(unquote(parts[1]))})
            if method == "GET" and parts == ["due"]:
                recs, machine = store.local_all(), q.get("machine", [""])[0]
                due, strays = core.to_deliver(recs, machine, store.local_reviewer(), store.local_deputy())
                return self._send(200, {"due": due, "strays": strays, "basecamp": basecamp.due(recs, machine)})
            if method == "GET" and parts == ["history"]:
                one = lambda k: q.get(k, [None])[0]
                return self._send(200, {"records": store.local_history(one("agent"), one("type"), one("since"),
                                                                        int(one("limit") or 20), bool(one("calls")))})
            if method == "GET" and parts == ["names"]:
                one = lambda k: q.get(k, [None])[0]
                return self._send(200, {"names": store.local_names(int(one("days") or 3), channel=one("channel"),
                                                                  test=one("test") == "1", exclude=one("exclude"))})
            if parts == ["reviewer"]:
                if method == "GET":
                    return self._send(200, {"reviewer": store.local_reviewer()})
                if method == "PUT":
                    store.local_set_reviewer(self._body()["reviewer"])
                    return self._send(200, {})
            if parts == ["deputy"]:
                if method == "GET":
                    return self._send(200, {"deputy": store.local_deputy()})
                if method == "PUT":
                    store.local_set_deputy(self._body()["deputy"])
                    return self._send(200, {})
            if parts == ["forks"]:
                if method == "GET":
                    return self._send(200, {"forks": store.local_forks()})
                if method == "PUT":
                    body = self._body()
                    family = store.local_add_fork(body["fork"], body.get("same_as"))
                    return self._send(200, {"family": family})
            if method == "GET" and parts == ["inbox"]:
                return self._send(200, {"count": store.local_inbox_count(q["session"][0])})
            if parts == ["records"]:
                if method == "GET":
                    recs = store.local_all()
                    if q.get("waiting"):
                        recs = [r for r in recs if store.waiting(r)]
                    return self._send(200, {"records": recs})
                if method == "POST":
                    body = self._body()
                    return self._send(200, {"record": store.local_create(body["record"], body.get("media"))})
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
                    rev = store.local_put(body["record"], body.get("rev"), body.get("media"))
                    return self._send(200, {"rev": rev}) if rev else self._send(409, {"error": f"{rid} changed"})
                if method == "DELETE":
                    store.local_delete(rid)
                    return self._send(200, {})
            return self._send(404, {"error": f"no {method} {url.path}"})
        except store.Refused as refused:
            # The record's own words, whole: a card it refuses is refused to the agent on its own machine as it is here.
            return self._send(422, {"refused": str(refused)})
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


def serve(port=None, carrier=None):
    """Serve this workspace's records on every interface, at the port its hub_url names. carrier takes and carries
    out an answer given on another machine, when the record keeps the answers. Returns the server."""
    port = urlparse(config.hub_url()).port if port is None else port
    srv = ThreadingHTTPServer(("", port), Handler)
    srv.daemon_threads = True
    srv.token = config.hub_token()
    srv.carrier = carrier
    config.SERVING = True
    threading.Thread(target=srv.serve_forever, daemon=True, name="hub").start()
    log.info("serving records to the other machines on port %s", srv.server_address[1])
    return srv
