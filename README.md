# toni-aave-bot

TONI live dashboard for Aave V4 (Ethereum) plus a Solana twin (Solend + Jupiter).

## Run

```bash
python3 -m pip install aiohttp requests pynacl base58
python3 dashboard.py --host 127.0.0.1 --port 8081 --broadcast
```

Open http://127.0.0.1:8081/

This folder expects sibling packages (same parent directory) used by `dashboard.py`:

- `aave-v4-liquidation-bot/`
- `aave-v4-monitor/`
- `defi-arb/`

ETH and SOL broadcast default to **sim-only**. Arm LIVE from the Broadcast card when wallets are funded.

## Spark / Compound / Morpho (one ETH contract)

Live path uses **one** contract: `contracts/GenericFlashLiquidator.sol` (Aave V3 4-arg + Spark pool + Compound comet + Morpho Blue). Constructor takes **no args**.

Numbered go-live: **[contracts/DEPLOY.md](contracts/DEPLOY.md)** — 1 deploy, 2 paste `LIQ_GENERIC_CONTRACT` into `.env`, 3 fund bot ETH, 4 Arm Keep Live, 5 Sim off.

Copy `.env.example` → `.env`. Dashboard loads `.env` on start (`KEYSTORE_PATH` / `KEYSTORE_PW` are the same names the signer already uses). Owner of the contract should be the **bot EOA** on the Funds card (`setOwner` only if you deployed from a different wallet).

SOL live does **not** need a custom program deploy (Python Solend/Jupiter + Jito). `ARB_CONTRACT` is only for ETH DEX arb, not for these liquidations.

## Secrets

Never commit keystores, `.env`, or `solana/keys/sponsor.json` / `bot.json`. `solana/keys/pubkeys.json` is addresses only.

Copy `solana/.env.example` → `solana/.env`. On first start the dashboard creates local sponsor/bot keypairs. Fund from the funder pubkey on the SOL Funds card (**0.08 SOL** sponsor, **0.25 SOL** bot).
