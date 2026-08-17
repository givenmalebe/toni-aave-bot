"""Compound v3 Comet (USDC / WETH, optional USDT if code exists).

Path: isLiquidatable → absorb + buyCollateral after Aave V3 flash of the base token.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import time

import liquidation_bot as lb

from . import executor as ex
from . import util as u

ID = "compound"
LABEL = "Compound"

# Official Compound III Ethereum proxies (docs.compound.finance / Comet repo).
COMETS = (
    {"id": "cUSDCv3", "addr": "0xc3d688B66703497DAA19211EEdff47f25384cdc3",
     "base_sym": "USDC"},
    {"id": "cWETHv3", "addr": "0xA17581A9E3356d9A858b789D68B4d866e593aE94",
     "base_sym": "WETH"},
    {"id": "cUSDTv3", "addr": "0x3Afdc9BCA9213A35503b077a6072F3D0d5AB0840",
     "base_sym": "USDT"},
)

_markets_cache = {"ts": 0, "rows": []}
_asset_cache = {}


def _live_comets() -> list[dict]:
    now = time.time()
    if _markets_cache["rows"] and now - _markets_cache["ts"] < 600:
        return list(_markets_cache["rows"])
    rows = []
    for m in COMETS:
        a = m["addr"].lower()
        if u.has_code(a):
            rec = dict(m)
            rec["addr"] = a
            try:
                raw = u.call(a, u.SEL["baseToken"])
                rec["base"] = u.addr_word(u.words(raw)[0])
            except Exception:
                rec["base"] = ""
            try:
                raw = u.call(a, u.SEL["baseTokenPriceFeed"])
                rec["base_feed"] = u.addr_word(u.words(raw)[0])
            except Exception:
                rec["base_feed"] = ""
            try:
                rec["base_scale"] = int(u.call(a, u.SEL["baseScale"]), 16)
            except Exception:
                rec["base_scale"] = 10 ** (6 if rec["base_sym"] in ("USDC", "USDT") else 18)
            try:
                rec["store_front"] = int(u.call(a, u.SEL["storeFrontPriceFactor"]), 16)
            except Exception:
                rec["store_front"] = None
            try:
                rec["absorb_paused"] = int(u.call(a, u.SEL["isAbsorbPaused"]), 16) != 0
                rec["buy_paused"] = int(u.call(a, u.SEL["isBuyPaused"]), 16) != 0
            except Exception:
                rec["absorb_paused"] = False
                rec["buy_paused"] = False
            rec["assets"] = _assets(a)
            rows.append(rec)
    _markets_cache["ts"] = now
    _markets_cache["rows"] = rows
    return rows


def enabled() -> bool:
    return bool(_live_comets())


def _assets(comet: str) -> list[dict]:
    hit = _asset_cache.get(comet)
    now = time.time()
    if hit and now - hit[0] < 600:
        return hit[1]
    out = []
    try:
        n = int(u.call(comet, u.SEL["numAssets"]), 16) & 0xFF
    except Exception:
        n = 0
    for i in range(min(n, 16)):
        try:
            raw = u.call(comet, u.SEL["getAssetInfo"] + u.pad32(i))
            ws = u.words(raw)
            if len(ws) < 8:
                continue
            # AssetInfo: offset, asset, priceFeed, scale, borrowCF, liqCF, liqFactor, supplyCap
            rec = {
                "offset": int(ws[0], 16),
                "asset": u.addr_word(ws[1]),
                "price_feed": u.addr_word(ws[2]),
                "scale": int(ws[3], 16),
                "borrow_cf": int(ws[4], 16),
                "liq_cf": int(ws[5], 16),
                "liq_factor": int(ws[6], 16),
                "sym": lb.token_sym(u.addr_word(ws[1])),
            }
            if rec["asset"] and int(rec["asset"], 16):
                out.append(rec)
        except Exception:
            continue
    _asset_cache[comet] = (now, out)
    return out


def _comet_price(comet: str, feed: str) -> float:
    if not feed:
        return 0.0
    try:
        raw = u.call(comet, u.SEL["getPrice"] + u.pad_addr(feed)[2:])
        n = int(raw, 16)
        if n > 0:
            return n / 1e8
    except Exception:
        pass
    return 0.0


def _is_liq(comet: str, user: str) -> bool:
    try:
        raw = u.call(comet, u.SEL["isLiquidatable"] + u.pad_addr(user)[2:])
        return int(raw, 16) != 0
    except Exception:
        return False


def _borrow(comet: str, user: str) -> int:
    try:
        return int(u.call(comet, u.SEL["borrowBalanceOf"] + u.pad_addr(user)[2:]), 16)
    except Exception:
        return 0


def _coll_bal(comet: str, user: str, asset: str) -> int:
    try:
        data = u.SEL["userCollateral"] + u.pad_addr(user)[2:] + u.pad_addr(asset)[2:]
        raw = u.call(comet, data)
        ws = u.words(raw)
        return int(ws[0], 16) if ws else 0
    except Exception:
        return 0


def harvest_users(from_block: int, to_block: int) -> dict:
    markets = _live_comets()
    users: dict[str, int] = {}
    n_logs = 0
    errors = []
    addrs = [m["addr"] for m in markets]
    if not addrs:
        return {"users": u.load_users(ID), "n_logs": 0, "errors": ["no comet code"]}
    topics_idx = (
        (u.TOPIC["comet_withdraw"], 1),
        (u.TOPIC["comet_supply_coll"], 2),
        (u.TOPIC["comet_absorb_coll"], 2),
        (u.TOPIC["comet_absorb_debt"], 2),
    )
    for topic, idx in topics_idx:
        logs, err = lb.get_logs_chunked(from_block, to_block, addrs, [topic])
        errors.extend(err)
        n_logs += len(logs)
        for lg in logs:
            topics = lg.get("topics") or []
            if len(topics) > idx:
                usr = u.addr_word(str(topics[idx]).replace("0x", "").rjust(64, "0"))
                if usr.startswith("0x") and int(usr, 16):
                    users[usr] = int(lg.get("blockNumber") or "0x0", 16)
    cached = u.load_users(ID)
    for a in cached:
        users.setdefault(a, 0)
    u.save_users(ID, list(users.keys()) + cached)
    return {
        "users": list(users.keys()),
        "n_logs": n_logs,
        "errors": errors[:8],
        "from_block": from_block,
        "to_block": to_block,
    }


def _score_market(m: dict, user: str, gas_gwei: float, eth_usd: float) -> dict | None:
    comet = m["addr"]
    if m.get("absorb_paused") or m.get("buy_paused"):
        return None
    if not _is_liq(comet, user):
        return None
    borrowed = _borrow(comet, user)
    if borrowed <= 0:
        return None
    base_px = _comet_price(comet, m.get("base_feed") or "")
    if base_px <= 0:
        base_px = lb.asset_price_usd(m.get("base") or "")
    scale = int(m.get("base_scale") or 1) or 1
    debt_usd = (borrowed / scale) * base_px
    if debt_usd < u.DUST_USD:
        return None
    coll_usd = 0.0
    weighted = 0.0
    best_coll = None
    best_coll_usd = 0.0
    leftover = ""
    for asset in m.get("assets") or []:
        bal = _coll_bal(comet, user, asset["asset"])
        if bal <= 0:
            continue
        px = _comet_price(comet, asset.get("price_feed") or "")
        if px <= 0:
            px = lb.asset_price_usd(asset["asset"])
        ast_scale = int(asset.get("scale") or 0) or (10 ** lb.token_decimals(asset["asset"]))
        usd = (bal / ast_scale) * px
        coll_usd += usd
        liq_cf = int(asset.get("liq_cf") or 0)
        weighted += usd * (liq_cf / 1e18 if liq_cf else 0)
        if usd > best_coll_usd:
            best_coll_usd = usd
            best_coll = asset
    if coll_usd <= 0 or not best_coll:
        leftover = "collateral remaining not decoded — skipped"
        return None
    hf_f = (weighted / debt_usd) if debt_usd else 999.0
    sf = m.get("store_front")
    liq_factor = int(best_coll.get("liq_factor") or 0)
    if not sf or not liq_factor:
        leftover = "storeFront/liquidationFactor not decoded — no fake +EV"
        bonus_usd = 0.0
        extra = {
            "flash_fee_bps": u.aave_flash_bps(),
            "flash_fee_usd": 0.0,
            "flash_fee_src": "aave-v3",
            "flash_note": "Aave V3 flashLoan",
            "net_usd": None,
            "profit_usd": None,
        }
        # still surface as liquidatable without inventing net
        net_ok = False
    else:
        # discount = storeFront * (1 - liquidationFactor); profit on coll at that discount
        discount = (int(sf) / 1e18) * (1.0 - liq_factor / 1e18)
        bonus_usd = best_coll_usd * discount
        gas = u.gas_usd(gas_gwei, eth_usd, u.GAS_UNITS_COMET)
        extra = u.apply_flash_fee(debt_usd, bonus_usd, 0.0, gas)
        net_ok = extra["net_usd"] is not None and extra["net_usd"] > 0
        leftover = ""
        if not net_ok:
            leftover = "net ≤ 0 after flash+gas"
    rec = u.opp_base(
        protocol_id=ID, protocol_label=LABEL, user=user, live_ok=True,
        leftover=leftover)
    rec.update({
        "hf": str(int(hf_f * 1e18)) if hf_f < 100 else str((1 << 256) - 1),
        "coll_usd": round(coll_usd, 2),
        "debt_usd": round(debt_usd, 2),
        "cover_usd": round(debt_usd, 2),
        "bonus_usd": round(bonus_usd, 2),
        "coll_sym": best_coll.get("sym") or lb.token_sym(best_coll["asset"]),
        "debt_sym": m.get("base_sym") or "?",
        "coll_addr": best_coll["asset"],
        "debt_addr": m.get("base") or "",
        "collateralAsset": best_coll["asset"],
        "debtAsset": m.get("base") or "",
        "venue": m.get("id") or "comet",
        "liquidatable": True,
        "source": "sweep",
        "debtToCover": str(borrowed),
    })
    rec.update({k: extra[k] for k in extra if k != "gas_usd"})
    rec["gas_usd"] = round(u.gas_usd(gas_gwei, eth_usd, u.GAS_UNITS_COMET), 2)
    if rec.get("net_usd") is not None and float(rec["net_usd"]) <= 0:
        return None
    if rec.get("net_usd") is None:
        return None  # don't show fake +EV rows
    fp = ex.plan_comet(
        comet=comet,
        user=user,
        coll=rec["collateralAsset"],
        base=rec["debtAsset"],
        base_wei=borrowed,
    )
    fp["market"] = m.get("id")
    ex.apply_plan(rec, fp)
    return rec


def scan_liquidatable(users, *, gas_gwei: float, eth_usd: float,
                      contested=None, recent_comp=None, max_users: int = 60) -> dict:
    markets = _live_comets()
    if not markets:
        return {"opps": [], "watch": [], "scanned": 0, "skipped": {"rpc": 0}}
    uniq = u.uniq_addrs(list(users or []) + u.load_users(ID), max_users)
    contested = {str(x).lower() for x in (contested or [])}
    recent_comp = {str(x).lower() for x in (recent_comp or [])}
    opps, watch, skipped = [], [], {
        "dust": 0, "healthy": 0, "no_account": 0, "negative": 0, "rpc": 0,
    }
    keep = []

    def one(usr):
        best, accs = None, []
        for m in markets:
            try:
                liq = _is_liq(m["addr"], usr)
                borrowed = _borrow(m["addr"], usr)
            except Exception as e:  # noqa: BLE001
                return usr, None, str(e)[:80]
            if borrowed <= 0 and not liq:
                continue
            accs.append((m, liq, borrowed))
            if liq:
                scored = _score_market(m, usr, gas_gwei, eth_usd)
                if scored and (best is None
                               or float(scored.get("net_usd") or 0)
                               > float(best.get("net_usd") or 0)):
                    best = scored
        return usr, (best, accs), None

    rows = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for item in ex.map(one, uniq):
            rows.append(item)

    for usr, packed, err in rows:
        if err:
            skipped["rpc"] += 1
            continue
        best, accs = packed if packed else (None, [])
        if not accs:
            skipped["no_account"] += 1
            continue
        keep.append(usr)
        # closest watch: min implied HF among markets with borrow
        min_hf = None
        debt_u = 0.0
        coll_u = 0.0
        if best:
            try:
                min_hf = float(int(best["hf"]) / 1e18)
            except Exception:
                min_hf = 0.99
            debt_u = float(best.get("debt_usd") or 0)
            coll_u = float(best.get("coll_usd") or 0)
        watch.append({
            "user": usr,
            "hf": str(int((min_hf if min_hf is not None else 2.0) * 1e18)),
            "coll_usd": round(coll_u, 2),
            "debt_usd": round(debt_u, 2),
            "protocol_id": ID,
            "protocol": LABEL,
        })
        if best:
            race = usr in contested or usr in recent_comp
            best["contested"] = race
            best["recent_competitor"] = usr in recent_comp
            best["race"] = race
            opps.append(best)
        else:
            skipped["healthy"] += 1
    watch.sort(key=lambda r: int(r.get("hf") or 0))
    u.save_users(ID, keep + uniq[:40])
    return {
        "opps": opps,
        "watch": watch[:12],
        "scanned": len(uniq),
        "skipped": skipped,
    }


def _parse_absorb(lg) -> dict | None:
    topics = lg.get("topics") or []
    t0 = str(topics[0]).lower() if topics else ""
    addr = (lg.get("address") or "").lower()
    base = {
        "spoke": addr,
        "tx": (lg.get("transactionHash") or "").lower(),
        "block": int(lg.get("blockNumber") or "0x0", 16),
        "log_index": int(lg.get("logIndex") or "0x0", 16),
        "topic0": t0,
        "protocol_id": ID,
        "protocol": LABEL,
        "venue": next((m["id"] for m in COMETS if m["addr"].lower() == addr), "comet"),
    }
    ws = u.words(lg.get("data"))
    if t0 == u.TOPIC["comet_absorb_coll"]:
        if len(topics) < 4:
            return None
        asset = u.addr_word(str(topics[3]).replace("0x", "").rjust(64, "0"))
        seized = int(ws[0], 16) if ws else 0
        usd = int(ws[1], 16) if len(ws) > 1 else 0
        return {
            **base,
            "searcher": u.addr_word(str(topics[1]).replace("0x", "").rjust(64, "0")),
            "user": u.addr_word(str(topics[2]).replace("0x", "").rjust(64, "0")),
            "coll_addr": asset,
            "coll_sym": lb.token_sym(asset),
            "debt_addr": "",
            "debt_sym": "?",
            "coll_seized": seized,
            "debt_to_cover": 0,
            "coll_usd_raw": usd,
        }
    if t0 == u.TOPIC["comet_absorb_debt"]:
        if len(topics) < 3:
            return None
        paid = int(ws[0], 16) if ws else 0
        return {
            **base,
            "searcher": u.addr_word(str(topics[1]).replace("0x", "").rjust(64, "0")),
            "user": u.addr_word(str(topics[2]).replace("0x", "").rjust(64, "0")),
            "coll_addr": "",
            "coll_sym": "?",
            "debt_addr": "",
            "debt_sym": next((m["base_sym"] for m in COMETS
                              if m["addr"].lower() == addr), "?"),
            "coll_seized": 0,
            "debt_to_cover": paid,
        }
    if t0 == u.TOPIC["comet_buy"]:
        if len(topics) < 3:
            return None
        asset = u.addr_word(str(topics[2]).replace("0x", "").rjust(64, "0"))
        base_amt = int(ws[0], 16) if ws else 0
        coll_amt = int(ws[1], 16) if len(ws) > 1 else 0
        return {
            **base,
            "searcher": u.addr_word(str(topics[1]).replace("0x", "").rjust(64, "0")),
            "user": "",
            "coll_addr": asset,
            "coll_sym": lb.token_sym(asset),
            "debt_addr": "",
            "debt_sym": next((m["base_sym"] for m in COMETS
                              if m["addr"].lower() == addr), "?"),
            "coll_seized": coll_amt,
            "debt_to_cover": base_amt,
            "kind": "buyCollateral",
        }
    return None


def scan_competitor_logs(from_block: int, to_block: int, extra_addrs=None) -> dict:
    markets = _live_comets()
    addrs = [m["addr"] for m in markets]
    if not addrs:
        return {"events": [], "n_logs": 0, "errors": [], "from_block": from_block,
                "to_block": to_block}
    events, n_logs, errors = [], 0, []
    for topic in (u.TOPIC["comet_absorb_coll"], u.TOPIC["comet_absorb_debt"],
                  u.TOPIC["comet_buy"]):
        logs, err = lb.get_logs_chunked(from_block, to_block, addrs, [topic])
        errors.extend(err)
        n_logs += len(logs)
        for lg in logs:
            p = _parse_absorb(lg)
            if p:
                events.append(p)
    events.sort(key=lambda r: (r.get("block") or 0, r.get("log_index") or 0), reverse=True)
    return {
        "events": events[:80],
        "n_logs": n_logs,
        "errors": errors[:8],
        "from_block": from_block,
        "to_block": to_block,
    }
