# LiveLens

Due diligence scoring for live AI agent tokens on Base.

17-category structured scorer covering Revenue, Agent infrastructure, Operator identity, Launch & LP, Activity, and On-Chain metrics. Outputs 0–100 score, letter grade, red flags, 6-group breakdown, and an LLM-generated analyst report.

---

## Features

- **Scrape + Score** — paste a project blurb or URL; scraper extracts data, researcher enriches with web search, scorer fires immediately
- **17 scoring categories** — 100 points across Revenue, Agent, Operator, Launch, Activity, On-Chain groups
- **LLM analyst report** — Verdict, Agent Assessment, Revenue Reality, Key Risk, What Would Make This Better
- **Clanker/Bankr auto-fill** — fair-launch token rules applied automatically (100B supply, LP locked, 0% team alloc)
- **Credit system** — USDC on Base; 15 credits per analysis
- **DexScreener integration** — live price, MCap, volume, LP data pulled at scrape time

---

## Stack

- Python 3.12 + Flask
- SQLite (no ORM)
- Anthropic API — `claude-sonnet-4-20250514` with web search beta
- DexScreener API (no key required)
- Deployed as `systemd` user service on Ubuntu

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/bbs199/livelens.git
cd livelens
pip install -r requirements.txt
```

### 2. Shared config

```bash
cp config_example.py /path/to/shared/config.py
# Edit config.py — set RECEIVING_WALLET, CREDITS_DB_PATH
```

### 3. Environment variables

Set in your systemd service or `.env`:

```
ANTHROPIC_API_KEY=sk-ant-api03-...
FLASK_SECRET=your-random-secret-key
```

### 4. Run

```bash
# Development
python app.py

# Production (systemd user service)
systemctl --user start beast-livelens
```

App runs on port `8526` by default.

---

## Scoring Categories

| Group | Categories | Max |
|-------|-----------|-----|
| Revenue | Revenue Existence, Value Accrual, Treasury Transparency | 25 |
| Agent | Agent Existence, Product Reality, Technical Sophistication | 22 |
| Operator | Operator Identity, Skin in the Game | 14 |
| Launch | Launch Platform & LP, Supply Distribution, Mechanic Sustainability | 15 |
| Activity | Shipping Cadence, Community Engagement, Ecosystem Integration | 12 |
| On-Chain | Volume/MCap Ratio, Holder Distribution, Price Trend | 8 |

---

## Credit Tiers

| USD | Credits | Analyses |
|-----|---------|----------|
| $5 | 50 | 3 |
| $10 | 100 | 6 |
| $25 | 250 | 16 |
| $50 | 500 | 33 |

Payment in USDC on Base. 15 credits per analysis.

---

## Project Structure

```
livelens/
├── app.py              # Flask routes + session handling
├── scorer.py           # 17-category scoring engine
├── scraper.py          # DexScreener + blurb extraction
├── researcher.py       # Anthropic web search enrichment
├── reporter.py         # LLM analyst report generation
├── db.py               # SQLite schema + query helpers
├── requirements.txt
├── config_example.py   # Config template (copy to shared/config.py)
└── templates/
    ├── base.html
    ├── index.html
    ├── scrape.html
    ├── form.html
    ├── result.html
    ├── search.html
    └── buy.html
```

---

## Notes

- `config.py` and `credits.db` live in a shared directory outside this repo — never committed
- Database path and receiving wallet are set in `config.py` only
- All secrets via environment variables, never hardcoded
