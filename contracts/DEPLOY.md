# Deploy GenericFlashLiquidator (Ethereum mainnet)

One contract covers **Aave V3 (old 4-arg)**, **Spark**, **Compound III**, and **Morpho Blue**.
Flash cash always comes from Aave V3 (~9 bps). Constructor takes **no addresses**.

You do **not** deploy 4 contracts. You do **not** deploy a Solana program for live SOL (that path is Python + Jito).

## Before you start

1. Install Foundry if `forge` is missing:
   - Windows PowerShell: `irm https://foundry.paradigm.xyz | iex` then close/reopen the terminal and run `foundryup`
   - Mac/Linux: `curl -L https://foundry.paradigm.xyz | bash` then `foundryup`
2. You need a funded Ethereum keystore (ETH for gas). Same names the bot already uses:
   - `KEYSTORE_PATH` = path to the encrypted UTC json
   - `KEYSTORE_PW` = password for that file
3. Open the dashboard **Funds** card and copy the **bot** address. That is the bot EOA. Profits from this contract go to `owner`. Owner should be that bot address.

## Numbered go-live

### 1. Deploy

**Windows PowerShell** (from this `contracts` folder):

```powershell
cd C:\Users\Surf\Documents\toni-aave-bot\contracts
$env:KEYSTORE_PATH = "C:\path\to\your\keystore.json"
$env:KEYSTORE_PW = "your-keystore-password"
# optional: $env:BOT_EOA = "0xYourBotAddressFromFundsCard"
.\deploy_generic.ps1
```

Or the exact forge line (no constructor args):

```powershell
forge create GenericFlashLiquidator.sol:GenericFlashLiquidator `
  --rpc-url https://ethereum-rpc.publicnode.com `
  --keystore $env:KEYSTORE_PATH `
  --password $env:KEYSTORE_PW `
  --broadcast
```

**Git Bash / WSL / Mac / Linux:**

```bash
cd contracts
export KEYSTORE_PATH=/path/to/keystore.json
export KEYSTORE_PW='your-keystore-password'
# optional: export BOT_EOA=0xYourBotAddressFromFundsCard
./deploy_generic.sh
```

Copy the **Deployed to** address. Check it:

```powershell
cast call 0xYOUR_NEW_CONTRACT "KIND()(bytes32)" --rpc-url https://ethereum-rpc.publicnode.com
```

You want:

`0x8caa2b9a42135cb026f57f48dfc7f1d565f83039807016026fa2fdfe883d27d1`

### 2. Paste env, restart dashboard

Copy repo-root `.env.example` → `.env` (never commit `.env`). Set:

```
LIQ_GENERIC_CONTRACT=0xYOUR_NEW_CONTRACT
```

You can point `LIQ_CONTRACT` at the **same** address (Aave 4-arg still works). If you already have an old Aave-only liquidator, leave `LIQ_CONTRACT` on that old address and only set `LIQ_GENERIC_CONTRACT`.

Restart `dashboard.py`. Spark / Compound / Morpho chips should stop saying “KIND() not on chain” once the probe matches.

### 3. Fund gas

- Keystore you deployed with: enough ETH to pay the deploy tx (~a few dollars of gas; variable).
- **Bot EOA**: ETH for live liquidation txs (dashboard Funds card). A common floor is ≈0.05 ETH; more is safer when gas spikes.
- Optional **sponsor** wallet (`SPONSOR_KEYSTORE` / `SPONSOR_PW`): extra ETH the bot can pull when the bot is light. Not required if the bot already holds gas.

MetaMask: send ETH on **Ethereum mainnet** to those addresses. No tokens needed on the contract itself — it flash-borrows.

### 4. If you did not deploy from the bot keystore — `setOwner`

If `forge create` used the **same** `KEYSTORE_PATH` as the bot, skip this. Owner is already the bot.

If you deployed from another wallet (example: MetaMask via a different keystore):

```powershell
cast send 0xYOUR_NEW_CONTRACT "setOwner(address)" 0xBOT_EOA `
  --keystore $env:KEYSTORE_PATH `
  --password $env:KEYSTORE_PW `
  --rpc-url https://ethereum-rpc.publicnode.com
```

Do this **once**, from the current owner. After that, leftover tokens/`recoverEth` go to the bot.

### 5. Arm Keep Live, Sim off

Dashboard Broadcast card:

1. Click **Arm LIVE** (that also turns Keep Live on).
2. Confirm **Keep Live** is ON.
3. Turn **Sim** OFF. Sim ON is the panic switch (no real sends).

Keep Live is saved in gitignored `broadcast_prefs.json`. The bot will **not** auto-arm if the keystore/password is missing.

## What you do **not** do

- Do not deploy a custom Solana program. SOL live is Python (Solend + Jupiter) + Jito.
- Do not set `ARB_CONTRACT` unless you also want **ETH DEX arb** live. Spark/Compound/Morpho liquidations do not need it.
- Do not commit `.env`, keystores, `broadcast_prefs.json`, `contracts.json`, or `solana/keys/bot.json`.
