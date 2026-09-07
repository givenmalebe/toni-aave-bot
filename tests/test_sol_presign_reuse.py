import base64
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sol_scanner as s


BOT_PK = "3Kom4T8Z4Z6zRqgywrqcFL3uJbYjURD5SmzRkDiid7XX"


def _fake_kp(*a, **k):
    return type("KP", (), {"pubkey": lambda self: type(
        "Pubkey", (), {"__str__": lambda self: BOT_PK})()})()


def test_presign_reuse_skips_recompile(monkeypatch):
    calls = {"compile": 0, "fetch_bh": 0, "sim": 0}

    def fake_compile(*a, **k):
        calls["compile"] += 1
        return b"x" * 128

    def fake_fetch(*a, **k):
        calls["fetch_bh"] += 1
        return "a" * 43

    def fake_sim(*a, **k):
        calls["sim"] += 1
        return ""

    def fake_send(raw_txs):
        return "bundle_abc", None

    monkeypatch.setattr(s, "_compile_v0", fake_compile)
    monkeypatch.setattr(s, "latest_blockhash", fake_fetch)
    monkeypatch.setattr(s, "_sim_tx_err", fake_sim)
    monkeypatch.setattr(s, "_jito_send_bundle", fake_send)
    monkeypatch.setattr(s, "_jito_confirm_bundle",
                        lambda bid, **k: {"landed": True, "slot": 123})
    monkeypatch.setattr(s, "_confirm_sol_tx",
                        lambda sig, **k: {"confirmed": True, "slot": 123})
    monkeypatch.setattr(s, "_build_tip_tx", lambda *a, **k: b"tip")
    monkeypatch.setattr(s, "_dynamic_jito_lamports", lambda *a, **k: 1_000_000)
    monkeypatch.setattr(s, "_jito_tip_account", lambda seed: "tip_acct")
    monkeypatch.setattr(s, "fetch_sol_price", lambda: 100.0)
    monkeypatch.setattr(s, "min_sol_liq_usd", lambda: 0.0)
    monkeypatch.setattr(s, "_bot_keypair_path", lambda: "kp.json")
    monkeypatch.setattr(s, "_load_solders_keypair", _fake_kp)
    monkeypatch.setattr(s, "_sponsor_keypair_path", lambda: "")
    monkeypatch.setattr(s, "_liq_account_gaps", lambda plan: [])
    monkeypatch.setattr(s, "_RESERVE_INDEX", {
        "repay": {"mint": "usdc", "pyth": "pyth_r", "switchboard": "sw_r",
                  "liquidity_supply": "liq_sup_r", "available_amount": 10**9,
                  "extra_oracle": ""},
        "coll": {"mint": "so1", "collateral_mint": "c1",
                 "collateral_supply": "coll_sup", "liquidity_supply": "liq_sup_c",
                 "fee_receiver": "fee_rec", "pyth": "pyth_c", "switchboard": "sw_c",
                 "extra_oracle": ""},
    })
    monkeypatch.setattr(s, "_spl_amount", lambda ata: 10**9)
    monkeypatch.setattr(s, "_account_info", lambda pk: {"owner": "tokprog"})
    monkeypatch.setattr(s, "_mint_token_program", lambda mint: "tokprog")
    monkeypatch.setattr(s, "_ata_addr", lambda *a, **k: "ata_" + str(a[1]))
    monkeypatch.setattr(s, "_create_ata_ix", lambda *a, **k: b"ataix")
    monkeypatch.setattr(s, "_solend_ix", lambda *a, **k: b"six")
    monkeypatch.setattr(s, "_drop_placeholder_pk", lambda p: p)
    monkeypatch.setattr(s, "_market_authority", lambda m: "auth_pk")
    monkeypatch.setattr(s, "_LENDING_MARKET", {"address": "market_pk"})
    monkeypatch.setattr(s, "_is_pubkey", lambda p: True)
    monkeypatch.setattr(s, "MINT_SOL", "mint_sol")
    monkeypatch.setattr(s, "TOKEN_PROGRAM", "tokprog")

    plan = {
        "kind": "liq",
        "protocol_id": "solend",
        "obligation": "OblA",
        "debt_reserve": "repay",
        "coll_reserve": "coll",
        "repay_mint": "usdc",
        "withdraw_mint": "so1",
        "debt_amount": 100_000_000,
        "close_factor": 0.5,
        "compute_units": 400_000,
        "priority_fee_ul": 1_000,
        "jito_tip_lamports": 1_000_000,
        "expected_profit_usd": 5.0,
        "presigned": {
            "_raw_refresh_b64": base64.b64encode(b"r" * 64).decode(),
            "_raw_liq_b64": base64.b64encode(b"l" * 64).decode(),
            "_bh": "a" * 43,
            "_repay_amt": 50_000_000,
            "_ts": time.time(),
        },
    }
    rec = s._live_send_liq(plan, {"bot": {"sol": 1.0, "lamports": 10**9}},
                           {"ts": 0})
    assert rec["pre_signed"] is True
    assert rec["stage"] in ("sent", "confirmed", "blocked")
    assert calls["compile"] == 0, "presigned bundle must skip recompile"
    assert calls["fetch_bh"] == 0, "presigned bundle must reuse stored blockhash"
    assert calls["sim"] == 0, "presigned bundle must skip resim"


def test_stale_presign_falls_back_to_live_compile(monkeypatch):
    calls = {"compile": 0}

    def fake_compile(*a, **k):
        calls["compile"] += 1
        return b"x" * 128

    def fake_fetch(*a, **k):
        return {"blockhash": "a" * 43}

    def fake_sim(*a, **k):
        return None

    monkeypatch.setattr(s, "_compile_v0", fake_compile)
    monkeypatch.setattr(s, "latest_blockhash", fake_fetch)
    monkeypatch.setattr(s, "_sim_tx_err", fake_sim)
    monkeypatch.setattr(s, "_jito_send_bundle", lambda raws: ("bid", None))
    monkeypatch.setattr(s, "_jito_confirm_bundle",
                        lambda bid, **k: {"landed": True, "slot": 123})
    monkeypatch.setattr(s, "_confirm_sol_tx",
                        lambda sig, **k: {"confirmed": True, "slot": 123})
    monkeypatch.setattr(s, "_build_tip_tx", lambda *a, **k: b"tip")
    monkeypatch.setattr(s, "_dynamic_jito_lamports", lambda *a, **k: 1_000_000)
    monkeypatch.setattr(s, "_jito_tip_account", lambda seed: "tip_acct")
    monkeypatch.setattr(s, "fetch_sol_price", lambda: 100.0)
    monkeypatch.setattr(s, "min_sol_liq_usd", lambda: 0.0)
    monkeypatch.setattr(s, "_bot_keypair_path", lambda: "kp.json")
    monkeypatch.setattr(s, "_load_solders_keypair", _fake_kp)
    monkeypatch.setattr(s, "_sponsor_keypair_path", lambda: "")
    monkeypatch.setattr(s, "_liq_account_gaps", lambda plan: [])
    monkeypatch.setattr(s, "_RESERVE_INDEX", {
        "repay": {"mint": "usdc", "pyth": "pyth_r", "switchboard": "sw_r",
                  "liquidity_supply": "liq_sup_r", "available_amount": 10**9,
                  "extra_oracle": ""},
        "coll": {"mint": "so1", "collateral_mint": "c1",
                 "collateral_supply": "coll_sup", "liquidity_supply": "liq_sup_c",
                 "fee_receiver": "fee_rec", "pyth": "pyth_c", "switchboard": "sw_c",
                 "extra_oracle": ""},
    })
    monkeypatch.setattr(s, "_spl_amount", lambda ata: 10**9)
    monkeypatch.setattr(s, "_account_info", lambda pk: {"owner": "tokprog"})
    monkeypatch.setattr(s, "_mint_token_program", lambda mint: "tokprog")
    monkeypatch.setattr(s, "_ata_addr", lambda *a, **k: "ata_" + str(a[1]))
    monkeypatch.setattr(s, "_create_ata_ix", lambda *a, **k: b"ataix")
    monkeypatch.setattr(s, "_solend_ix", lambda *a, **k: b"six")
    monkeypatch.setattr(s, "_drop_placeholder_pk", lambda p: p)
    monkeypatch.setattr(s, "_market_authority", lambda m: "auth_pk")
    monkeypatch.setattr(s, "_LENDING_MARKET", {"address": "market_pk"})
    monkeypatch.setattr(s, "_is_pubkey", lambda p: True)
    monkeypatch.setattr(s, "MINT_SOL", "mint_sol")
    monkeypatch.setattr(s, "TOKEN_PROGRAM", "tokprog")

    plan = {
        "kind": "liq",
        "protocol_id": "solend",
        "obligation": "OblB",
        "debt_reserve": "repay",
        "coll_reserve": "coll",
        "repay_mint": "usdc",
        "withdraw_mint": "so1",
        "debt_amount": 100_000_000,
        "close_factor": 0.5,
        "compute_units": 400_000,
        "priority_fee_ul": 1_000,
        "jito_tip_lamports": 1_000_000,
        "expected_profit_usd": 5.0,
        "presigned": {
            "_raw_refresh_b64": base64.b64encode(b"r" * 64).decode(),
            "_raw_liq_b64": base64.b64encode(b"l" * 64).decode(),
            "_bh": "a" * 43,
            "_repay_amt": 50_000_000,
            "_ts": time.time() - 999,  # too old -> live compile
        },
    }
    rec = s._live_send_liq(plan, {"bot": {"sol": 1.0, "lamports": 10**9}},
                           {"ts": 0})
    assert not rec.get("pre_signed")
    assert calls["compile"] >= 1, "stale presign must recompile"


def test_presign_kamino_guards(monkeypatch):
    assert s.presign_kamino_plan(None) is None
    assert s.presign_kamino_plan({"kind": "arb"}) is None
    assert s.presign_kamino_plan({"kind": "liq", "protocol_id": "solend"}) is None
    monkeypatch.setattr(s, "_is_pubkey", lambda p: False)
    assert s.presign_kamino_plan(
        {"kind": "liq", "protocol_id": "kamino",
         "obligation": "not-a-pubkey"}) is None


def test_presign_marginfi_guards(monkeypatch):
    assert s.presign_marginfi_plan(None) is None
    assert s.presign_marginfi_plan({"kind": "liq", "protocol_id": "kamino"}) is None
    assert s.presign_marginfi_plan(
        {"kind": "liq", "protocol_id": "marginfi", "obligation": ""}) is None


def test_build_precompute_entries_wire_kamino_marginfi(monkeypatch):
    monkeypatch.setattr(s, "presign_kamino_plan",
                        lambda plan: {"_proto": "kamino", "_ts": time.time()})
    monkeypatch.setattr(s, "presign_marginfi_plan",
                        lambda plan: {"_proto": "marginfi", "_ts": time.time()})
    monkeys = {
        "kamino": {"obligation": "k1", "protocol_id": "kamino", "hf": 0.9,
                   "plan": {"ready": True}},
        "marginfi": {"obligation": "m1", "protocol_id": "marginfi", "hf": 0.9,
                     "plan": {"ready": True}},
        "solend": {"obligation": "s1", "protocol_id": "solend", "hf": 0.9,
                   "plan": {"ready": True}},
    }
    monkeypatch.setattr(s, "presign_solend_plan",
                        lambda plan: {"_proto": "solend", "_ts": time.time()})
    monkeypatch.setattr(s, "_is_pubkey", lambda p: bool(p))
    monkeypatch.setattr(s, "_prewarm_jupiter_routes", lambda: {})
    monkeypatch.setattr(s, "_kamino_reserves", lambda: {})
    monkeypatch.setattr(s, "fetch_sol_price", lambda: 100.0)
    entries = s.build_precompute_entries(list(monkeys.values()))
    by_obl = {e["obligation"]: e for e in entries}
    assert by_obl["k1"]["presigned"]["_proto"] == "kamino"
    assert by_obl["m1"]["presigned"]["_proto"] == "marginfi"
    assert by_obl["s1"]["presigned"]["_proto"] == "solend"