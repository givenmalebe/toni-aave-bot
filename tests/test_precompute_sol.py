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


def test_build_sol_entry():
    entry = ps.build_entry(
        obligation="obligation_abc",
        kind="liq",
        repay_reserve="repay_res",
        withdraw_reserve="withdraw_res",
        repay_mint="USDC",
        withdraw_mint="SOL",
        debt_amount=844200000,
        hf=1.0,
        compute_units=400000,
        priority_fee_ul=50000,
        jito_tip_lamports=50000,
        instruction_sequence=[{"program": "solend", "data": "0x123"}],
        account_metas=[{"pubkey": "res1", "is_signer": False, "is_writable": True}],
        jupiter_route={"in": "SOL", "out": "USDC", "amount": 844200000},
        estimated_profit_usd=42.0,
    )
    assert entry["obligation"] == "obligation_abc"
    assert entry["debt_amount"] == 844200000
    assert entry["estimated_profit_usd"] == 42.0
    assert len(entry["instruction_sequence"]) == 1
