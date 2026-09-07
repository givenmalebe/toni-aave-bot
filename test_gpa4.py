"""Direct V2 GPA test with correct program IDs."""
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

def post(url, body, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())

# Kamino - correct program ID, V2 with pagination
print("\n--- Kamino V2 GPA (limit=10) ---")
body = {
    "jsonrpc": "2.0", "id": 1,
    "method": "getProgramAccountsV2",
    "params": ["KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD",
               {"encoding": "base64", "limit": 10}]
}
try:
    d = post(rpc, body, timeout=30)
    r = d.get("result", {})
    accts = r.get("accounts", [])
    cursor = r.get("cursor")
    print(f"  Error: {d.get('error')}, Accounts: {len(accts)}, Cursor: {'yes' if cursor else 'no'}")
    if accts:
        print(f"  First: {accts[0].get('pubkey', '?')[:30]}...")
except Exception as e:
    print(f"  Exception: {e}")

# MarginFi - correct program ID, V2 with dataSize filter
print("\n--- MarginFi V2 GPA (dataSize=2304, limit=10) ---")
body = {
    "jsonrpc": "2.0", "id": 2,
    "method": "getProgramAccountsV2",
    "params": ["MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA",
               {"encoding": "base64", "limit": 10,
                "filters": [{"dataSize": 2304}]}]
}
try:
    d = post(rpc, body, timeout=30)
    r = d.get("result", {})
    accts = r.get("accounts", [])
    cursor = r.get("cursor")
    print(f"  Error: {d.get('error')}, Accounts: {len(accts)}, Cursor: {'yes' if cursor else 'no'}")
    if accts:
        print(f"  First: {accts[0].get('pubkey', '?')[:30]}...")
except Exception as e:
    print(f"  Exception: {e}")

# MarginFi - no filters at all
print("\n--- MarginFi V2 GPA (no filters, limit=5) ---")
body = {
    "jsonrpc": "2.0", "id": 3,
    "method": "getProgramAccountsV2",
    "params": ["MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA",
               {"encoding": "base64", "limit": 5}]
}
try:
    d = post(rpc, body, timeout=30)
    r = d.get("result", {})
    accts = r.get("accounts", [])
    cursor = r.get("cursor")
    print(f"  Error: {d.get('error')}, Accounts: {len(accts)}, Cursor: {'yes' if cursor else 'no'}")
    if accts:
        print(f"  First: {accts[0].get('pubkey', '?')[:30]}...")
        print(f"  Account data length: {len(accts[0].get('account', {}).get('data', [''])[0]) if accts[0].get('account') else 'N/A'}")
except Exception as e:
    print(f"  Exception: {e}")

# Standard (non-V2) GPA with correct Kamino ID, no filters
print("\n--- Kamino standard GPA (no filters, withContext) ---")
body = {
    "jsonrpc": "2.0", "id": 4,
    "method": "getProgramAccounts",
    "params": ["KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD",
               {"encoding": "base64", "withContext": True}]
}
try:
    d = post(rpc, body, timeout=60)
    r = d.get("result", {})
    val = r.get("value") if isinstance(r, dict) and "value" in r else r
    print(f"  Error: {d.get('error')}, Accounts: {len(val) if isinstance(val, list) else type(val)}")
except Exception as e:
    print(f"  Exception: {e}")
