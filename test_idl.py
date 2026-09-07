"""Fetch the klend IDL from chain and decode the Obligation struct layout."""
import os, json, urllib.request, hashlib, base64

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

# Compute Anchor discriminator for "Obligation"
disc_input = b"account:Obligation"
disc_hash = hashlib.sha256(disc_input).digest()[:8]
print(f"Expected Obligation discriminator: {disc_hash.hex()}")

# Get the klend program data to find the IDL
# In Anchor, the IDL is stored in a PDA or in the program data
# Let's try fetching the IDL account
KAMINO_PROGRAM = "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD"

# Fetch the program account to get the IDL address
body = {
    "jsonrpc": "2.0", "id": 1,
    "method": "getAccountInfo",
    "params": [KAMINO_PROGRAM, {"encoding": "jsonParsed"}]
}
d = post(body)
acc = d.get("result", {}).get("value", {})
program_data = (acc.get("data", {}).get("parsed", {}).get("info", {}).get("data", {}).get("address", ""))
print(f"Program data: {program_data}")

# The IDL account PDA is derived as: sha256("anchor:idl")[0..32] + program_id
# For newer Anchor versions: anchor_idl_account = find_program_address(["anchor:idl"], program_id)
# Let's try common IDL account locations
idl_disc = hashlib.sha256(b"anchor:idl").digest()
# IDL PDA: [idl_disc, program_id] seeds
import struct

# Try fetching a known IDL-like account near the program
# Actually, let's just get a 3344-byte obligation and try to figure out the offsets

# Fetch a 3344-byte obligation with activity (non-zero deposits)
d = post({"jsonrpc":"2.0","id":2,"method":"getProgramAccountsV2",
    "params":[KAMINO_PROGRAM,
              {"encoding":"base64","limit":5,"filters":[
                  {"dataSize":3344},
                  {"memcmp":{"offset":32,"bytes":"7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF"}}
              ]}]})
accts = d.get("result",{}).get("accounts",[])
print(f"\nFound {len(accts)} main-market 3344-byte obligations")

for i, acc in enumerate(accts[:3]):
    raw = base64.b64decode((acc.get("account",{}).get("data",[""]))[0])
    print(f"\n--- Obligation {i} ({acc['pubkey'][:16]}...) ---")
    
    disc = raw[:8]
    print(f"  Discriminator match: {disc == disc_hash} ({disc.hex()})")
    
    # Layout analysis
    # [0-8] discriminator
    # [8] version
    # [9] tag
    # [10-16] padding (6 bytes to align u64)
    # [16-24] last_update.slot (u64 LE)
    # [24] last_update.stale (bool)
    # [25-32] padding + elevation_group?
    # [32-64] lending_market (Pubkey)
    # [64-96] owner (Pubkey)
    
    version = raw[8]
    tag = raw[9]
    slot = struct.unpack_from('<Q', raw, 16)[0]
    stale = raw[24]
    elev_group = raw[25]
    print(f"  version={version} tag={tag} slot={slot} stale={stale} elev={elev_group}")
    
    # After owner at offset 96, what follows?
    # Check if deposited_value (u128) is at 96
    # Actually, Anchor zero_copy: after owner(Pubkey=32), the next field 
    # needs 8-byte alignment. 96 is 8-byte aligned.
    # So deposited_value(u128) should be at [96:112]
    # borrowed_value(u128) at [112:128]
    # allowed_borrow_value(u128) at [128:144]
    # unhealthy_borrow_value(u128) at [144:160]
    
    deposited = int.from_bytes(raw[96:112], 'little')
    borrowed = int.from_bytes(raw[112:128], 'little')
    allowed = int.from_bytes(raw[128:144], 'little')
    unhealthy = int.from_bytes(raw[144:160], 'little')
    
    print(f"  deposited_value: {deposited}")
    print(f"  borrowed_value: {borrowed}")
    print(f"  allowed_borrow: {allowed}")
    print(f"  unhealthy_borrow: {unhealthy}")
    
    if borrowed > 0:
        hf = unhealthy / borrowed if unhealthy > 0 else deposited / borrowed
        print(f"  HF = {hf:.4f}")
    
    # Also try different offsets in case the layout is different
    # Check [96:200] as hex
    print(f"  Raw [96:160]: {raw[96:160].hex()}")
    
    # Check after the 4 health values, what follows?
    # deposits Vec<ObligationCollateral> starts at [160:...]
    # Vec prefix (u32 LE) at [160:164]
    deposits_len = struct.unpack_from('<I', raw, 160)[0]
    borrows_len = struct.unpack_from('<I', raw, 164)[0] if 164 < len(raw) else 0
    print(f"  deposits_vec_len (at 160): {deposits_len}")
    print(f"  borrows_vec_len (at 164?): {borrows_len}")
