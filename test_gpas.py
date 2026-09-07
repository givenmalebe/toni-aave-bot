import sol_scanner as sols
import base64
import json

# Kamino
KAMINO_PROGRAM = "KLend2g3cP87ber8CnQqYde3Hf7V1UoT3H6B712YU9s"
KAMINO_MAIN_MARKET = "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF"

# Try without market filter first
print("=== Kamino (no market filter, dataSize=1300) ===")
try:
    result, url = sols.sol_rpc("getProgramAccounts", [
        KAMINO_PROGRAM,
        {"encoding": "base64", "filters": [{"dataSize": 1300}], "commitment": "confirmed"}
    ], timeout=15.0)
    print("  URL: %s" % (url or "none"))
    print("  Result type: %s, len: %s" % (type(result), len(result) if result else 0))
    if result:
        for acc in result[:3]:
            pk = acc.get("pubkey", "")
            data_b64 = ((acc.get("account") or {}).get("data") or [""])[0]
            raw = base64.b64decode(data_b64) if data_b64 else b""
            print("  %s data_len=%d" % (pk[:12], len(raw)))
except Exception as e:
    print("  ERROR: %s" % e)

print()
print("=== Kamino (with market filter, dataSize=1300) ===")
try:
    result, url = sols.sol_rpc("getProgramAccounts", [
        KAMINO_PROGRAM,
        {"encoding": "base64", "filters": [
            {"dataSize": 1300},
            {"memcmp": {"offset": 11, "bytes": KAMINO_MAIN_MARKET}}
        ], "commitment": "confirmed"}
    ], timeout=15.0)
    print("  Result len: %s" % (len(result) if result else 0))
except Exception as e:
    print("  ERROR: %s" % e)

# Check all data sizes
print()
print("=== Kamino (all data sizes, no market filter) ===")
for ds in [1300, 1456, 1824, 2200]:
    try:
        result, _ = sols.sol_rpc("getProgramAccounts", [
            KAMINO_PROGRAM,
            {"encoding": "base64", "filters": [{"dataSize": ds}], "commitment": "confirmed"}
        ], timeout=15.0)
        n = len(result) if result else 0
        print("  dataSize=%d: %d accounts" % (ds, n))
    except Exception as e:
        print("  dataSize=%d: ERROR %s" % (ds, e))

# MarginFi
MARGINFI_PROGRAM = "MFv2hPWmiPmBDfspyx3iGx43s2TRBDfJsEAFLA2ZCRj"
print()
print("=== MarginFi (dataSize=2520) ===")
try:
    result, url = sols.sol_rpc("getProgramAccounts", [
        MARGINFI_PROGRAM,
        {"encoding": "base64", "filters": [{"dataSize": 2520}], "commitment": "confirmed"}
    ], timeout=15.0)
    print("  Result len: %s" % (len(result) if result else 0))
    if result:
        for acc in result[:3]:
            pk = acc.get("pubkey", "")
            data_b64 = ((acc.get("account") or {}).get("data") or [""])[0]
            raw = base64.b64decode(data_b64) if data_b64 else b""
            disc = raw[:8].hex() if raw else ""
            print("  %s data_len=%d disc=%s" % (pk[:12], len(raw), disc))
except Exception as e:
    print("  ERROR: %s" % e)

# Drift
DRIFT_PROGRAM = "dRiftyHA39mcWEtFyxxDne2iDySbtPwfXSxnSvRePAKL"
print()
print("=== Drift (dataSize=4376) ===")
try:
    result, url = sols.sol_rpc("getProgramAccounts", [
        DRIFT_PROGRAM,
        {"encoding": "base64", "filters": [{"dataSize": 4376}], "commitment": "confirmed"}
    ], timeout=15.0)
    print("  Result len: %s" % (len(result) if result else 0))
    if result:
        for acc in result[:3]:
            pk = acc.get("pubkey", "")
            data_b64 = ((acc.get("account") or {}).get("data") or [""])[0]
            raw = base64.b64decode(data_b64) if data_b64 else b""
            disc = raw[:8].hex() if raw else ""
            print("  %s data_len=%d disc=%s" % (pk[:12], len(raw), disc))
except Exception as e:
    print("  ERROR: %s" % e)
