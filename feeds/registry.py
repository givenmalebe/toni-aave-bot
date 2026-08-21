"""Event-to-position registries for both chains."""
import threading


def build_eth_registry(watchlist: list[dict],
                       feed_for_asset: dict[str, str]) -> dict[str, list[dict]]:
    reg: dict[str, list[dict]] = {}
    fmap = {k.lower(): v for k, v in (feed_for_asset or {}).items()}
    for w in watchlist or []:
        asset = (w.get("collateral") or "").lower()
        feed = fmap.get(asset)
        if not feed:
            continue
        rec = {
            "user": w.get("user", ""),
            "asset": asset,
            "side": w.get("side", "collateral"),
            "hf": float(w.get("hf") or 999.0),
            "coll_usd": float(w.get("coll_usd") or 0.0),
            "debt_usd": float(w.get("debt_usd") or 0.0),
            "liq_threshold": float(w.get("liq_threshold") or 0.8),
        }
        reg.setdefault(feed.lower(), []).append(rec)
    return reg


def affected(registry: dict[str, list[dict]], feed_addr: str) -> list[dict]:
    return registry.get((feed_addr or "").lower(), [])


def recomputed_hf(pos: dict, price_ratio: float) -> float:
    if price_ratio <= 0:
        price_ratio = 1.0
    coll = pos["coll_usd"] * (price_ratio if pos.get("side") == "collateral" else 1.0)
    debt = pos["debt_usd"]
    if debt <= 0:
        return 999.0
    return (coll * pos["liq_threshold"]) / debt


def build_sol_shards(obligations: list[str], n: int) -> list[list[str]]:
    from feeds.common import shard
    return shard(list(obligations or []), n)
