"""ETH long-tail debt band gate ($10k min, $500k max by default)."""
import pytest

from profit_engine import liq_debt_band_skip


def test_below_band_min_skipped():
    skip, why = liq_debt_band_skip(2_780.0)
    assert skip
    assert "band min" in why


def test_at_band_min_passes():
    assert not liq_debt_band_skip(10_000.0)[0]


def test_mid_band_passes():
    assert not liq_debt_band_skip(48_240.16)[0]


def test_at_band_max_passes():
    assert not liq_debt_band_skip(500_000.0)[0]


def test_above_band_max_skipped():
    skip, why = liq_debt_band_skip(43_148_939.26)
    assert skip
    assert "band max" in why


def test_unknown_debt_passes_through():
    # Other +EV gates still protect us; band only applies when known.
    assert not liq_debt_band_skip(None)[0]
    assert not liq_debt_band_skip(0)[0]
    assert not liq_debt_band_skip("not-a-number")[0]


def test_custom_bounds():
    assert liq_debt_band_skip(5_000, min_usd=10_000)[0]
    assert not liq_debt_band_skip(5_000, min_usd=1_000)[0]
    assert liq_debt_band_skip(600_000, max_usd=500_000)[0]


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("MIN_LIQ_DEBT_USD", "20000")
    monkeypatch.setenv("MAX_LIQ_DEBT_USD", "100000")
    import importlib
    import profit_engine
    importlib.reload(profit_engine)
    try:
        assert profit_engine.liq_debt_band_skip(15_000)[0]
        assert not profit_engine.liq_debt_band_skip(25_000)[0]
        assert profit_engine.liq_debt_band_skip(150_000)[0]
    finally:
        importlib.reload(profit_engine)
