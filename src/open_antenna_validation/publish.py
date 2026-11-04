"""Publication of the campaigns on the WordPress site (plugin open-antenna-validation).

The station always initiates the connection; WordPress never reaches it. The queue is the
database itself: ``publication.sent_until_ms`` only moves once the site has accepted the
windows before it, so a network outage of several days loses nothing. Retries back off
exponentially up to ``retry_max_s``.

What leaves the station: the campaign metadata (as on the local result page, locator
shortened) and the window medians per signal and channel. Never a raw measurement nor IQ.
When the site limits the package of a campaign, the published window is a multiple of the
local one, chosen once so that the whole campaign fits (median of the local medians); the
local database keeps its own window.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import logging
import math
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

import numpy as np

from open_antenna_validation import __version__
from open_antenna_validation.campaigns import CampaignNotFound, campaign_meta
from open_antenna_validation.config import Config
from open_antenna_validation.storage import Database
from open_antenna_validation.timeutil import now_ms

log = logging.getLogger(__name__)

SCHEMA = 1
API_NAMESPACE = "open-antenna-validation/v1"
TOKEN_HEADER = "X-OAV-Token"
TICK_S = 1.0
FIRST_RETRY_S = 60.0
HTTP_TOO_LARGE = 413
HTTP_INSUFFICIENT_STORAGE = 507
# Published window = local window times one of these factors.
FACTORS = (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 144, 192, 288)
PUBLISHED = ("public", "unlisted")
STATE_WORDS = {"pending": "en attente", "partial": "en cours", "complete": "publiée",
               "too_large": "trop grosse pour le site", "error": "erreur"}


class PublishError(Exception):
    """Transfer refused or failed; message in French."""


class StorageFull(PublishError):
    """The site refuses new data: its storage quota is reached (HTTP 507)."""


class PackageTooLarge(PublishError):
    """The site refuses a campaign: its package would exceed the limit (HTTP 413)."""

    def __init__(self, message: str, campaign: str | None) -> None:
        super().__init__(message)
        self.campaign = campaign


class WordPressClient:
    """POST JSON (gzip) to the plugin REST API, with both URL styles of WordPress."""

    def __init__(self, url: str, token: str, timeout_s: float) -> None:
        self._base = url.rstrip("/")
        self._token = token
        self._timeout = timeout_s
        # Sites with "plain" permalinks only answer on ?rest_route=.
        self._plain = False

    def _url(self, route: str) -> str:
        if self._plain:
            return f"{self._base}/?rest_route=/{API_NAMESPACE}/{route}"
        return f"{self._base}/wp-json/{API_NAMESPACE}/{route}"

    def post(self, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._base or not self._token:
            raise PublishError("Adresse du site ou jeton de la station non renseigné.")
        body = gzip.compress(json.dumps(payload, ensure_ascii=False,
                                        separators=(",", ":")).encode("utf-8"))
        for attempt in range(2):
            request = urllib.request.Request(
                self._url(route), data=body, method="POST",
                headers={
                    # Not application/json: WordPress would parse the compressed body itself
                    # and refuse it before the plugin sees it (D14 of openAISReceiver).
                    "Content-Type": "application/gzip",
                    "Authorization": f"Bearer {self._token}",
                    # Some shared hosts (OVH...) drop Authorization before PHP sees it.
                    TOKEN_HEADER: self._token,
                    "User-Agent": f"open-antenna-validation/{__version__}",
                })
            # Without pretty permalinks, /wp-json/ may answer 404, or 200 with an HTML page:
            # either way, retry once with ?rest_route= and keep that style.
            retry_plain = attempt == 0 and not self._plain
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    text = response.read().decode("utf-8", errors="replace")
                    answer = json.loads(text) if text else {}
                    if not isinstance(answer, dict):
                        raise json.JSONDecodeError("objet JSON attendu", text, 0)
                    return answer
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and retry_plain:
                    self._plain = True
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:4000]
                data: dict[str, Any] = {}
                with contextlib.suppress(ValueError, AttributeError):
                    data = json.loads(detail)
                message = str(data.get("message") or detail[:300])
                if exc.code == HTTP_INSUFFICIENT_STORAGE:
                    raise StorageFull("Stockage du site plein : envois suspendus, "
                                      "la station garde ses données.") from exc
                if exc.code == HTTP_TOO_LARGE:
                    raise PackageTooLarge(message, (data.get("data") or {}).get("campaign")
                                          ) from exc
                if exc.code in (401, 403):
                    raise PublishError(f"Jeton refusé par le site : {message}") from exc
                raise PublishError(f"Le site a répondu {exc.code} : {message}") from exc
            except json.JSONDecodeError as exc:
                if retry_plain:
                    self._plain = True
                    continue
                raise PublishError("Réponse du site illisible (le plugin est-il actif ?)."
                                   ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise PublishError(f"Site injoignable : {exc}") from exc
        raise PublishError("Route du plugin introuvable (le plugin est-il actif ?).")


def choose_window(local_ms: int, duration_ms: int, n_rows_per_window: int, meta_bytes: int,
                  limits: dict[str, Any] | None, fill: float) -> int:
    """Smallest published window (multiple of the local one) whose whole campaign fits in
    the site's package limit. Never refuses: the widest factor is the last resort."""
    limit = int((limits or {}).get("package_max_bytes") or 0)
    if limit <= 0:
        return local_ms
    row_bytes = int((limits or {}).get("row_bytes") or 40)
    for k in FACTORS:
        window = local_ms * k
        rows = math.ceil(duration_ms / window) * n_rows_per_window
        if meta_bytes + rows * row_bytes <= fill * limit:
            return window
    return local_ms * FACTORS[-1]


def package_bytes(meta_bytes: int, rows: int, limits: dict[str, Any] | None) -> int:
    return meta_bytes + rows * int((limits or {}).get("row_bytes") or 40)


def _meta_json(meta: dict[str, Any]) -> str:
    return json.dumps(meta, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class Publisher:
    def __init__(self, db: Database, config: Config,
                 status: Callable[[], dict[str, Any]]) -> None:
        self._db = db
        self._config = config
        self._status = status
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._client_key: tuple[str, str] | None = None
        self._client: WordPressClient | None = None
        self._next = 0.0
        self._retry_s = FIRST_RETRY_S
        self.last_success_ms: int | None = None
        self.last_error: str | None = None
        self.suspended = False
        self.limits: dict[str, Any] | None = None

    # --- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="wordpress", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def trigger(self) -> None:
        """Send now (campaign finished, visibility changed, settings saved)."""
        self._next = 0.0
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(TICK_S)
            self._wake.clear()
            if self._stop.is_set():
                return
            if self._config.wordpress.enabled and time.monotonic() >= self._next:
                self.cycle()

    def client(self) -> WordPressClient:
        wp = self._config.wordpress
        key = (wp.url, wp.token)
        if self._client is None or self._client_key != key:
            self._client, self._client_key = WordPressClient(wp.url, wp.token, wp.timeout_s), key
        return self._client

    # --- one cycle ----------------------------------------------------------------------

    def cycle(self) -> None:
        wp = self._config.wordpress
        now = time.monotonic()
        try:
            self.publish_pending()
        except StorageFull as exc:
            with self._lock:
                if not self.suspended:
                    self._db.log_event("wordpress", {"message": str(exc)})
                self.suspended = True
                self.last_error = str(exc)
                self._next = now + wp.retry_max_s
            log.warning("Publication suspendue : stockage du site plein")
        except PublishError as exc:
            with self._lock:
                if self.last_error != str(exc):
                    self._db.log_event("wordpress", {"message": str(exc)})
                self.last_error = str(exc)
                self._next = now + self._retry_s
                self._retry_s = min(self._retry_s * 2, wp.retry_max_s)
            log.warning("Publication : %s (nouvel essai dans %.0f s)", exc, self._next - now)
        except Exception:
            log.exception("Publication en échec")
            self._next = now + self._retry_s
        else:
            with self._lock:
                self.suspended = False
                self.last_error = None
                self.last_success_ms = now_ms()
                self._retry_s = FIRST_RETRY_S
                self._next = now + wp.interval_s

    def test(self, url: str, token: str) -> dict[str, Any]:
        """Limits of a site not saved yet (link test from the settings form)."""
        wp = self._config.wordpress
        client = WordPressClient(url.strip().rstrip("/") or wp.url, token.strip() or wp.token,
                                 wp.timeout_s)
        return client.post("limits", {"schema": SCHEMA, "station_id": self._config.station.id})

    def fetch_limits(self) -> dict[str, Any]:
        answer = self.client().post("limits", {"schema": SCHEMA,
                                               "station_id": self._config.station.id})
        self.limits = answer
        return answer

    def _station(self) -> dict[str, Any]:
        s, wp = self._config.station, self._config.wordpress
        status = self._status()
        return {"id": s.id, "name": s.name, "callsign": s.callsign,
                "locator": s.locator[:wp.locator_chars] if wp.locator_chars else "",
                "software": f"open-antenna-validation {__version__}",
                "status": {"ts_ms": now_ms(), **status, "queue_rows": self.queue_rows()}}

    def publish_pending(self) -> None:
        """Send everything the site has not accepted yet; at least the station state."""
        limits = self.fetch_limits()
        if limits.get("full"):
            raise StorageFull("Stockage du site plein : envois suspendus, "
                              "la station garde ses données.")
        for _ in range(1000):  # bounded: one request per batch
            batch = self._batch(limits)
            answer = self._send(batch)
            self._accept(batch, answer)
            if not batch["full"]:
                return  # the rest waits for its windows to close

    def _send(self, batch: dict[str, Any]) -> dict[str, Any]:
        payload = {"schema": SCHEMA, "station": self._station(),
                   "campaigns": [{k: v for k, v in c.items() if not k.startswith("_")}
                                 for c in batch["campaigns"]]}
        try:
            return self.client().post("ingest", payload)
        except PackageTooLarge as exc:
            uuid = exc.campaign or (batch["campaigns"][0]["uuid"] if batch["campaigns"] else None)
            if uuid:
                self._set_state(uuid, "too_large", str(exc))
                self._db.log_event("wordpress", {"campaign": uuid, "message": str(exc)})
            raise

    # --- what to send --------------------------------------------------------------------

    def _candidates(self) -> list[Any]:
        return self._db.query(
            "SELECT c.id, c.uuid, c.state, c.visibility, c.start_ms, c.planned_end_ms,"
            " c.n_channels, c.params_json, p.window_ms, p.sent_until_ms, p.meta_hash, p.state"
            " AS pstate FROM campaigns c LEFT JOIN publication p ON p.campaign_id = c.id"
            " WHERE c.visibility IN ('public', 'unlisted') AND c.state != 'preparation'"
            " ORDER BY c.start_ms")

    def queue_rows(self) -> int:
        total = 0
        for c in self._candidates():
            if json.loads(c["params_json"] or "{}").get("demo") or c["pstate"] in (
                    "complete", "too_large"):
                continue
            total += int(self._db.query(
                "SELECT COUNT(*) FROM aggregates WHERE campaign_id = ? AND window_start_ms >= ?",
                (c["id"], c["sent_until_ms"] or 0))[0][0])
        return total

    def _batch(self, limits: dict[str, Any]) -> dict[str, Any]:
        wp = self._config.wordpress
        budget = wp.batch_rows
        out: list[dict[str, Any]] = []
        for c in self._candidates():
            params = json.loads(c["params_json"] or "{}")
            if params.get("demo") or c["pstate"] == "too_large" or len(out) >= 20:
                continue
            try:
                meta = campaign_meta(self._db, c["uuid"], locator_chars=wp.locator_chars)
            except CampaignNotFound:
                continue
            text = _meta_json(meta)
            digest = hashlib.sha256((c["visibility"] + text).encode()).hexdigest()[:32]
            if c["pstate"] == "complete" and digest == c["meta_hash"]:
                continue
            local_ms = int(params.get("window_ms") or 60_000)
            window = c["window_ms"]
            if window is None:
                duration = int((c["planned_end_ms"] or 0) - (c["start_ms"] or 0))
                per_window = len(meta["signals"]) * int(c["n_channels"])
                window = choose_window(local_ms, duration, per_window, len(text.encode()),
                                       limits, wp.package_fill)
                self._ensure_row(int(c["id"]), window)
            rows, until, complete = self._rows(int(c["id"]), c["state"], local_ms, int(window),
                                               int(c["sent_until_ms"] or 0), budget)
            budget -= len(rows)
            entry: dict[str, Any] = {"uuid": c["uuid"], "visibility": c["visibility"],
                                     "step_ms": int(window), "rows": rows,
                                     "complete": complete, "_until": until, "_hash": digest,
                                     "_cid": int(c["id"])}
            if digest != c["meta_hash"]:
                entry["meta"] = meta
            out.append(entry)
            if budget <= 0:
                break
        return {"campaigns": out, "full": budget <= 0}

    def _rows(self, cid: int, state: str, local_ms: int, window: int, since: int,
              budget: int) -> tuple[list[list[Any]], int, bool]:
        """Published windows not sent yet, complete ones only while the campaign runs."""
        last = self._db.query("SELECT MAX(window_start_ms) FROM aggregates WHERE campaign_id = ?",
                              (cid,))[0][0]
        if last is None:
            return [], since, state in ("done", "abandoned")
        ended = state in ("done", "abandoned")
        closed_until = int(last) + local_ms  # every local window before this is closed
        until = (closed_until - closed_until % window) if not ended else closed_until + window
        until -= until % window
        if until <= since:
            return [], since, ended and since >= closed_until
        agg = self._db.query(
            "SELECT signal_id, channel_idx, window_start_ms, rssi_med, floor_med FROM aggregates"
            " WHERE campaign_id = ? AND window_start_ms >= ? AND window_start_ms < ?"
            " ORDER BY window_start_ms", (cid, since, until))
        groups: dict[tuple[int, int, int], tuple[list[float], list[float]]] = {}
        for a in agg:
            t = int(a["window_start_ms"]) - int(a["window_start_ms"]) % window
            r, f = groups.setdefault((t, int(a["signal_id"]), int(a["channel_idx"])), ([], []))
            if a["rssi_med"] is not None:
                r.append(float(a["rssi_med"]))
            if a["floor_med"] is not None:
                f.append(float(a["floor_med"]))
        keys = sorted(groups)
        if len(keys) > budget:
            # Cut on a window boundary so that a window is never sent in two parts.
            cut = keys[budget][0]
            keys = [k for k in keys if k[0] < cut]
            until = cut
            if not keys:
                return [], since, False
        rows = [[sid, ch, t, _med(groups[(t, sid, ch)][0]), _med(groups[(t, sid, ch)][1])]
                for t, sid, ch in keys]
        return rows, until, ended and until >= closed_until

    def _ensure_row(self, cid: int, window: int) -> None:
        with self._db.transaction() as conn:
            conn.execute("INSERT INTO publication (campaign_id, window_ms, sent_until_ms)"
                         " VALUES (?, ?, 0) ON CONFLICT (campaign_id) DO UPDATE"
                         " SET window_ms = excluded.window_ms", (cid, window))

    def _accept(self, batch: dict[str, Any], answer: dict[str, Any]) -> None:
        accepted = answer.get("campaigns") or {}
        with self._db.transaction() as conn:
            for c in batch["campaigns"]:
                info = accepted.get(c["uuid"]) or {}
                state = "complete" if c["complete"] else "partial"
                conn.execute(
                    "UPDATE publication SET sent_until_ms = ?, meta_hash = ?,"
                    " rows_sent = rows_sent + ?, bytes = COALESCE(?, bytes), state = ?,"
                    " message = '', last_ms = ? WHERE campaign_id = ?",
                    (c["_until"], c["_hash"], len(c["rows"]), info.get("bytes"), state,
                     now_ms(), c["_cid"]))

    def _set_state(self, uuid: str, state: str, message: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("UPDATE publication SET state = ?, message = ?, last_ms = ?"
                         " WHERE campaign_id = (SELECT id FROM campaigns WHERE uuid = ?)",
                         (state, message, now_ms(), uuid))

    # --- reports -------------------------------------------------------------------------

    def estimate(self, duration_s: float, n_signals: int, n_channels: int,
                 local_window_s: float) -> dict[str, Any]:
        """Published resolution of a campaign about to start (wizard)."""
        if not self._config.wordpress.enabled:
            return {"enabled": False}
        limits = self.limits
        if limits is None:
            try:
                limits = self.fetch_limits()
            except PublishError as exc:
                return {"enabled": True, "error": str(exc)}
        meta_bytes = 3000 + 400 * n_channels + 120 * n_signals
        local_ms = int(local_window_s * 1000)
        window = choose_window(local_ms, int(duration_s * 1000), n_signals * n_channels,
                               meta_bytes, limits, self._config.wordpress.package_fill)
        rows = math.ceil(duration_s * 1000 / window) * n_signals * n_channels
        return {"enabled": True, "window_ms": window, "local_window_ms": local_ms,
                "bytes": package_bytes(meta_bytes, rows, limits),
                "limit_bytes": int(limits.get("package_max_bytes") or 0),
                "full": bool(limits.get("full"))}

    def report(self) -> dict[str, Any]:
        wp = self._config.wordpress
        return {"enabled": wp.enabled, "url": wp.url, "token_set": bool(wp.token),
                "last_success_ms": self.last_success_ms, "error": self.last_error,
                "suspended": self.suspended, "limits": self.limits}

    def campaign_states(self) -> dict[str, dict[str, Any]]:
        return {r["uuid"]: {"state": r["state"], "word": STATE_WORDS.get(r["state"], r["state"]),
                            "window_ms": r["window_ms"], "bytes": r["bytes"],
                            "message": r["message"], "last_ms": r["last_ms"]}
                for r in self._db.query(
                    "SELECT c.uuid, p.* FROM publication p JOIN campaigns c"
                    " ON c.id = p.campaign_id")}


def _med(values: list[float]) -> float | None:
    return round(float(np.median(values)), 2) if values else None
