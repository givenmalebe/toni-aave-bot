"""SOL event feed: pubsub fan-out for obligations and program accounts."""
import asyncio
import json
import logging
import websockets

from feeds.common import ProviderHealth, shard

log = logging.getLogger("sol_feed")

# Keep in lockstep with sol_scanner / sol_lending.kamino
SOLEND_PROGRAM = "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"
KAMINO_PROGRAM = "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD"
DISCOVERY_PROGRAMS = (SOLEND_PROGRAM, KAMINO_PROGRAM)


class SolEventFeed:
    def __init__(self, ws_urls: list[str], obligations_provider,
                 on_account_change, on_program_log=None, enabled: bool = True,
                 bench_threshold: int = 3, bench_window_s: int = 300,
                 bench_seconds: int = 600):
        self.urls = [u for u in (ws_urls or []) if u]
        self.obligations_provider = obligations_provider
        self.on_account_change = on_account_change
        self.on_program_log = on_program_log or (lambda n: None)
        self.enabled = enabled and bool(self.urls)
        self.health = {u: ProviderHealth(u, bench_threshold, bench_window_s,
                                         bench_seconds) for u in self.urls}
        self.events_seen = 0
        self.mode = "off"
        self._pending: dict[str, dict] = {}
        self._subs: dict[str, dict] = {}

    def stats(self) -> dict:
        if not self.enabled:
            self.mode = "off"
        else:
            live = sum(1 for h in self.health.values() if h.available)
            self.mode = "live" if live else "degraded"
        return {"mode": self.mode, "events_seen": self.events_seen,
                "providers": [h.snapshot() for h in self.health.values()]}

    def stop(self) -> None:
        self.enabled = False

    def _emit_pubkey(self, pubkey: str) -> None:
        if pubkey:
            self.on_account_change(pubkey)

    def _handle(self, url: str, raw) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        pending = self._pending.setdefault(url, {})
        subs = self._subs.setdefault(url, {})
        if "method" not in msg and "id" in msg and "result" in msg:
            kind = pending.pop(msg.get("id"), None)
            if isinstance(kind, dict) and kind.get("type") == "account":
                subs[msg["result"]] = kind.get("pubkey")
            return
        method = msg.get("method") or ""
        params = msg.get("params") or {}
        result = params.get("result") or {}
        self.events_seen += 1
        if method == "accountNotification":
            sub = params.get("subscription")
            pubkey = subs.get(sub) or (result.get("value") or {}).get("pubkey")
            if pubkey:
                self._emit_pubkey(pubkey)
        elif method == "programNotification":
            val = result.get("value") if isinstance(result, dict) else {}
            if not isinstance(val, dict):
                val = {}
            pubkey = val.get("pubkey")
            if pubkey:
                self._emit_pubkey(pubkey)
            self.on_program_log(result)

    async def _consume_one(self, url: str, shard_idx: int, total: int,
                           single_pass: bool = False) -> None:
        h = self.health[url]
        while self.enabled:
            try:
                my_obls = shard(self.obligations_provider(), total)[shard_idx]
                async with websockets.connect(url, ping_interval=20) as ws:
                    rid = 1
                    pending = self._pending.setdefault(url, {})
                    for obl in my_obls:
                        pending[rid] = {"type": "account", "pubkey": obl}
                        await ws.send(json.dumps({
                            "jsonrpc": "2.0", "id": rid,
                            "method": "accountSubscribe",
                            "params": [obl, {"encoding": "base64",
                                             "commitment": "confirmed"}]}))
                        rid += 1
                    for prog in DISCOVERY_PROGRAMS:
                        pending[rid] = {"type": "program", "program": prog}
                        await ws.send(json.dumps({
                            "jsonrpc": "2.0", "id": rid,
                            "method": "programSubscribe",
                            "params": [prog, {
                                "encoding": "base64",
                                "commitment": "confirmed",
                            }]}))
                        rid += 1
                    h.record_ok()
                    async for raw in ws:
                        h.note_event()
                        self._handle(url, raw)
            except Exception as e:
                log.warning("sol feed %s error: %s", url, e)
                h.record_fail()
            if single_pass:
                return
            await asyncio.sleep(min(30, 2 ** min(h.fail_count, 5)))

    async def run(self) -> None:
        if not self.enabled:
            return
        n = len(self.urls)
        await asyncio.gather(
            *(self._consume_one(u, i, n) for i, u in enumerate(self.urls)))
