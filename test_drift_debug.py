"""Debug Drift GPA filter behavior."""
import os, json, urllib.request, base64

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, ".env")) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

rpc = os.environ.get("SOLANA_RPC", "")

def post(body, timeout=20):
    req = urllib.request.Request(rpc, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())

DRIFT = "dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH"
disc = b"\xd0\xc0\xda\xa4\x17\x15\x14\x0b"
import base58
disc_b58 = base58.b58encode(disc).decode()
print(f"Discriminator: {disc.hex()}")
print(f"Discriminator (base58): {disc_b58}")

# Test 1: dataSize only
print("\n--- Test 1: dataSize=4376 only ---")
d = post({"jsonrpc":"2.0","id":1,"method":"getProgramAccountsV2",
    "params":[DRIFT, {"encoding":"base64","limit":3,"filters":[{"dataSize":4376}]}]})
accts = d.get("result",{}).get("accounts",[])
print(f"  Results: {len(accts)}")
if accts:
    raw = base64.b64decode((accts[0].get("account",{}).get("data",[""]))[0])
    print(f"  First 8 bytes: {raw[:8].hex()}")
    print(f"  Expected disc: {disc.hex()}")
    print(f"  Match: {raw[:8] == disc}")

# Test 2: dataSize + memcmp(base58) at offset 0
print("\n--- Test 2: dataSize=4376 + memcmp(base58) ---")
d = post({"jsonrpc":"2.0","id":2,"method":"getProgramAccountsV2",
    "params":[DRIFT, {"encoding":"base64","limit":3,"filters":[
        {"dataSize":4376},
        {"memcmp":{"offset":0,"bytes":disc_b58}}
    ]}]})
accts = d.get("result",{}).get("accounts",[])
print(f"  Results: {len(accts)}")
if accts:
    raw = base64.b64decode((accts[0].get("account",{}).get("data",[""]))[0])
    print(f"  First 8 bytes: {raw[:8].hex()}")

# Test 3: dataSize + memcmp(base64) at offset 0
print("\n--- Test 3: dataSize=4376 + memcmp(base64) ---")
disc_b64 = base64.b64encode(disc).decode()
d = post({"jsonrpc":"2.0","id":3,"method":"getProgramAccountsV2",
    "params":[DRIFT, {"encoding":"base64","limit":3,"filters":[
        {"dataSize":4376},
        {"memcmp":{"offset":0,"bytes":disc_b64,"encoding":"base64"}}
    ]}]})
accts = d.get("result",{}).get("accounts",[])
print(f"  Results: {len(accts)}")
if accts:
    raw = base64.b64decode((accts[0].get("account",{}).get("data",[""]))[0])
    print(f"  First 8 bytes: {raw[:8].hex()}")

# Test 4: Check what helius returns for the error
print("\n--- Test 4: Check raw response ---")
d = post({"jsonrpc":"2.0","id":4,"method":"getProgramAccountsV2",
    "params":[DRIFT, {"encoding":"base64","limit":3,"filters":[
        {"dataSize":4376},
        {"memcmp":{"offset":0,"bytes":disc_b58}}
    ]}]})
print(f"  Keys: {list(d.keys())}")
if "error" in d:
    print(f"  Error: {d['error']}")
if "result" in d:
    r = d["result"]
    if isinstance(r, dict):
        print(f"  Result keys: {list(r.keys())}")
        print(f"  Accounts: {len(r.get('accounts', []))}")
        print(f"  Context: {r.get('context', {})}")
    else:
        print(f"  Result type: {type(r)}")
