import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feeds.sol_feed import SolEventFeed


class FakeWS:
    def __init__(self, msgs):
        self.sent = []
        self._msgs = msgs
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def send(self, raw): self.sent.append(json.loads(raw))
    async def recv(self): return json.dumps({"jsonrpc": "2.0", "id": 1, "result": 1})
    def __aiter__(self): return self._iter()
    async def _iter(self):
        for m in self._msgs:
            yield m


ACCT = json.dumps({"jsonrpc": "2.0", "method": "accountNotification", "params": {
    "result": {"value": {"pubkey": "Obl1"}}}})


def test_account_notification_routes_to_callback(monkeypatch):
    import feeds.sol_feed as sf
    seen, logs = [], []
    f = SolEventFeed(["ws://fake"], lambda: ["Obl1", "Obl2"],
                     seen.append, logs.append)
    def fake_connect(url, **k):
        return FakeWS([ACCT])
    monkeypatch.setattr(sf.websockets, "connect", fake_connect)
    import asyncio
    asyncio.run(f._consume_one("ws://fake", shard_idx=0, total=1,
                               single_pass=True))
    assert seen == ["Obl1"]
    assert f.stats()["events_seen"] == 1
    subs = [s for s in f.health["ws://fake"].snapshot().values()]
    assert subs is not None


def test_disabled_mode_off():
    f = SolEventFeed([], lambda: [], lambda p: None, lambda n: None,
                     enabled=False)
    assert f.stats()["mode"] == "off"


def test_sharding_across_providers(monkeypatch):
    import feeds.sol_feed as sf
    f = SolEventFeed(["ws://a", "ws://b"], lambda: [f"obl{i}" for i in range(5)],
                     lambda p: None, lambda n: None)
    got = {}
    class WS2(FakeWS):
        async def send(self, raw):
            m = json.loads(raw)
            if m.get("method") == "accountSubscribe":
                got.setdefault(id(self), []).append(m["params"][0])
    def fake_connect(url, **k):
        return WS2([])
    monkeypatch.setattr(sf.websockets, "connect", fake_connect)
    import asyncio
    async def drive():
        tasks = [asyncio.create_task(f._consume_one(u, i, 2, single_pass=True))
                 for i, u in enumerate(["ws://a", "ws://b"])]
        await asyncio.gather(*tasks)
    asyncio.run(drive())
    all_subs = sum(got.values(), [])
    assert sorted(all_subs) == [f"obl{i}" for i in range(5)]


def test_subscribes_solend_and_kamino_logs(monkeypatch):
    import feeds.sol_feed as sf
    f = SolEventFeed(["ws://fake"], lambda: ["Obl1"],
                     lambda p: None, lambda n: None)
    progs = []
    class WS3(FakeWS):
        async def send(self, raw):
            m = json.loads(raw)
            if m.get("method") == "logsSubscribe":
                progs.append(m["params"][0])
    def fake_connect(url, **k):
        return WS3([])
    monkeypatch.setattr(sf.websockets, "connect", fake_connect)
    import asyncio
    asyncio.run(f._consume_one("ws://fake", shard_idx=0, total=1,
                               single_pass=True))
    assert set(progs) == {sf.SOLEND_PROGRAM, sf.KAMINO_PROGRAM}
