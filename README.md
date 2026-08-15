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

## Secrets

Never commit keystores, `.env`, or `solana/keys/sponsor.json` / `bot.json`. `solana/keys/pubkeys.json` is addresses only.

Copy `solana/.env.example` → `solana/.env`. On first start the dashboard creates local sponsor/bot keypairs. Fund from the funder pubkey on the SOL Funds card (**0.08 SOL** sponsor, **0.25 SOL** bot).
