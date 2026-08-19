import precompute_sol as ps


def test_cache_miss():
    ps._cache.clear()
    ps._cache_hits = 0
    ps._cache_misses = 0
    result = ps.get("nonexistent_obligation")
    assert result is None
    assert ps._cache_misses == 1


def test_cache_hit():
    ps._cache.clear()
    ps._cache["obligation_abc"] = {"kind": "liq", "updated_slot": 440333000}
    result = ps.get("obligation_abc")
    assert result is not None
    assert result["kind"] == "liq"


def test_evict_stale():
    ps._cache.clear()
    ps._last_slot = 440333100
    ps._cache["old_obl"] = {"updated_slot": 440333000}  # 100 slots old
    ps._cache["new_obl"] = {"updated_slot": 440333099}  # 1 slot old
    evicted = ps.evict_stale(max_slots_old=30)
    assert evicted == 1
    assert "old_obl" not in ps._cache
    assert "new_obl" in ps._cache
