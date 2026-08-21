"""Regression tests for Kamino kLend obligation parsing.

Layout per official klend IDL (Kamino-Finance/klend-sdk), validated on-chain
2026-08-21: collateral-sum == depositedValueSf on 53/53 live accounts.
"""
import base58
import pytest

from sol_lending import kamino


def _obl(deposits=None, borrows=None, deposited_sf=0, debt_mv_sf=0,
         allowed_sf=0, unhealthy_sf=0):
    raw = bytearray(3344)
    raw[0:8] = bytes.fromhex("a8ce8d6a584caca7")
    raw[16:24] = (421_313_588).to_bytes(8, "little")
    raw[24] = 1  # stale
    raw[32:64] = b"\x22" * 32  # lending market
    raw[64:96] = b"\x11" * 32  # owner
    for i, (res, amt, mv) in enumerate(deposits or []):
        off = 96 + 136 * i
        raw[off:off + 32] = res
        raw[off + 32:off + 40] = amt.to_bytes(8, "little")
        raw[off + 40:off + 56] = mv.to_bytes(16, "little")
    raw[1192:1208] = deposited_sf.to_bytes(16, "little")
    for j, (res, bamt, bmv) in enumerate(borrows or []):
        off = 1208 + 200 * j
        raw[off:off + 32] = res
        raw[off + 88:off + 104] = bamt.to_bytes(16, "little")
        raw[off + 104:off + 120] = bmv.to_bytes(16, "little")
    raw[2224:2240] = debt_mv_sf.to_bytes(16, "little")
    raw[2240:2256] = allowed_sf.to_bytes(16, "little")
    raw[2256:2272] = unhealthy_sf.to_bytes(16, "little")
    raw[2287] = 1 if borrows else 0
    return bytes(raw)


OWNER_B58 = base58.b58encode(b"\x11" * 32).decode()


def test_liquidatable_obligation_hf_and_usd():
    dep_mvs = [int(12.5e18), int(8.0e18)]
    deposits = [(b"\xa1" * 32, 5_000_000_000, dep_mvs[0]),
                (b"\xa2" * 32, 80_000_000, dep_mvs[1])]
    borrows = [(b"\xb1" * 32, int(8.9e18), int(9.87e18))]
    raw = _obl(deposits=deposits, borrows=borrows,
               deposited_sf=sum(dep_mvs),
               debt_mv_sf=int(9.87e18), allowed_sf=int(15.4e18),
               unhealthy_sf=int(8.2e18))
    row = kamino.parse_obligation("OblPk", raw)
    assert row is not None
    assert row["owner"] == OWNER_B58
    assert row["hf"] == pytest.approx(8.2 / 9.87, abs=1e-4)
    assert row["coll_usd"] == pytest.approx(20.50, abs=0.01)
    assert row["debt_usd"] == pytest.approx(9.87, abs=0.01)
    assert row["borrowed_usd"] == pytest.approx(9.87, abs=0.01)
    assert len(row["deposit_reserves"]) == 2
    assert len(row["borrow_reserves"]) == 1


def test_old_bug_offsets_are_not_read_as_values():
    # A pubkey at offset 96 (old bug read it as deposited_value) must not
    # poison aggregates decoded from the true offsets.
    dep_mv = int(30.0e18)
    raw = bytearray(_obl(
        deposits=[(bytes(range(32)), 1_000_000, dep_mv)],
        borrows=[(b"\xb1" * 32, int(2.0e18), int(10.0e18))],
        deposited_sf=dep_mv, debt_mv_sf=int(10.0e18),
        allowed_sf=int(7.5e18), unhealthy_sf=int(8.0e18)))
    row = kamino.parse_obligation("OblPk", bytes(raw))
    assert row["debt_usd"] == pytest.approx(10.0, abs=0.01)
    assert row["hf"] == pytest.approx(0.8, abs=1e-3)


def test_healthy_obligation_parses_with_hf_above_one():
    raw = _obl(deposits=[(b"\xa1" * 32, 1_000, int(100e18))],
               borrows=[(b"\xb1" * 32, int(1e18), int(10e18))],
               deposited_sf=int(100e18), debt_mv_sf=int(10e18),
               allowed_sf=int(75e18), unhealthy_sf=int(85e18))
    row = kamino.parse_obligation("OblPk", raw)
    assert row is not None
    assert row["hf"] == pytest.approx(8.5, abs=1e-3)


def test_zero_debt_returns_none():
    raw = _obl(deposits=[(b"\xa1" * 32, 1_000, int(5e18))],
               deposited_sf=int(5e18))
    assert kamino.parse_obligation("OblPk", raw) is None


def test_short_account_returns_none():
    assert kamino.parse_obligation("OblPk", b"\x01" * 200) is None


def test_absurd_values_rejected():
    # Near-u128-max WADs decode to absurd USD — must be rejected outright.
    huge = 1 << 126
    raw = bytearray(_obl(
        borrows=[(b"\xb1" * 32, huge, huge)],
        debt_mv_sf=huge, allowed_sf=huge, unhealthy_sf=huge))
    assert kamino.parse_obligation("OblPk", bytes(raw)) is None
