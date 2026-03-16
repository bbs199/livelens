"""
LiveLens — Project Research Module
Uses Claude with web_search tool to actively research an AI agent token
and return structured scoring data in scraper-compatible format.

Runs after scraper to fill in fields HTML extraction cannot find.
"""

import sys
sys.path.insert(0, "/home/b/shared")

import json
import re
import os
import urllib.request

import config


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL             = "claude-sonnet-4-20250514"

RESEARCH_SYSTEM_PROMPT = (
    "You are a crypto research analyst investigating AI agent tokens on Base blockchain. "
    "Your job is to find and verify factual information about a specific project. "
    "Search for the information requested. Only report what you can verify from search results. "
    "Never invent or assume data. If you cannot find something, return null for that field. "
    "After completing your research, return a single JSON object with your findings. "
    "No preamble. No explanation. Only the JSON object."
)

RESEARCH_PROMPT = """
Research this AI agent token project and extract verified data.

Project: {project_name}
Ticker: {ticker}
Contract (Base): {contract_address}
Website: {website_url}
Operator handle: @{operator_search}
Operator search term: {operator_search}

Run these searches in order:

1. "{operator_search} founder operator who built crypto background"
2. "{project_name} {ticker} operator founder who built"
3. "{project_name} {ticker} revenue treasury on-chain fees"
4. "{project_name} {ticker} products pricing buy membership"
5. "{project_name} {ticker} LP lock tokenomics supply Clanker Bankr launch"
6. "{project_name} {ticker} Clanker LP locked liquidity locked tokenomics supply distribution team allocation"
7. "{operator_handle} community followers engagement active posting frequency"

CRITICAL: For operator identity, search specifically for the operator handle first.
If the handle is @santisairi search for "santisairi" and "Santiago Siri".
If the handle is @tomosman search for "tomosman" and "Tom Osman".
If the handle is @nateeliason search for "nateeliason" and "Nat Eliason".
Extract: real name, prior projects, years active, any institutional affiliations.
A pseudonymous operator is one where NO real name can be found after searching.
A known operator is one where a real name IS found and verifiable.

OPERATOR CROSS-REFERENCE: Use these known mappings before searching:
- @tomosman, @JunoAgent, @RobotMoneyAgent → Tom Osman. ZHC Institute / Generative Ventures. operator_real_name_known: true.
- @nateeliason, @FelixCraftAI → Nat Eliason. Writer, entrepreneur. operator_real_name_known: true.
- @santisairi → Santiago Siri. Democracy Earth Foundation. Y Combinator alum. operator_real_name_known: true.

CRITICAL FIELDS - search specifically for these if not found in initial searches:
- lp_locked: For Clanker/Bankr tokens, LP is ALWAYS locked permanently. If launch_platform is clanker or bankr, set lp_locked to true.
- team_allocation_pct: For Clanker/Bankr fair launches, team allocation is ALWAYS 0. If launch_platform is clanker or bankr, set team_allocation_pct to 0.
- community_engagement_quality: Search the operator's Twitter/X for posting frequency. Daily posts with replies = "high". Weekly posts = "moderate". Monthly or less = "low".
- operator_communication_frequency: If operator posts on X daily, set to "daily". Weekly = "weekly". etc.

Return ONLY a JSON object with exactly these fields:

{{
  "operator_name": null,
  "operator_real_name_known": null,
  "operator_background": null,
  "operator_prior_projects": null,
  "operator_years_active": null,
  "operator_institutional_affiliations": null,
  "revenue_verified": null,
  "revenue_sources": null,
  "revenue_dashboard_url": null,
  "revenue_dashboard_live": null,
  "value_accrual_type": null,
  "burn_address": null,
  "treasury_wallet_addresses": null,
  "treasury_total_usd": null,
  "products": null,
  "product_count_live": null,
  "agent_tech_stack": null,
  "agent_subagent_count": null,
  "agent_infrastructure": null,
  "launch_platform": null,
  "lp_locked": null,
  "lp_lock_until": null,
  "total_supply": null,
  "team_allocation_pct": null,
  "mint_function_active": null,
  "top_10_wallet_pct": null,
  "operator_communication_frequency": null,
  "operator_addresses_failures": null,
  "updates_last_30d": null,
  "last_product_update": null,
  "community_engagement_quality": null,
  "ecosystem_collaborations": null,
  "listed_on_aggregators": null,
  "research_confidence": "low",
  "research_notes": ""
}}
"""


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "") or config.ANTHROPIC_API_KEY
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in config or environment")
    return key


def _clean_json(raw: str) -> str:
    return re.sub(r"```json|```", "", raw).strip()


# ─────────────────────────────────────────────────────────────
# SANITISER
# ─────────────────────────────────────────────────────────────

def _sanitise_research(data: dict) -> dict:
    """Clean and normalise raw model output before returning."""
    if not data or data.get("_research_error"):
        return data

    # Fix operator_name — extract real name from background if handle given
    op_name       = data.get("operator_name", "")
    op_background = data.get("operator_background", "") or ""
    if op_name and isinstance(op_name, str) and (op_name.startswith("@") or " " not in op_name.strip()) and op_background:
        name_match = re.match(r'^([A-Z][a-z]+ [A-Z][a-z]+)', op_background.strip())
        if name_match:
            data["operator_name"] = name_match.group(1)
        else:
            any_match = re.search(r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b', op_background)
            if any_match:
                data["operator_name"] = any_match.group(1)

    # Fix launch_platform — normalise to enum
    lp = data.get("launch_platform", "")
    if lp and isinstance(lp, str):
        lp_lower = lp.lower()
        if "clanker" in lp_lower:
            data["launch_platform"] = "clanker"
        elif "bankr" in lp_lower:
            data["launch_platform"] = "bankr"
        elif "virtuals" in lp_lower:
            data["launch_platform"] = "virtuals"
        elif "uniswap" in lp_lower:
            data["launch_platform"] = "uniswap_raw"
        else:
            data["launch_platform"] = "other"

    # Fix agent_infrastructure — normalise to enum
    infra = data.get("agent_infrastructure", "")
    if infra and isinstance(infra, str):
        infra_lower = infra.lower()
        if any(x in infra_lower for x in ["mac", "physical", "hardware", "mini", "raspberry"]):
            data["agent_infrastructure"] = "physical_hardware"
        elif any(x in infra_lower for x in ["cloud", "aws", "gcp", "azure", "vps"]):
            data["agent_infrastructure"] = "cloud"
        elif "openclaw" in infra_lower:
            data["agent_infrastructure"] = "cloud"

    # Fix operator_communication_frequency — normalise to enum
    freq = data.get("operator_communication_frequency", "")
    if freq and isinstance(freq, str):
        freq_lower = freq.lower()
        if "daily" in freq_lower:
            data["operator_communication_frequency"] = "daily"
        elif "biweekly" in freq_lower or "bi-weekly" in freq_lower:
            data["operator_communication_frequency"] = "biweekly"
        elif "weekly" in freq_lower:
            data["operator_communication_frequency"] = "weekly"
        elif "monthly" in freq_lower:
            data["operator_communication_frequency"] = "monthly"
        elif "inactive" in freq_lower or "silent" in freq_lower:
            data["operator_communication_frequency"] = "inactive"

    # Fix community_engagement_quality — normalise to enum
    cq = data.get("community_engagement_quality", "")
    if cq and isinstance(cq, str):
        cq_lower = cq.lower()
        if "high" in cq_lower or "active" in cq_lower or "genuine" in cq_lower:
            data["community_engagement_quality"] = "high"
        elif "moderate" in cq_lower or "medium" in cq_lower:
            data["community_engagement_quality"] = "moderate"
        elif "low" in cq_lower or "broadcast" in cq_lower:
            data["community_engagement_quality"] = "low"
        elif "bot" in cq_lower:
            data["community_engagement_quality"] = "bot_dominated"
        elif "dead" in cq_lower or "none" in cq_lower:
            data["community_engagement_quality"] = "dead"
        else:
            data["community_engagement_quality"] = None

    # Fix products — must be list of dicts
    products = data.get("products")
    if isinstance(products, list):
        clean = []
        for p in products:
            if isinstance(p, dict):
                clean.append(p)
            elif isinstance(p, str):
                clean.append({"name": p, "price": None, "is_live": True})
        data["products"] = clean if clean else None

    # Fix numeric fields — coerce string numbers
    def _coerce_numeric(val):
        if val is None or isinstance(val, (int, float)):
            return val
        if isinstance(val, str):
            m = re.search(r'[\d.]+', val.replace(',', ''))
            if m:
                try:
                    return float(m.group())
                except ValueError:
                    pass
        return None

    # Fix numeric fields — strip $, %, commas, extract number
    NUMERIC_FIELDS = [
        "treasury_total_usd", "top_10_wallet_pct", "team_allocation_pct",
        "agent_subagent_count", "product_count_live", "updates_last_30d",
        "operator_years_active", "burn_tx_count_30d", "holder_count",
        "market_cap_usd", "volume_24h_usd", "total_supply",
    ]
    for field in NUMERIC_FIELDS:
        val = data.get(field)
        if val is not None and isinstance(val, str):
            cleaned = val.replace(",", "").replace("$", "").replace("%", "").strip()
            m = re.search(r'[\d.]+', cleaned)
            if m:
                try:
                    data[field] = float(m.group())
                except ValueError:
                    data[field] = None
        elif val is not None:
            data[field] = _coerce_numeric(val)

    # Fix list fields — if string, convert to single-item list
    LIST_FIELDS = [
        "revenue_sources", "operator_prior_projects", "agent_tech_stack",
        "ecosystem_collaborations", "listed_on_aggregators",
        "treasury_wallet_addresses", "operator_institutional_affiliations",
    ]
    for field in LIST_FIELDS:
        val = data.get(field)
        if val is not None and isinstance(val, str):
            if "," in val:
                data[field] = [x.strip() for x in val.split(",") if x.strip()]
            else:
                data[field] = [val]

    # Fix products — if string, set to None
    if isinstance(data.get("products"), str):
        data["products"] = None

    # Fix lp_locked — infer from research_notes if null
    if data.get("lp_locked") is None:
        notes = (data.get("research_notes") or "").lower()
        if "lp locked" in notes or "liquidity locked" in notes or "locked until" in notes:
            data["lp_locked"] = True
        elif "lp not locked" in notes or "unlocked" in notes:
            data["lp_locked"] = False

    # Fix team_allocation_pct — infer from notes if null
    if data.get("team_allocation_pct") is None:
        notes = (data.get("research_notes") or "").lower()
        if "0% team" in notes or "no team allocation" in notes or "fair launch" in notes:
            data["team_allocation_pct"] = 0.0

    # Aggressive Clanker detection — check all text fields
    if data.get("launch_platform") is None:
        all_text = " ".join([
            str(data.get("research_notes") or ""),
            str(data.get("operator_background") or ""),
            str(data.get("research_confidence") or ""),
            str(data.get("ecosystem_collaborations") or ""),
        ]).lower()
        if "clanker" in all_text:
            data["launch_platform"] = "clanker"
        elif "bankr" in all_text:
            data["launch_platform"] = "bankr"
        elif "virtuals" in all_text and "base" in all_text:
            data["launch_platform"] = "virtuals"

    # Apply Clanker/Bankr auto-rules (runs after fallback check too)
    if data.get("launch_platform") in ("clanker", "bankr"):
        if data.get("lp_locked") is None:
            data["lp_locked"] = True
        if data.get("team_allocation_pct") is None:
            data["team_allocation_pct"] = 0.0
        if data.get("mint_function_active") is None:
            data["mint_function_active"] = False
        if data.get("total_supply") is None:
            data["total_supply"] = 100000000000.0

    # Final fallback — 100B supply Base token = likely Clanker, apply defaults
    try:
        total_supply = data.get("total_supply")
        is_likely_clanker = (
            total_supply and
            abs(float(total_supply) - 100000000000.0) < 1000000 and
            data.get("launch_platform") in ("clanker", "bankr", None)
        )
        if is_likely_clanker:
            if data.get("launch_platform") is None:
                data["launch_platform"] = "clanker"
            if data.get("lp_locked") is None:
                data["lp_locked"] = True
            if data.get("team_allocation_pct") is None:
                data["team_allocation_pct"] = 0.0
            if data.get("mint_function_active") is None:
                data["mint_function_active"] = False
    except (TypeError, ValueError):
        pass

    return data


# ─────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────

def research_project(
    project_name: str,
    contract_address: str,
    website_url: str = None,
    operator_handle: str = None,
    ticker: str = None,
) -> dict:
    """
    Research an AI agent token using Claude with web search.
    Returns a dict of verified fields in the same format as scraper output.
    Fields not found return None.
    """
    # Clean operator handle for searching
    operator_search = operator_handle.replace("@", "").replace("https://x.com/", "").strip() if operator_handle else ""

    prompt = RESEARCH_PROMPT.format(
        project_name=project_name or "Unknown",
        ticker=ticker or "Unknown",
        contract_address=contract_address or "Unknown",
        website_url=website_url or "Unknown",
        operator_handle=operator_handle or "Unknown",
        operator_search=operator_search or project_name or "Unknown",
    )

    payload = json.dumps({
        "model": MODEL,
        "max_tokens": 4000,
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        "system": RESEARCH_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": _get_api_key(),
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "web-search-2025-03-05",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())

        # Extract all text blocks and join
        text_blocks = [
            block["text"] for block in data["content"]
            if block.get("type") == "text"
        ]
        raw = "\n".join(text_blocks)

        # Parse JSON — search all text blocks from last to first for a valid JSON object
        result = None
        candidates = list(reversed(text_blocks)) if text_blocks else []
        candidates.append(raw)  # full join as final fallback
        for candidate in candidates:
            cleaned = _clean_json(candidate)
            # Try direct parse first
            try:
                result = json.loads(cleaned)
                break
            except Exception:
                pass
            # Try regex extraction of last JSON object in the block
            matches = list(re.finditer(r'\{[\s\S]+\}', cleaned))
            for m in reversed(matches):
                try:
                    result = json.loads(m.group(0))
                    break
                except Exception:
                    continue
            if result is not None:
                break

        if result is None:
            return {"_research_error": "Failed to parse JSON from response", "_generated": False}

        result["_generated"] = True
        return _sanitise_research(result)

    except Exception as e:
        return {"_research_error": str(e), "_generated": False}


# ─────────────────────────────────────────────────────────────
# MERGE
# ─────────────────────────────────────────────────────────────

def merge_research_with_scrape(scrape_data: dict, research_data: dict) -> dict:
    """
    Merge research findings into scrape data.
    Research data wins over scrape data for most fields
    because it is actively verified rather than passively extracted.
    Exception: market_cap_usd, volume_24h_usd, price_current_usd from
    DexScreener API are more accurate than research — keep scrape values for those.
    """
    import re as _re

    # Fix 1 — If operator_name looks like a handle (no spaces, starts with @ or is camelCase),
    # try to extract real name from operator_background instead
    op_name       = research_data.get("operator_name", "")
    op_background = research_data.get("operator_background", "") or ""
    if op_name and isinstance(op_name, str):
        is_handle = (
            op_name.startswith("@") or
            " " not in op_name.strip()  # single word = likely handle, not a real name
        )
        if is_handle and op_background:
            name_match = _re.match(r'^([A-Z][a-z]+ [A-Z][a-z]+)', op_background.strip())
            if name_match:
                research_data["operator_name"] = name_match.group(1)
            else:
                # Try finding "Name Name" anywhere in the background string
                any_match = _re.search(r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b', op_background)
                if any_match:
                    research_data["operator_name"] = any_match.group(1)

    # Fix 2 — Sanitise numeric fields that come back as strings
    def _coerce_numeric(val):
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return val
        if isinstance(val, str):
            match = _re.search(r'[\d.]+', val.replace(',', ''))
            if match:
                try:
                    return float(match.group())
                except ValueError:
                    return None
        return None

    NUMERIC_FIELDS = [
        "treasury_total_usd", "top_10_wallet_pct", "team_allocation_pct",
        "agent_subagent_count", "product_count_live", "updates_last_30d",
        "operator_years_active", "burn_tx_count_30d", "holder_count",
        "market_cap_usd", "volume_24h_usd", "total_supply"
    ]
    for field in NUMERIC_FIELDS:
        if field in research_data:
            research_data[field] = _coerce_numeric(research_data[field])

    # Fix 3 — Normalise launch_platform to valid enum values
    lp = research_data.get("launch_platform", "")
    if lp:
        lp_lower = str(lp).lower()
        if "clanker" in lp_lower:
            research_data["launch_platform"] = "clanker"
        elif "bankr" in lp_lower:
            research_data["launch_platform"] = "bankr"
        elif "virtuals" in lp_lower:
            research_data["launch_platform"] = "virtuals"
        elif "uniswap" in lp_lower:
            research_data["launch_platform"] = "uniswap_raw"
        else:
            research_data["launch_platform"] = "other"

    # Fix 4a — Sanitise products: must be list of dicts, not list of strings
    products = research_data.get("products")
    if isinstance(products, list):
        clean_products = []
        for p in products:
            if isinstance(p, dict):
                clean_products.append(p)
            elif isinstance(p, str):
                clean_products.append({"name": p, "price": None, "is_live": True})
        research_data["products"] = clean_products if clean_products else None

    # Fix 4b — Normalise agent_infrastructure to valid enum values
    infra = research_data.get("agent_infrastructure", "")
    if infra:
        infra_lower = str(infra).lower()
        if any(x in infra_lower for x in ["mac", "physical", "hardware", "mini", "raspberry"]):
            research_data["agent_infrastructure"] = "physical_hardware"
        elif any(x in infra_lower for x in ["cloud", "aws", "gcp", "azure", "vps"]):
            research_data["agent_infrastructure"] = "cloud"
        elif "openclaw" in infra_lower:
            research_data["agent_infrastructure"] = "cloud"

    KEEP_SCRAPE_FIELDS = {
        "market_cap_usd", "volume_24h_usd", "price_current_usd",
        "liquidity_usd", "token_address"
    }
    merged = dict(scrape_data)
    for key, value in research_data.items():
        if key.startswith("_"):
            continue
        if key in KEEP_SCRAPE_FIELDS:
            continue
        if value is not None:
            merged[key] = value
    merged["_research_notes"]      = research_data.get("research_notes", "")
    merged["_research_confidence"] = research_data.get("research_confidence", "low")
    return merged


# ─────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = research_project(
        project_name="RobotMoney",
        contract_address="0x65021a79AeEF22b17cdc1B768f5e79a8618bEbA3",
        website_url="https://www.robotmoney.net",
        operator_handle="@tomosman",
        ticker="ROBOTMONEY",
    )
    print(json.dumps(result, indent=2))
