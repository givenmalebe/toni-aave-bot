"""ETH event feed: WSS fan-out for oracle ticks, competitor liqs, new heads."""
import asyncio
import json
import logging
import websockets

from feeds.common import ProviderHealth, FireDedupe

log = logging.getLogger("eth_feed")

ANSWER_UPDATED_TOPIC0 = (
    "0x0559884fd3a4dab5802a313c0de45135c99f5adfe2e5d167a357b41c9ae4bf76")


def parse_log(lg: dict) -> dict | None:
    topics = lg.get("topics") or []
    if not topics:
        return None
    if topics[0].lower() == ANSWER_UPDATED_TOPIC0 and len(topics) >= 2:
        try:
            price = int(topics[1], 16)
            if price >= 2 ** 255:
                price -= 2 ** 256
            return {"kind": "oracle_tick", "feed": (lg.get("address") or "").lower(),
                    "price": float(price)}
        except ValueError:
            return None
    return None


class EthEventFeed:
    def __init__(self, wss_urls: list[str], on_oracle_tick, on_new_block,
                 enabled: bool = True, bench_threshold: int = 3,
                 bench_window_s: int = 300, bench_seconds: int = 600):
        self.urls = [u for u in (wss_urls or []) if u]
        self.on_oracle_tick = on_oracle_tick
        self.on_new_block = on_new_block
        self.enabled = enabled and bool(self.urls)
        self.health = {u: ProviderHealth(u, bench_threshold, bench_window_s,
                                         bench_seconds) for u in self.urls}
        self.dedupe = FireDedupe(ttl_s=30)
        self.events_seen = 0
        self.mode = "off"

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

    def _handle(self, raw) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        params = msg.get("params") or {}
        result = params.get("result")
        if not isinstance(result, dict):
            return
        self.events_seen += 1
        evt = parse_log(result)
        if evt:
            key = f"{evt['feed']}:{result.get('blockNumber')}"
            if not self.dedupe.seen(key):
                self.on_oracle_tick(evt["feed"], evt["price"])
            return
        number = result.get("number")
        if number is not None:
            try:
                self.on_new_block(int(number, 16))
            except (TypeError, ValueError):
                pass

    async def _consume_one(self, url: str, single_pass: bool = False) -> None:
        h = self.health[url]
        while self.enabled:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    sub = {"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe",
                           "params": ["logs", {"topics": [ANSWER_UPDATED_TOPIC0]}]}
                    await ws.send(json.dumps(sub))
                    await ws.recv()
                    sub2 = {"jsonrpc": "2.0", "id": 2,
                            "method": "eth_subscribe", "params": ["newHeads"]}
                    await ws.send(json.dumps(sub2))
                    await ws.recv()
                    h.record_ok()
                    async for raw in ws:
                        h.note_event()
                        self._handle(raw)
            except Exception as e:
                log.warning("eth feed %s error: %s", url, e)
                h.record_fail()
            if single_pass:
                return
            await asyncio.sleep(min(30, 2 ** min(h.fail_count, 5)))

    async def run(self) -> None:
        if not self.enabled:
            return
        await asyncio.gather(*(self._consume_one(u) for u in self.urls))
