import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feeds.eth_feed import EthEventFeed, parse_log


SUB_RES = json.dumps({"jsonrpc": "2.0", "id": 1,
                      "result": "0xsub"})
TICK = json.dumps({"jsonrpc": "2.0", "method": "eth_subscription", "params": {
    "subscription": "0xsub",
    "result": {"address": "0xF1", "topics": [
        "0x0559884fd3a4dab5802a313c0de45135c99f5adfe2e5d167a357b41c9ae4bf76",
        "0x00000000000000000000000000000000000000000000000000000000000f4240",
        "0x" + "0"*64,
    ], "blockNumber": "0x64"}}})
HEAD = json.dumps({"jsonrpc": "2.0", "method": "eth_subscription", "params": {
    "subscription": "0xsub",
    "result": {"number": "0x65"}}})


class FakeWS:
    def __init__(self, msgs):
        self._msgs = msgs
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def send(self, raw): pass
    async def recv(self): return SUB_RES
    def __aiter__(self): return self._iter()
    async def _iter(self):
        for m in self._msgs:
            yield m


def test_parse_answer_updated():
    evt = parse_log(json.loads(TICK)["params"]["result"])
    assert evt and evt["feed"] == "0xf1"
    assert abs(evt["price"] - 1_000_000) < 1e-6


def test_stats_mode_off_when_disabled():
    f = EthEventFeed([], on_oracle_tick=lambda a, p: None,
                     on_new_block=lambda b: None, enabled=False)
    assert f.stats()["mode"] == "off"


def test_run_consumes_events(monkeypatch):
    import feeds.eth_feed as ef
    ticks, blocks = [], []
    f = EthEventFeed(["wss://fake"], ticks.append, blocks.append)
    def fake_connect(url, **k):
        return FakeWS([TICK, HEAD])
    monkeypatch.setattr(ef.websockets, "connect", fake_connect)
    import asyncio
    asyncio.run(f._consume_one("wss://fake", single_pass=True))
    assert len(ticks) == 1 and blocks == [101]
    assert f.stats()["events_seen"] == 2
