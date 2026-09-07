"""Debug sol_gpa directly."""
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

def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read())

# Test 1: Standard GPA on Helius with Kamino + filters
print("\n--- Test 1: Standard GPA (Kamino, dataSize=1300) ---")
body = {
    "jsonrpc": "2.0", "id": 1,
    "method": "getProgramAccounts",
    "params": ["KLend2g3cP87ber8CnQqYde3Hf7V1UoT3H6B712YU9s",
               {"encoding": "base64", "withContext": True,
                "filters": [{"dataSize": 1300}]}]
}
try:
    d = post(rpc, body)
    r = d.get("result", {})
    val = r.get("value") if isinstance(r, dict) and "value" in r else r
    print(f"  Error: {d.get('error')}, Accounts: {len(val) if isinstance(val, list) else type(val)}")
except Exception as e:
    print(f"  Exception: {e}")

# Test 2: getProgramAccountsV2 on Helius with Kamino
print("\n--- Test 2: V2 GPA (Kamino, limit=10) ---")
body = {
    "jsonrpc": "2.0", "id": 2,
    "method": "getProgramAccountsV2",
    "params": ["KLend2g3cP87ber8CnQqYde3Hf7V1UoT3H6B712YU9s",
               {"encoding": "base64", "limit": 10}]
}
try:
    d = post(rpc, body)
    r = d.get("result", {})
    accts = r.get("accounts", [])
    cursor = r.get("cursor")
    print(f"  Error: {d.get('error')}, Accounts: {len(accts)}, Cursor: {cursor}")
    if accts:
        print(f"  First: {accts[0].get('pubkey', '?')[:30]}...")
except Exception as e:
    print(f"  Exception: {e}")

# Test 3: V2 with filters
print("\n--- Test 3: V2 GPA (Kamino, dataSize=1300, limit=10) ---")
body = {
    "jsonrpc": "2.0", "id": 3,
    "method": "getProgramAccountsV2",
    "params": ["KLend2g3cP87ber8CnQqYde3Hf7V1UoT3H6B712YU9s",
               {"encoding": "base64", "limit": 10,
                "filters": [{"dataSize": 1300}]}]
}
try:
    d = post(rpc, body)
    r = d.get("result", {})
    accts = r.get("accounts", [])
    cursor = r.get("cursor")
    print(f"  Error: {d.get('error')}, Accounts: {len(accts)}, Cursor: {cursor}")
    if accts:
        print(f"  First: {accts[0].get('pubkey', '?')[:30]}...")
except Exception as e:
    print(f"  Exception: {e}")

# Test 4: V2 MarginFi
print("\n--- Test 4: V2 GPA (MarginFi, limit=10, dataSize=4368) ---")
body = {
    "jsonrpc": "2.0", "id": 4,
    "method": "getProgramAccountsV2",
    "params": ["MFv2hPWmiPmBDfspyx3iGx43s2TRBDfJsEAFLA2ZCRj",
               {"encoding": "base64", "limit": 10,
                "filters": [{"dataSize": 4368}]}]
}
try:
    d = post(rpc, body)
    r = d.get("result", {})
    accts = r.get("accounts", [])
    cursor = r.get("cursor")
    print(f"  Error: {d.get('error')}, Accounts: {len(accts)}, Cursor: {cursor}")
    if accts:
        print(f"  First: {accts[0].get('pubkey', '?')[:30]}...")
except Exception as e:
    print(f"  Exception: {e}")

# Test 5: Drift with correct ID (check if program ID is wrong)
print("\n--- Test 5: V2 GPA (Drift, limit=10) ---")
body = {
    "jsonrpc": "2.0", "id": 5,
    "method": "getProgramAccountsV2",
    "params": ["dRiftyHA39mcWEtFyxxDne2iDySbtPwfXSxnSvRePAKL",
               {"encoding": "base64", "limit": 10}]
}
try:
    d = post(rpc, body)
    r = d.get("result", {})
    accts = r.get("accounts", [])
    cursor = r.get("cursor")
    print(f"  Error: {d.get('error')}, Accounts: {len(accts)}, Cursor: {cursor}")
    if accts:
        print(f"  First: {accts[0].get('pubkey', '?')[:30]}...")
except Exception as e:
    print(f"  Exception: {e}")
