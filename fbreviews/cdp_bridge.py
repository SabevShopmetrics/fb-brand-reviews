"""CDP bridge between browser-use 1.0 and OpenWork's built-in browser.

OpenWork is an Electron app started with --remote-debugging-port. Its CDP endpoint shows two kinds of pages:
  * the OpenWork UI itself (default browser context, file:///Applications/OpenWork.app/...)
  * the tabs of the built-in browser (a separate context: the "openwork-browser" partition)

browser-use 1.0 (through its browser-harness daemon) always opens a tab of its own with Target.createTarget, which
Electron answers with "Not supported", and closes that tab again on shutdown. The bridge sits in between and:
  * answers Target.createTarget with the built-in browser's tab (the "lease" tab) instead of a new one
  * never lets anything close the lease tab, tabs that were open before the run, or the OpenWork UI (fake success)
  * hides the OpenWork UI pages from the agent entirely (Target.getTargets, target events, attach)
  * fakes Browser.close and a failing Browser.setDownloadBehavior
Everything else is forwarded unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import urllib.request
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

import websockets
from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

logger = logging.getLogger("fbreviews.bridge")

APP_URL_PREFIXES = ("file:///Applications/OpenWork.app", "devtools://", "chrome-extension://")
TARGET_EVENTS = {"Target.targetCreated", "Target.targetInfoChanged", "Target.attachedToTarget"}


class BridgeError(RuntimeError):
    pass


def _http_json(url: str, timeout: float = 5.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # local CDP endpoint only
        return json.loads(r.read().decode())


@dataclass
class TargetPolicy:
    """Which targets the agent may see / close. Pure logic, unit-tested."""

    default_context: str | None
    lease_target: str | None = None
    protected: set[str] = field(default_factory=set)  # open before the run: never closed
    hidden: set[str] = field(default_factory=set)

    def is_hidden_info(self, info: dict) -> bool:
        tid = info.get("targetId")
        if tid in self.hidden:
            return True
        url = info.get("url", "") or ""
        ctx = info.get("browserContextId")
        hide = url.startswith(APP_URL_PREFIXES) or (
            self.default_context is not None and ctx == self.default_context and info.get("type") in ("page", "iframe", "webview", "other")
        )
        if hide and tid:
            self.hidden.add(tid)
        return hide

    def filter_infos(self, infos: list[dict]) -> list[dict]:
        return [t for t in infos if not self.is_hidden_info(t)]

    def may_close(self, target_id: str | None) -> bool:
        return bool(target_id) and target_id != self.lease_target and target_id not in self.protected and target_id not in self.hidden

    def pick_lease(self, infos: list[dict], match: str | None) -> dict | None:
        pages = [t for t in self.filter_infos(infos) if t.get("type") == "page"]
        if not pages:
            return None
        if match:
            hit = [t for t in pages if match.lower() in (t.get("url") or "").lower()]
            if hit:
                return hit[0]
        fb = [t for t in pages if "facebook.com" in (t.get("url") or "")]
        return (fb or pages)[0]


class OpenWorkBridge:
    def __init__(self, upstream_http: str, tab_match: str | None = None, host: str = "127.0.0.1", port: int = 0):
        self.upstream_http = upstream_http.rstrip("/")
        self.tab_match = tab_match
        self.host, self.port = host, port
        self.policy: TargetPolicy | None = None
        self.lease_info: dict | None = None
        self._server = None
        self._browser_path = "/devtools/browser/bridge"
        self._upstream_ws_base = ""

    # ---------- lifecycle ----------
    async def start(self) -> str:
        try:
            version = await asyncio.to_thread(_http_json, f"{self.upstream_http}/json/version")
        except Exception as e:
            raise BridgeError(
                f"OpenWork CDP не отговаря на {self.upstream_http} ({e}). Пуснат ли е OpenWork? "
                "Задайте OPENWORK_CDP_URL ако портът е друг."
            ) from None
        upstream_ws = version["webSocketDebuggerUrl"]
        self._upstream_ws_base = upstream_ws.split("/devtools/")[0]
        self._browser_path = "/devtools/" + upstream_ws.split("/devtools/", 1)[1]
        contexts, infos = await self._probe(upstream_ws)
        self.policy = TargetPolicy(default_context=contexts.get("defaultBrowserContextId"))
        lease = self.policy.pick_lease(infos, self.tab_match)
        if lease is None:
            raise BridgeError(
                "Във вградения браузър на OpenWork няма отворен таб. Отворете браузъра в OpenWork, "
                "влезте във Facebook и пуснете отново."
            )
        self.lease_info = lease
        self.policy.lease_target = lease["targetId"]
        visible = {t["targetId"] for t in self.policy.filter_infos(infos)}
        self.policy.protected = set(visible)
        # The agent works in the lease tab only: the user's other open tabs are left alone and out of sight.
        self.policy.hidden |= visible - {lease["targetId"]}
        self._server = await serve(
            self._handle, self.host, self.port, process_request=self._process_http, max_size=None, ping_interval=None
        )
        self.port = self._server.sockets[0].getsockname()[1]
        self._awake_task = asyncio.create_task(self._keep_awake())
        logger.info("bridge on %s -> %s, lease tab %s (%s)", self.url, self.upstream_http, lease["targetId"][:8], lease.get("url"))
        return self.url

    async def _keep_awake(self, every: float = 2.0) -> None:
        """OpenWork's browser panel is often not on screen; Chromium then marks the page hidden and stops rendering,
        and Facebook fills its lazy review list only for a visible page. Keep the lease tab "in front" and focused."""
        assert self.policy is not None
        url = f"{self._upstream_ws_base}/devtools/page/{self.policy.lease_target}"
        while True:
            try:
                async with ws_connect(url, max_size=None, ping_interval=None) as ws:
                    await ws.send(json.dumps({"id": 1, "method": "Emulation.setFocusEmulationEnabled", "params": {"enabled": True}}))
                    i = 1
                    while True:
                        i += 1
                        await ws.send(json.dumps({"id": i, "method": "Page.bringToFront", "params": {}}))
                        # drain replies/events so the socket never backs up
                        with contextlib.suppress(asyncio.TimeoutError):
                            while True:
                                await asyncio.wait_for(ws.recv(), timeout=every)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # tab reloading, DevTools busy: retry
                logger.debug("keep-awake reconnect: %s", e)
                await asyncio.sleep(every)

    async def stop(self) -> None:
        task = getattr(self, "_awake_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            self._awake_task = None
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> "OpenWorkBridge":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def _probe(self, upstream_ws: str) -> tuple[dict, list[dict]]:
        async with ws_connect(upstream_ws, max_size=None, ping_interval=None) as ws:
            async def call(i: int, method: str) -> dict:
                await ws.send(json.dumps({"id": i, "method": method, "params": {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == i:
                        return msg.get("result") or {}

            return await call(1, "Target.getBrowserContexts"), (await call(2, "Target.getTargets")).get("targetInfos", [])

    # ---------- HTTP (/json/*) ----------
    def _process_http(self, connection: ServerConnection, request: Request) -> Response | None:
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None
        path = request.path.split("?")[0].rstrip("/")
        ws_browser = f"ws://{self.host}:{self.port}{self._browser_path}"
        try:
            if path == "/json/version":
                data = _http_json(f"{self.upstream_http}/json/version")
                data["webSocketDebuggerUrl"] = ws_browser
            elif path in ("/json", "/json/list"):
                data = [self._rewrite_page(t) for t in _http_json(f"{self.upstream_http}/json/list") if not self._hidden_listing(t)]
            elif path == "/json/new":
                data = self._rewrite_page({**(self.lease_info or {}), "type": "page"})
            else:
                return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        except Exception as e:
            return connection.respond(HTTPStatus.BAD_GATEWAY, f"{e}\n")
        body = json.dumps(data).encode()
        headers = Headers([("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        return Response(200, "OK", headers, body)

    def _hidden_listing(self, t: dict) -> bool:
        assert self.policy is not None
        return t.get("id") in self.policy.hidden or (t.get("url") or "").startswith(APP_URL_PREFIXES)

    def _rewrite_page(self, t: dict) -> dict:
        tid = t.get("id") or t.get("targetId")
        if tid:
            t["webSocketDebuggerUrl"] = f"ws://{self.host}:{self.port}/devtools/page/{tid}"
        return t

    # ---------- WebSocket proxy ----------
    async def _handle(self, client: ServerConnection) -> None:
        assert self.policy is not None
        path = client.request.path if client.request else self._browser_path
        if path.startswith("/devtools/page/") and path.rsplit("/", 1)[-1] in self.policy.hidden:
            await client.close(1008, "hidden by OpenWork bridge")
            return
        upstream_url = self._upstream_ws_base + path
        pending: dict[int, dict] = {}
        try:
            async with ws_connect(upstream_url, max_size=None, ping_interval=None) as upstream:
                t1 = asyncio.create_task(self._client_to_upstream(client, upstream, pending))
                t2 = asyncio.create_task(self._upstream_to_client(client, upstream, pending))
                done, rest = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
                for t in rest:
                    t.cancel()
                for t in (*done, *rest):  # retrieve every outcome: a closed socket is a normal end
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await t
        except (OSError, websockets.exceptions.WebSocketException) as e:
            logger.debug("bridge connection ended: %s", e)

    async def _reply(self, client: ServerConnection, msg: dict, result: dict | None = None, error: str | None = None) -> None:
        out: dict[str, Any] = {"id": msg["id"]}
        if msg.get("sessionId"):
            out["sessionId"] = msg["sessionId"]
        if error:
            out["error"] = {"code": -32000, "message": error}
        else:
            out["result"] = result or {}
        with contextlib.suppress(websockets.exceptions.ConnectionClosed):
            await client.send(json.dumps(out))

    async def _client_to_upstream(self, client: ServerConnection, upstream, pending: dict[int, dict]) -> None:
        policy = self.policy
        assert policy is not None
        async for raw in client:
            msg = json.loads(raw)
            method, params = msg.get("method", ""), msg.get("params") or {}
            if "id" in msg and method:
                tid = params.get("targetId")
                if method == "Target.createTarget":
                    await self._reply(client, msg, {"targetId": policy.lease_target})
                    url = params.get("url") or "about:blank"
                    if url != "about:blank" and not url.startswith("about:blank#"):
                        asyncio.create_task(self._navigate_lease(url))
                    continue
                if method == "Target.closeTarget" and not policy.may_close(tid):
                    await self._reply(client, msg, {"success": True})
                    continue
                if method == "Browser.close":
                    await self._reply(client, msg, {})
                    continue
                if method in ("Target.attachToTarget", "Target.activateTarget", "Target.getTargetInfo") and tid in policy.hidden:
                    await self._reply(client, msg, error="Target is hidden by the OpenWork bridge")
                    continue
                pending[msg["id"]] = {"method": method}
            await upstream.send(raw)

    async def _upstream_to_client(self, client: ServerConnection, upstream, pending: dict[int, dict]) -> None:
        policy = self.policy
        assert policy is not None
        async for raw in upstream:
            msg = json.loads(raw)
            if "id" in msg:
                req = pending.pop(msg["id"], None)
                if req is not None:
                    m = req["method"]
                    if m == "Target.getTargets" and "result" in msg:
                        msg["result"]["targetInfos"] = policy.filter_infos(msg["result"].get("targetInfos", []))
                        raw = json.dumps(msg)
                    elif m == "Browser.setDownloadBehavior" and "error" in msg:
                        msg.pop("error")
                        msg["result"] = {}
                        raw = json.dumps(msg)
            else:
                method = msg.get("method", "")
                params = msg.get("params") or {}
                if method in TARGET_EVENTS and policy.is_hidden_info(params.get("targetInfo") or {}):
                    continue
                if method == "Target.targetDestroyed" and params.get("targetId") in policy.hidden:
                    continue
            await client.send(raw if isinstance(raw, str) else raw.decode())

    async def navigate_lease(self, url: str) -> None:
        await self._navigate_lease(url)

    async def _navigate_lease(self, url: str) -> None:
        """Target.createTarget(url=...) asked for a page: show it in the lease tab instead."""
        assert self.policy is not None
        try:
            async with ws_connect(
                f"{self._upstream_ws_base}/devtools/page/{self.policy.lease_target}", max_size=None, ping_interval=None
            ) as ws:
                await ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
                await asyncio.wait_for(ws.recv(), 10)
        except Exception as e:
            logger.warning("bridge: could not navigate the lease tab to %s: %s", url, e)


class BridgeThread:
    """Runs OpenWorkBridge on its own event loop in a background thread.

    browser-use makes some blocking calls to its daemon from the caller's event loop; a bridge on that same loop
    could not answer them (deadlock). In its own thread it always can."""

    def __init__(self, upstream_http: str, tab_match: str | None = None):
        self.bridge = OpenWorkBridge(upstream_http, tab_match)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = None

    def start(self, timeout: float = 30) -> str:
        import threading

        result: dict[str, Any] = {}
        started = threading.Event()

        def run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            try:
                result["url"] = loop.run_until_complete(self.bridge.start())
            except BaseException as e:  # reported to the caller
                result["error"] = e
                started.set()
                loop.close()
                return
            started.set()
            loop.run_forever()
            loop.run_until_complete(self.bridge.stop())
            loop.close()

        self._thread = threading.Thread(target=run, name="openwork-cdp-bridge", daemon=True)
        self._thread.start()
        if not started.wait(timeout):
            raise BridgeError("CDP мостът не стартира навреме")
        if "error" in result:
            raise result["error"]
        return result["url"]

    def navigate_lease(self, url: str, timeout: float = 15) -> None:
        if self._loop is None:
            return
        fut = asyncio.run_coroutine_threadsafe(self.bridge.navigate_lease(url), self._loop)
        fut.result(timeout)

    @property
    def url(self) -> str:
        return self.bridge.url

    @property
    def lease_info(self) -> dict | None:
        return self.bridge.lease_info

    def stop(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(10)
        self._loop = self._thread = None

    def __enter__(self) -> "BridgeThread":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()
