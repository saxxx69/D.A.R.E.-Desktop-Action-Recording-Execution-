# Binance Demo - Trading Automations

## Purpose
Record and execute trading workflows on Binance Demo Platform: login, open trades, set stop-loss/take-profit, close positions, etc.

## Target
- **URL**: https://demo.binance.com (or your binance demo endpoint)
- **Type**: Web Page
- **Platform**: Browser (Chrome/Firefox)
- **Account**: Demo/Sandbox Account

## Recordings

### run-2026-05-06-open-trade
- **Scenario**: Open a new trade (spot or futures)
- **Steps**: 
  1. Login to Binance Demo
  2. Navigate to Trading interface
  3. Select trading pair (e.g., BTC/USDT)
  4. Enter position size
  5. Set entry price (if limit order)
  6. Click "Buy" or "Sell"
  7. Confirm order
- **Status**: ⏳ In Progress
- **Notes**: Using demo account, no real funds at risk

## Configuration
See `config.json` for site-specific settings and authentication.

## Running Automations

```bash
cd recordings/web-pages/binance-demo

# Start recording
dare record --notes "Open BTC/USDT long position"

# After recording, process the run
dare normalize --run <run-id>
dare generate-dsl --run <run-id>
dare build-intents --run <run-id>
dare validate --run <run-id>

# Dry-run before executing live
dare execute --run <run-id> --live false

# Execute live on Binance Demo
dare execute --run <run-id> --live true
```

## Safety Notes
✅ Using **DEMO** account only — no real funds
✅ Always validate with `--live false` first
✅ Keep API keys and credentials in environment variables
✅ Test on small positions first

