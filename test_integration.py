"""Integration test: run all three SOL multi-protocol adapters against live Helius RPC."""
import os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Load .env before anything else
with open(os.path.join(HERE, ".env")) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

from sol_lending import kamino, marginfi, drift

print("=" * 60)
print("MULTI-PROTOCOL ADAPTER INTEGRATION TEST")
print("=" * 60)

results = {}

for name, mod in [("kamino", kamino), ("marginfi", marginfi), ("drift", drift)]:
    print(f"\n--- {name.upper()} ---")
    t0 = time.time()
    try:
        r = mod.scan_obligations(max_accounts=30)
        elapsed = time.time() - t0
        print(f"  Time: {elapsed:.1f}s")
        print(f"  OK: {r.get('ok')}")
        print(f"  Probed: {r.get('probed')}")
        print(f"  Hydrated: {r.get('hydrated')}")
        print(f"  Opportunities (HF<1): {len(r.get('opportunities', []))}")
        print(f"  Watch (HF<1.15): {len(r.get('watch', []))}")
        if r.get("errors"):
            print(f"  Errors: {r['errors'][:3]}")

        if r.get("watch"):
            print(f"  Sample watch items:")
            for item in r["watch"][:3]:
                print(f"    HF={item.get('hf'):.4f} "
                      f"coll=${item.get('coll_usd') or item.get('assets_raw', '?'):.2f} "
                      f"debt=${item.get('debt_usd') or item.get('liabs_raw', '?'):.2f}")

        results[name] = r
    except Exception as e:
        print(f"  EXCEPTION: {e}")
        results[name] = {"ok": False, "error": str(e)}

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
for name, r in results.items():
    status = "PASS" if r.get("ok") else "FAIL"
    probed = r.get("probed", 0)
    hydrated = r.get("hydrated", 0)
    opps = len(r.get("opportunities", []))
    print(f"  {name.upper()}: {status} | probed={probed} hydrated={hydrated} opps={opps}")
