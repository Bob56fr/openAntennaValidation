"""Stand-in for the WordPress plugin: implements the station-side contract.

Routes /wp-json/open-antenna-validation/v1/{limits,ingest} (and ?rest_route=...), token in
Authorization: Bearer or X-OAV-Token, gzip bodies (application/gzip), JSON Schema validation
(docs/wordpress/*.schema.json), idempotent windows, package limit (413), quota (507).

By hand: ``python tests/fake_wordpress.py --port 8088 --token secret``.
"""

from __future__ import annotations

import argparse
import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import jsonschema

SCHEMAS = Path(__file__).resolve().parents[1] / "docs" / "wordpress"
PREFIX = "/open-antenna-validation/v1/"
GZIP_MAGIC = bytes((0x1F, 0x8B))
ROW_BYTES = 40


class FakeWordPress:
    def __init__(self, token: str, plain_permalinks: bool = False,
                 package_max_bytes: int = 0) -> None:
        self.token = token
        self.plain_permalinks = plain_permalinks
        self.package_max_bytes = package_max_bytes
        self.storage_full = False
        self.fail_next = 0
        self.payloads: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.station: dict[str, Any] | None = None
        # uuid -> {"meta", "visibility", "step_ms", "windows": {(sig, ch, t): (rssi, floor)}}
        self.campaigns: dict[str, dict[str, Any]] = {}
        self._schemas = {name: json.loads((SCHEMAS / f"{name}.schema.json")
                                          .read_text(encoding="utf-8"))
                         for name in ("ingest", "limits")}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> FakeWordPress:
        self._thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def package_bytes(self, uuid: str) -> int:
        c = self.campaigns[uuid]
        meta = json.dumps(c["meta"], ensure_ascii=False, separators=(",", ":")) \
            if c["meta"] else ""
        return len(meta.encode()) + len(c["windows"]) * ROW_BYTES

    def limits(self) -> dict[str, Any]:
        answer = {"schema": 1, "package_max_bytes": self.package_max_bytes,
                  "row_bytes": ROW_BYTES, "quota_bytes": 200 << 20, "used_bytes": 1 << 20,
                  "full": self.storage_full, "operator": "Opérateur d'essai",
                  "station": "Station d'essai"}
        jsonschema.validate(answer, self._schemas["limits"])
        return answer

    def handle(self, route: str, headers: Any, body: bytes) -> tuple[int, dict[str, Any]]:
        if self.fail_next > 0:
            self.fail_next -= 1
            return 503, {"code": "unavailable", "message": "Service indisponible"}
        auth = headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else headers.get("X-OAV-Token", "")
        if token != self.token:
            return 401, {"code": "oav_token", "message": "Jeton inconnu"}
        if headers.get("Content-Type", "").startswith("application/json"):
            return 400, {"code": "rest_invalid_json", "message": "Contenu JSON non valide."}
        if body[:2] == GZIP_MAGIC:
            body = gzip.decompress(body)
        payload = json.loads(body or b"{}")
        if route == "limits":
            return 200, self.limits()
        if route != "ingest":
            return 404, {"code": "rest_no_route"}
        if self.storage_full:
            return 507, {"code": "oav_quota", "message": "Quota atteint",
                         "data": {"status": 507}}
        try:
            jsonschema.validate(payload, self._schemas["ingest"])
        except jsonschema.ValidationError as exc:
            self.errors.append(exc.message)
            return 400, {"code": "invalid", "message": exc.message}
        self.payloads.append(payload)
        self.station = payload["station"]
        out = {}
        for c in payload["campaigns"]:
            stored = self.campaigns.setdefault(c["uuid"], {"meta": None, "windows": {}})
            before = (stored["meta"], dict(stored["windows"]))
            if "meta" in c:
                stored["meta"] = c["meta"]
            stored.update(visibility=c["visibility"], step_ms=c["step_ms"],
                          complete=c["complete"])
            for sig, ch, t, rssi, floor in c["rows"]:
                stored["windows"][(sig, ch, t)] = (rssi, floor)
            size = self.package_bytes(c["uuid"])
            if self.package_max_bytes and size > self.package_max_bytes:
                stored["meta"], stored["windows"] = before
                return 413, {"code": "oav_package", "message": "Paquet de campagne trop gros",
                             "data": {"status": 413, "campaign": c["uuid"],
                                      "limit_bytes": self.package_max_bytes}}
            out[c["uuid"]] = {"rows": len(stored["windows"]), "bytes": size}
        return 200, {"ok": True, "campaigns": out, "limits": self.limits()}

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                url = urlparse(self.path)
                route = ""
                if url.path.startswith("/wp-json" + PREFIX) and not fake.plain_permalinks:
                    route = url.path[len("/wp-json" + PREFIX):]
                rest = parse_qs(url.query).get("rest_route", [""])[0]
                if rest.startswith(PREFIX):
                    route = rest[len(PREFIX):]
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if not route and url.path.startswith("/wp-json"):
                    # Plain permalinks: some sites answer /wp-json/ with their home page.
                    page = b"<!doctype html><html><body>Accueil</body></html>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)
                    return
                status, answer = fake.handle(route, self.headers, body) if route \
                    else (404, {"code": "rest_no_route"})
                data = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--token", default="secret")
    args = parser.parse_args()
    fake = FakeWordPress(args.token)
    fake.server.server_close()
    fake.server = ThreadingHTTPServer(("127.0.0.1", args.port), fake._handler())
    print(f"Faux WordPress sur http://127.0.0.1:{args.port}/ (jeton {args.token})")
    fake.server.serve_forever()
