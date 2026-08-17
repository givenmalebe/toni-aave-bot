"""Spark Lend — Aave V3-shaped Pool. Live send: Aave flash → Spark liquidationCall."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import liquidation_bot as lb

from . import executor as ex
from . import util as u

ID = "spark"
LABEL = "Spark"
# Official SparkLend Pool proxy (Ethereum). Verified ChainSecurity + spark.fi docs.
POOL = "0xC13e21B648A5Ee794902342038FF3aDAB66BE987"
# Fallback oracle if ADDRESSES_PROVIDER → getPriceOracle fails (docs: Spark Oracle).
ORACLE_FALLBACK = "0x8105f69D9C41644c6A0803fDA7D03Aa70996cFD9"

_oracle_cache = {"ts": 0, "addr": ""}
_reserves_cache = {"ts": 0, "addrs": []}


def enabled() -> bool:
    if not u.has_code(POOL):
        return False
    try:
        _oracle()
    except Exception:
        pass
    return True


def _oracle() -> str:
    now = __import__("time").time()
    if _oracle_cache["addr"] and now - _oracle_cache["ts"] < 600:
        return _oracle_cache["addr"]
    addr = ""
    try:
        raw = u.call(POOL, u.SEL["ADDRESSES_PROVIDER"])
        provider = u.addr_word(u.words(raw)[0]) if u.words(raw) else ""
        if provider and int(provider, 16):
            raw2 = u.call(provider, u.SEL["getPriceOracle"])
            o = u.addr_word(u.words(raw2)[0]) if u.words(raw2) else ""
            if o and int(o, 16):
                addr = o
    except Exception:
        addr = ""
    if not addr:
        addr = ORACLE_FALLBACK.lower()
        if not u.has_code(addr):
            addr = lb.V3_ORACLE.lower()
    _oracle_cache["ts"] = now
    _oracle_cache["addr"] = addr.lower()
    return _oracle_cache["addr"]


def get_reserves_list() -> list[str]:
    now = __import__("time").time()
    if _reserves_cache["addrs"] and now - _reserves_cache["ts"] < 600:
        return list(_reserves_cache["addrs"])
    try:
        raw = u.call(POOL, lb.SEL["getReservesList"])
        addrs = lb._decode_addr_array(raw)
    except Exception:
        addrs = []
    if addrs:
        _reserves_cache["ts"] = now
        _reserves_cache["addrs"] = addrs
    return list(addrs)


def _user_assets(user: str) -> tuple[list[str], list[str]]:
    try:
        raw = u.call(POOL, lb.SEL["getUserConfiguration"] + u.pad_addr(user)[2:])
    except Exception:
        return [], []
    ws = u.words(raw)
    if not ws:
        return [], []
    if len(ws) >= 2 and int(ws[0], 16) == 32:
        data = int(ws[1], 16)
    else:
        data = int(ws[0], 16)
    reserves = get_reserves_list()
    if not reserves or not data:
        return [], []
    colls, debts = [], []
    for i, asset in enumerate(reserves):
        if (data >> (2 * i)) & 1:
            colls.append(asset)
        if (data >> (2 * i + 1)) & 1:
            debts.append(asset)
    return colls, debts


def _pair(user: str) -> dict:
    colls, debts = _user_assets(user)
    coll = lb._pick_preferred(colls, lb._PREFERRED_COLL) if colls else ""
    debt = lb._pick_preferred(debts, lb._PREFERRED_DEBT) if debts else ""
    return {
        "coll_addr": coll,
        "debt_addr": debt,
        "coll_sym": lb.token_sym(coll) if coll else "?",
        "debt_sym": lb.token_sym(debt) if debt else "?",
    }


def _account(user: str) -> dict | None:
    try:
        raw = u.call(POOL, lb.SEL["getUserAccountData"] + u.pad_addr(user)[2:])
    except Exception:
        return None
    return lb._parse_account_raw(raw, "v3")


def harvest_users(from_block: int, to_block: int) -> dict:
    users: dict[str, int] = {}
    n_logs = 0
    errors = []
    for topic in (lb.V3_BORROW_TOPIC, lb.V3_LIQ_TOPIC):
        logs, err = lb.get_logs_chunked(from_block, to_block, POOL, [topic])
        errors.extend(err)
        n_logs += len(logs)
        idx = 2 if topic == lb.V3_BORROW_TOPIC else 3
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


def _score(acc: dict, pair: dict, gas_gwei: float, eth_usd: float) -> dict | None:
    hf = int(acc.get("hf") or 0)
    hf_f = hf / 1e18 if hf and hf < (1 << 200) else 999.0
    if hf >= lb.HEALTH_THRESHOLD:
        return None
    debt_usd = float(acc.get("debt_usd") or 0)
    coll_usd = float(acc.get("coll_usd") or 0)
    if debt_usd < u.DUST_USD:
        return None
    close = 1.0 if hf_f < u.CLOSE_FACTOR_HF else 0.5
    bonus = u.DEFAULT_BONUS
    proto_fee = 0.0
    coll = pair.get("coll_addr") or ""
    if coll:
        cfg = u.get_reserve_config(POOL, coll)
        if cfg.get("liq_bonus") is not None:
            bonus = float(cfg["liq_bonus"])
        proto_fee = float(cfg.get("protocol_fee") or 0.0)
    cover = min(debt_usd * close, max(coll_usd / (1.0 + bonus), 0))
    bonus_usd = cover * bonus
    proto_usd = bonus_usd * proto_fee
    gas = u.gas_usd(gas_gwei, eth_usd)
    extra = u.apply_flash_fee(cover, bonus_usd, proto_usd, gas)
    if extra["net_usd"] <= 0:
        return None
    return {
        "hf": str(hf),
        "coll_usd": round(coll_usd, 2),
        "debt_usd": round(debt_usd, 2),
        "cover_usd": round(cover, 2),
        "close_factor": close,
        "bonus_usd": round(bonus_usd, 2),
        "gas_usd": round(gas, 2),
        "liquidatable": True,
        **extra,
        **pair,
        "collateralAsset": pair.get("coll_addr") or "",
        "debtAsset": pair.get("debt_addr") or "",
    }


def scan_liquidatable(users, *, gas_gwei: float, eth_usd: float,
                      contested=None, recent_comp=None, max_users: int = 80) -> dict:
    uniq = u.uniq_addrs(list(users or []) + u.load_users(ID), max_users)
    contested = {str(x).lower() for x in (contested or [])}
    recent_comp = {str(x).lower() for x in (recent_comp or [])}

    def one(usr):
        try:
            return usr, _account(usr), None
        except Exception as e:  # noqa: BLE001
            return usr, None, str(e)[:80]

    rows = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for item in ex.map(one, uniq):
            rows.append(item)

    opps, watch, skipped = [], [], {
        "dust": 0, "healthy": 0, "no_account": 0, "negative": 0, "rpc": 0,
    }
    keep = []
    for usr, acc, err in rows:
        if err:
            skipped["rpc"] += 1
            continue
        if not acc:
            skipped["no_account"] += 1
            continue
        hf = int(acc.get("hf") or 0)
        keep.append(usr)
        watch.append({
            "user": usr,
            "hf": str(hf),
            "coll_usd": round(float(acc.get("coll_usd") or 0), 2),
            "debt_usd": round(float(acc.get("debt_usd") or 0), 2),
            "protocol_id": ID,
            "protocol": LABEL,
        })
        if float(acc.get("debt_usd") or 0) < u.DUST_USD:
            skipped["dust"] += 1
            continue
        if hf >= lb.HEALTH_THRESHOLD:
            skipped["healthy"] += 1
            continue
        pair = _pair(usr)
        scored = _score(acc, pair, gas_gwei, eth_usd)
        if not scored:
            skipped["negative"] += 1
            continue
        race = usr in contested or usr in recent_comp
        rec = u.opp_base(protocol_id=ID, protocol_label=LABEL, user=usr, live_ok=True)
        rec.update(scored)
        rec["contested"] = race
        rec["recent_competitor"] = usr in recent_comp
        rec["race"] = race
        rec["source"] = "sweep"
        rec["venue"] = "spark-pool"
        rec["collateralAsset"] = pair.get("coll_addr") or rec.get("collateralAsset") or ""
        rec["debtAsset"] = pair.get("debt_addr") or rec.get("debtAsset") or ""
        rec["debtToCover"] = str((1 << 256) - 1)
        fp = ex.plan_aave_like(
            pool=POOL,
            user=usr,
            coll=rec["collateralAsset"],
            debt=rec["debtAsset"],
            cover_usd=float(rec.get("cover_usd") or 0),
        )
        ex.apply_plan(rec, fp)
        opps.append(rec)
    watch.sort(key=lambda r: int(r.get("hf") or 0))
    u.save_users(ID, keep + uniq[:40])
    return {
        "opps": opps,
        "watch": watch[:12],
        "scanned": len(uniq),
        "skipped": skipped,
    }


def scan_competitor_logs(from_block: int, to_block: int, extra_addrs=None) -> dict:
    logs, errors = lb.get_logs_chunked(from_block, to_block, POOL, [lb.V3_LIQ_TOPIC])
    events = []
    for lg in logs:
        p = lb.parse_v3_liq_log(lg)
        if not p:
            continue
        p["protocol_id"] = ID
        p["protocol"] = LABEL
        p["spoke"] = POOL.lower()
        events.append(p)
    return {
        "events": events,
        "n_logs": len(logs),
        "errors": errors[:8],
        "from_block": from_block,
        "to_block": to_block,
    }
