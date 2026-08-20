import asyncio
import time

import precompute_eth as pe

def test_cache_miss():
    pe._cache.clear()
    pe._cache_hits = 0
    pe._cache_misses = 0
    result = pe.get("0xnonexistent")
    assert result is None
    assert pe._cache_misses == 1

def test_cache_hit():
    pe._cache.clear()
    pe._cache["0xabc"] = {"ts": time.time(), "data": {"calldata": "0x123", "updated_block": 100}}
    result = pe.get("0xabc")
    assert result is not None
    assert result["calldata"] == "0x123"

def test_evict_stale():
    pe._cache.clear()
    pe._last_block = 100
    pe._cache["0xold"] = {"ts": time.time(), "data": {"updated_block": 95}}  # 5 blocks old
    pe._cache["0xnew"] = {"ts": time.time(), "data": {"updated_block": 99}}  # 1 block old
    evicted = pe.evict_stale(max_blocks_old=3)
    assert evicted == 1
    assert "0xold" not in pe._cache
    assert "0xnew" in pe._cache

def test_build_entry():
    entry = pe.build_entry(
        protocol="aave-v3",
        user="0xabc",
        collateral="0xcoll",
        debt="0xdead",
        debt_amount_wei=1000000,
        hf=0.95,
        contract_addr="0xcontract",
        liq_sig="0xc2fa746c",
        liq_args=["0xabc", "0xcoll", "0xdead", "0xf4240"],
        swap_path=b"\x00",
        gas_limit=1500000,
        estimated_profit_usd=42.0,
        flash_amount_wei=1000000,
        debt_token="0xA0b86991",
        coll_token="0xC02aaA39",
    )
    assert entry["protocol"] == "aave-v3"
    assert entry["calldata"].startswith("0xc2fa746c")
    assert entry["estimated_profit_usd"] == 42.0
    assert entry["hf"] == 0.95


class MockResponse:
    def __init__(self, data):
        self._data = data
    async def json(self):
        return self._data
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        pass


class FakeSession:
    def __init__(self, block_num=100):
        self._block_num = block_num
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        pass
    def post(self, url, json=None, timeout=None):
        return MockResponse({"result": hex(self._block_num)})


def test_refresh_updates_cache(monkeypatch):
    pe._cache.clear()
    pe._last_block = 0
    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", lambda: FakeSession(200))

    pos = {
        "user": "0xabc", "protocol": "aave-v3", "collateral": "0xcoll",
        "debt": "0xdead", "debtToCover": "1000000", "hf": 0.95,
        "contract": "0xcontract", "liq_sig": "0xc2fa746c",
        "liq_args": ["0xabc", "0xcoll", "0xdead", "0xf4240"],
        "swap_path": b"\x00", "gas_limit": 1500000, "net_usd": 42.0,
        "flash_amount": "1000000", "debt_token": "0xA0b86991", "coll_token": "0xC02aaA39",
    }
    asyncio.run(pe.refresh([pos], "http://mock"))
    assert pe._last_block == 200
    assert "0xabc" in pe._cache
