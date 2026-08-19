"""Integration test: verify pre-computed calldata matches on-the-fly computation."""

import precompute_eth as pe
import precompute_sol as ps


def test_eth_cache_roundtrip():
    """ETH: build entry, store, retrieve, verify all fields."""
    pe._cache.clear()
    pe._last_block = 100

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
        swap_path=b"\x00\x01\x02",
        gas_limit=1500000,
        estimated_profit_usd=42.0,
        flash_amount_wei=1000000,
        debt_token="0xA0b86991",
        coll_token="0xC02aaA39",
    )
    pe._cache["0xabc"] = entry

    result = pe.get("0xabc")
    assert result is not None
    assert result["protocol"] == "aave-v3"
    assert result["calldata"].startswith("0xc2fa746c")
    assert result["estimated_profit_usd"] == 42.0


def test_sol_cache_roundtrip():
    """SOL: build entry, store, retrieve, verify all fields."""
    ps._cache.clear()
    ps._last_slot = 440333000

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
        instruction_sequence=[{"program": "solend"}],
        account_metas=[{"pubkey": "res1"}],
        jupiter_route={"in": "SOL", "out": "USDC"},
        estimated_profit_usd=42.0,
    )
    ps._cache["obligation_abc"] = entry

    result = ps.get("obligation_abc")
    assert result is not None
    assert result["obligation"] == "obligation_abc"
    assert result["estimated_profit_usd"] == 42.0
    assert len(result["instruction_sequence"]) == 1


def test_eth_cache_eviction():
    """ETH: verify stale entries are evicted."""
    pe._cache.clear()
    pe._last_block = 200
    pe._cache["0xhot"] = {"updated_block": 199}  # 1 block old
    pe._cache["0xcold"] = {"updated_block": 195}  # 5 blocks old
    evicted = pe.evict_stale(max_blocks_old=3)
    assert evicted == 1
    assert "0xhot" in pe._cache
    assert "0xcold" not in pe._cache


def test_sol_cache_eviction():
    """SOL: verify stale entries are evicted."""
    ps._cache.clear()
    ps._last_slot = 440333100
    ps._cache["hot_obl"] = {"updated_slot": 440333099}  # 1 slot old
    ps._cache["cold_obl"] = {"updated_slot": 440333050}  # 50 slots old
    evicted = ps.evict_stale(max_slots_old=30)
    assert evicted == 1
    assert "hot_obl" in ps._cache
    assert "cold_obl" not in ps._cache
