import os, json, urllib.request, time

# Load .env
HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, ".env")) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

rpc = os.environ.get("SOLANA_RPC", "")
print(f"RPC: {rpc[:60]}...")

programs = {
    "Kamino": "KLend2g3cP87ber8CnQqYde3Hf7V1UoT3H6B712YU9s",
    "MarginFi": "MFv2hPWmiPmBDfspyx3iGx43s2TRBDfJsEAFLA2ZCRj",
    "Drift": "dRiftyHA39mcWEtFyxxDne2iDySbtPwfXSxnSvRePAKL",
}

for name, pid in programs.items():
    print(f"\n--- {name} ({pid[:8]}...) ---")
    body = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getProgramAccounts",
        "params": [pid, {"encoding": "base64", "withContext": True}]
    }
    req = urllib.request.Request(
        rpc,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        data = json.loads(resp.read())
        elapsed = time.time() - t0
        err = data.get("error")
        if err:
            print(f"  ERROR: {err}")
        else:
            r = data.get("result", {})
            val = r.get("value") if isinstance(r, dict) and "value" in r else r
            if isinstance(val, list):
                print(f"  Accounts: {len(val)}  ({elapsed:.1f}s)")
                if val:
                    print(f"  First pubkey: {val[0].get('pubkey', '?')[:20]}...")
            else:
                print(f"  Unexpected type: {type(val)}  ({elapsed:.1f}s)")
                print(f"  Raw: {str(data)[:200]}")
    except Exception as e:
        print(f"  Exception: {e}")
