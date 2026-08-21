import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import precompute_eth, precompute_sol


def test_eth_refresh_tolerates_string_numerics(monkeypatch):
    class FakeResp:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def json(self): return {"result": "0x10"}
    class FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def post(self, *a, **k): return FakeResp()
    monkeypatch.setattr("aiohttp.ClientSession", FakeSession)
    pos = [{"user": "0xA", "debtToCover": "12", "hf": "0.9",
            "flash_amount": "100", "net_usd": "1.5"}]
    asyncio.run(precompute_eth.refresh(pos, "http://x"))
    entry = precompute_eth.get("0xa")
    assert entry is not None and entry["hf"] == 0.9


def test_sol_refresh_tolerates_string_numerics(monkeypatch):
    class FakeResp:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def json(self): return {"result": 100}
    class FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def post(self, *a, **k): return FakeResp()
    monkeypatch.setattr("aiohttp.ClientSession", FakeSession)
    obl = [{"obligation": "Obl1", "hf": "0.9", "debt_amount": "5",
            "expected_profit_usd": "0.5"}]
    asyncio.run(precompute_sol.refresh(obl, "http://x"))
    assert precompute_sol.get("Obl1") is not None
