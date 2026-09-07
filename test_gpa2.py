import os, json, urllib.request, time

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, ".env")) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

rpc = os.environ.get("SOLANA_RPC", "")
print(f"RPC: {rpc}")

def gpatest(label, pid, filters=None):
    print(f"\n--- {label} ---")
    params = [pid, {"encoding": "base64", "withContext": True}]
    if filters:
        params[1]["filters"] = filters
    body = {"jsonrpc": "2.0", "id": 1, "method": "getProgramAccounts", "params": params}
    req = urllib.request.Request(rpc, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
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
                if val[:3]:
                    for a in val[:3]:
                        print(f"    {a.get('pubkey', '?')[:24]}...")
            else:
                print(f"  Result: {str(val)[:100]}  ({elapsed:.1f}s)")
    except Exception as e:
        print(f"  Exception ({time.time()-t0:.1f}s): {e}")

# 1) Test GPA works at all - use Token Program (spl-token, very large)
gpatest("SPL Token (sanity check)", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        [{"dataSize": 165}])

# 2) Kamino - full unfiltered GPA
gpatest("Kamino (unfiltered)", "KLend2g3cP87ber8CnQqYde3Hf7V1UoT3H6B712YU9s")

# 3) Kamino obligation size filter (obligation account = 1616 bytes)
gpatest("Kamino (dataSize=1616)", "KLend2g3cP87ber8CnQqYde3Hf7V1UoT3H6B712YU9s",
        [{"dataSize": 1616}])

# 4) MarginFi - full unfiltered
gpatest("MarginFi (unfiltered)", "MFv2hPWmiPmBDfspyx3iGx43s2TRBDfJsEAFLA2ZCRj")

# 5) MarginFi account size (MarginfiAccount = 4368 bytes)
gpatest("MarginFi (dataSize=4368)", "MFv2hPWmiPmBDfspyx3iGx43s2TRBDfJsEAFLA2ZCRj",
        [{"dataSize": 4368}])

# 6) Drift - use memcmp on discriminator only
gpatest("Drift (unfiltered)", "dRiftyHA39mcWEtFyxxDne2iDySbtPwfXSxnSvRePAKL")
