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
    pe._cache["0xabc"] = {"calldata": "0x123", "updated_block": 100}
    result = pe.get("0xabc")
    assert result is not None
    assert result["calldata"] == "0x123"

def test_evict_stale():
    pe._cache.clear()
    pe._last_block = 100
    pe._cache["0xold"] = {"updated_block": 95}  # 5 blocks old
    pe._cache["0xnew"] = {"updated_block": 99}  # 1 block old
    evicted = pe.evict_stale(max_blocks_old=3)
    assert evicted == 1
    assert "0xold" not in pe._cache
    assert "0xnew" in pe._cache
