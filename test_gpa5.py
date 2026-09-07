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
print(f"RPC: {rpc[:50]}...")

def post(url, body, timeout=20):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())

# Test 1: Kamino V2, no filters, limit=5
print("\n1. Kamino V2 (no filters, limit=5)")
try:
    d = post(rpc, {"jsonrpc":"2.0","id":1,"method":"getProgramAccountsV2",
        "params":["KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD",
                  {"encoding":"base64","limit":5}]})
    r = d.get("result",{})
    accts = r.get("accounts",[])
    print(f"  Error: {d.get('error')}, Accounts: {len(accts)}, Cursor: {r.get('cursor','none')}")
except Exception as e:
    print(f"  Exception: {e}")

# Test 2: MarginFi V2, dataSize=2304, limit=5
print("\n2. MarginFi V2 (dataSize=2304, limit=5)")
try:
    d = post(rpc, {"jsonrpc":"2.0","id":2,"method":"getProgramAccountsV2",
        "params":["MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA",
                  {"encoding":"base64","limit":5,"filters":[{"dataSize":2304}]}]})
    r = d.get("result",{})
    accts = r.get("accounts",[])
    print(f"  Error: {d.get('error')}, Accounts: {len(accts)}, Cursor: {r.get('cursor','none')}")
except Exception as e:
    print(f"  Exception: {e}")

# Test 3: Drift V2, dataSize=4376, limit=5
print("\n3. Drift V2 (dataSize=4376, limit=5)")
try:
    d = post(rpc, {"jsonrpc":"2.0","id":3,"method":"getProgramAccountsV2",
        "params":["dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH",
                  {"encoding":"base64","limit":5,"filters":[{"dataSize":4376}]}]})
    r = d.get("result",{})
    accts = r.get("accounts",[])
    print(f"  Error: {d.get('error')}, Accounts: {len(accts)}, Cursor: {r.get('cursor','none')}")
except Exception as e:
    print(f"  Exception: {e}")

# Test 4: Try without dataSize filter for Drift
print("\n4. Drift V2 (no filters, limit=5)")
try:
    d = post(rpc, {"jsonrpc":"2.0","id":4,"method":"getProgramAccountsV2",
        "params":["dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH",
                  {"encoding":"base64","limit":5}]})
    r = d.get("result",{})
    accts = r.get("accounts",[])
    print(f"  Error: {d.get('error')}, Accounts: {len(accts)}, Cursor: {r.get('cursor','none')}")
    if accts:
        acc = accts[0]
        data_b64 = (acc.get("account",{}).get("data",[""]))[0]
        print(f"  First account: {acc.get('pubkey','?')[:30]}, data_len={len(data_b64) if data_b64 else 0}")
except Exception as e:
    print(f"  Exception: {e}")

# Test 5: Kamino V2 with dataSize filter only
print("\n5. Kamino V2 (dataSize=1300, limit=5)")
try:
    d = post(rpc, {"jsonrpc":"2.0","id":5,"method":"getProgramAccountsV2",
        "params":["KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD",
                  {"encoding":"base64","limit":5,"filters":[{"dataSize":1300}]}]})
    r = d.get("result",{})
    accts = r.get("accounts",[])
    print(f"  Error: {d.get('error')}, Accounts: {len(accts)}, Cursor: {r.get('cursor','none')}")
except Exception as e:
    print(f"  Exception: {e}")

# Test 6: Verify RPC works at all - getTokenSupply
print("\n6. Sanity: getTokenSupply (USDC)")
try:
    d = post(rpc, {"jsonrpc":"2.0","id":6,"method":"getTokenSupply",
        "params":["EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"]})
    print(f"  Error: {d.get('error')}, Result: {d.get('result',{}).get('value',{}).get('uiAmountString','?')}")
except Exception as e:
    print(f"  Exception: {e}")
