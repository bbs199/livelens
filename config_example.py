"""
LiveLens — shared config example.
Copy to /home/b/shared/config.py and fill in your values.
Never commit the real config.py.
"""

import os

# USDC receiving wallet on Base (your wallet address)
RECEIVING_WALLET = "0xYOUR_WALLET_ADDRESS"

# USDC contract on Base (this is the canonical mainnet address — safe to leave as-is)
USDC_CONTRACT    = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

# Basescan API endpoint (public)
BASESCAN_API     = "https://api.basescan.org/api"

# Anthropic API key — loaded from environment variable
# Set via: export ANTHROPIC_API_KEY=sk-ant-api03-...
# Or inject via systemd service Environment= directive
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Path to shared credits SQLite database
CREDITS_DB_PATH  = "/path/to/shared/credits.db"

# Operator sets this manually: GREEN / YELLOW / RED
# Displayed as narrative overlay on result pages
NARRATIVE_STATE = "YELLOW"

# Credit purchase tiers — amounts in USD, paid in USDC on Base
CREDIT_TIERS = [
    {"usd": 5,  "credits": 50,  "label": "$5 — 50 credits"},
    {"usd": 10, "credits": 100, "label": "$10 — 100 credits"},
    {"usd": 25, "credits": 250, "label": "$25 — 250 credits"},
    {"usd": 50, "credits": 500, "label": "$50 — 500 credits"},
]

# Port assignments
PORT_LAUNCHLENS = 8525
PORT_LIVELENS   = 8526
