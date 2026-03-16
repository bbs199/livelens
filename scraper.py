"""
LiveLens Scraper — Production Module
Accepts raw blurb text with embedded URLs.
Fetches pages, runs LLM extraction, returns structured field dict
ready to pre-populate the LiveLens scoring form.

Used by app.py via the /scrape endpoint.
API key read from config.ANTHROPIC_API_KEY (or ANTHROPIC_API_KEY env var).
"""

import sys
sys.path.insert(0, "/home/b/shared")

import json
import re
import os
import urllib.request
import urllib.error
from urllib.parse import urlparse

import config


# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL             = "claude-sonnet-4-20250514"
MAX_CHARS_PER_PAGE = 10000
REQUEST_TIMEOUT    = 15

# Domains not worth fetching — gated, redirect-only, or no useful data
SKIP_DOMAINS = [
    "twitter.com", "x.com",
    "discord.com", "discord.gg",
    "youtube.com",
    "medium.com",
    "linktr.ee", "linktree.com",
    "docsend.com",
    "instagram.com", "facebook.com", "reddit.com",
    "dexscreener.com",  # API call replaces HTML fetch
]


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    key = config.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in config or environment")
    return key


def _call_claude(prompt: str, system: str, max_tokens: int = 2000) -> str:
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": _get_api_key(),
            "anthropic-version": "2023-06-01",
        }
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
        return data["content"][0]["text"]


def _fetch_url(url: str) -> str | None:
    """Fetch URL, strip HTML tags, return plain text. None on failure."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LiveLens/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            raw = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.DOTALL)
            raw = re.sub(r"<style[^>]*>.*?</style>",  " ", raw, flags=re.DOTALL)
            raw = re.sub(r"<[^>]+>", " ", raw)
            raw = re.sub(r"\s+", " ", raw).strip()
            return raw[:MAX_CHARS_PER_PAGE]
    except Exception:
        return None


def _is_fetchable(url: str) -> bool:
    """Return True if this URL is worth fetching."""
    try:
        domain = urlparse(url).netloc.lower()
        return not any(skip in domain for skip in SKIP_DOMAINS)
    except Exception:
        return False


def _extract_urls(text: str) -> list:
    """
    Pull all URLs from raw text.
    Also constructs a DexScreener URL for any Base contract address (0x...) found in the text.
    Deduplicated, order preserved.
    """
    found = re.findall(r'https?://[^\s\)\]\>\"\,]+', text)

    # Construct DexScreener URL from any Base contract address in the blurb
    # Pattern: looks for "CA:" or "contract" followed by 0x address
    ca_matches = re.findall(r'(?:CA|contract)[:\s]+0x([0-9a-fA-F]{40})', text)
    for addr in ca_matches:
        full_addr = f"0x{addr}"
        dex_url = f"https://dexscreener.com/base/{full_addr}"
        if dex_url not in found:
            found.append(dex_url)

    seen  = set()
    unique = []
    for u in found:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def _clean_json(raw: str) -> str:
    """Strip markdown fences if LLM wrapped output in them."""
    return re.sub(r"```json|```", "", raw).strip()


def _extract_contract_address(text: str) -> str | None:
    """Extract Base contract address from raw text. Looks for 0x followed by 40 hex chars."""
    matches = re.findall(r'0x[a-fA-F0-9]{40}', text)
    return matches[0] if matches else None


def _fetch_dexscreener(address: str) -> dict:
    """
    Fetch token data from DexScreener public API.
    Address can be either a pair address or token contract — API handles both.
    Always returns the actual token contract address from the response.
    """
    url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        pairs = data.get("pairs", [])
        if not pairs:
            # Try as pair address instead
            url2 = f"https://api.dexscreener.com/latest/dex/pairs/base/{address}"
            req2 = urllib.request.Request(url2, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                data2 = json.loads(resp2.read().decode())
            pairs = data2.get("pairs", [])
            if not pairs:
                return {}
        base_pairs = [p for p in pairs if p.get("chainId") == "base"]
        pair = base_pairs[0] if base_pairs else pairs[0]
        # Extract the actual token contract address
        base_token    = pair.get("baseToken", {})
        token_name    = base_token.get("name", "").strip()
        token_symbol  = base_token.get("symbol", "").strip()
        result = {
            "token_address":     base_token.get("address", ""),
            "ticker":            token_symbol if token_symbol else None,
            "project_name":      token_name if token_name else None,
            "market_cap_usd":    float(pair.get("marketCap", 0) or 0),
            "volume_24h_usd":    float(pair.get("volume", {}).get("h24", 0) or 0),
            "price_current_usd": float(pair.get("priceUsd", 0) or 0),
            "liquidity_usd":     float(pair.get("liquidity", {}).get("usd", 0) or 0),
        }
        # Infer total supply from FDV / price
        fdv   = float(pair.get("fdv", 0) or 0)
        price = float(pair.get("priceUsd", 0) or 0)
        if fdv and price:
            inferred_supply = fdv / price
            rounded = round(inferred_supply / 1e9) * 1e9
            if rounded > 0:
                result["total_supply"] = rounded
        return result
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────
# EXTRACTION PASS 1 — blurb
# ─────────────────────────────────────────────────────────────

def _extract_from_blurb(blurb: str) -> dict:
    """
    First LLM pass — extract everything visible in the blurb itself.
    """
    system = (
        "You are a crypto project data extractor. "
        "Extract structured data from AI agent token project descriptions. "
        "Return ONLY valid JSON. No preamble, no markdown fences, no explanation. "
        "Use null for any field not present."
    )

    prompt = f"""Extract the following fields from this AI agent token blurb. Return only JSON.

{{
  "project_name": string or null,
  "ticker": string or null,
  "chain": string or null,
  "agent_name": string or null,
  "operator_name": string or null,
  "operator_real_name_known": boolean or null,
  "website_url": string or null,
  "docs_url": string or null,
  "twitter_url": string or null,
  "token_address": string or null,
  "launch_platform": string or null,
  "airdrop_mentioned": boolean,
  "token_utility": {{
    "has_staking": boolean,
    "has_burns": boolean,
    "has_governance": boolean
  }}
}}

Blurb:
{blurb}"""

    try:
        raw = _call_claude(prompt, system, max_tokens=1500)
        return json.loads(_clean_json(raw))
    except Exception as e:
        return {"_blurb_error": str(e)}


# ─────────────────────────────────────────────────────────────
# EXTRACTION PASS 2 — fetched pages
# ─────────────────────────────────────────────────────────────

def _extract_from_pages(content: str) -> dict:
    """
    Second LLM pass — extract detailed LiveLens fields from fetched page content.
    Conservative: only extract values explicitly stated in the content.
    """
    system = (
        "You are a crypto data extractor specialising in AI agent tokens on Base. "
        "Extract precise data from web page content. "
        "Return ONLY valid JSON. No preamble, no markdown fences, no explanation. "
        "Be conservative — only extract values explicitly stated. Use null if not found. "
        "If a field value is not explicitly stated in the page content, return null. "
        "Do not infer, estimate, or hallucinate values. "
        "A number like agent_subagent_count must appear literally on the page to be extracted."
    )

    prompt = f"""Extract the following LiveLens fields from this page content. Return only JSON.

{{
  "revenue_verified": boolean or null,
  "revenue_sources": list of strings or [],
  "revenue_dashboard_url": string or null,
  "revenue_dashboard_live": boolean or null,
  "burn_address": string or null,
  "burn_tx_count_30d": number or null,
  "treasury_wallet_addresses": list of strings or [],
  "treasury_total_usd": number or null,
  "treasury_dashboard_url": string or null,
  "value_accrual_type": "buyback_burn" | "staking_rewards" | "fee_redistribution" | "none" | null,
  "fee_structure": object or null,
  "agent_subagent_count": number or null,
  "agent_infrastructure": string or null,
  "agent_tech_stack": list of strings or [],
  "agent_output_examples": list of strings or [],
  "products": [
    {{"name": string, "price": number or null, "is_live": boolean}}
  ] or [],
  "product_count_live": number or null,
  "operator_real_name_known": boolean or null,
  "operator_communication_frequency": "daily" | "weekly" | "monthly" | "sporadic" | null,
  "operator_addresses_failures": boolean or null,
  "lp_locked": boolean or null,
  "lp_lock_until": string or null,
  "total_supply": number or null,
  "circulating_supply": number or null,
  "team_allocation_pct": number or null,
  "mint_function_active": boolean or null,
  "top_10_wallet_pct": number or null,
  "presale_conducted": boolean or null,
  "market_cap_usd": number or null,
  "volume_24h_usd": number or null,
  "price_ath_usd": number or null,
  "price_current_usd": number or null,
  "holder_count": number or null,
  "volume_trend_7d": "increasing" | "stable" | "declining" | null,
  "project_age_days": number or null
}}

Return null for any field you are not 100% certain about from the page text. It is better to return null than to guess.

Page content:
{content}"""

    try:
        raw = _call_claude(prompt, system, max_tokens=3000)
        return json.loads(_clean_json(raw))
    except Exception as e:
        return {"_pages_error": str(e)}


# ─────────────────────────────────────────────────────────────
# MERGE
# ─────────────────────────────────────────────────────────────

# Fields where page data is more authoritative than blurb
PAGE_PRIORITY_FIELDS = {
    "revenue_verified", "revenue_sources", "revenue_dashboard_url", "revenue_dashboard_live",
    "burn_address", "burn_tx_count_30d", "treasury_wallet_addresses", "treasury_total_usd",
    "treasury_dashboard_url", "value_accrual_type", "fee_structure",
    "agent_subagent_count", "agent_infrastructure", "agent_tech_stack", "agent_output_examples",
    "products", "product_count_live",
    "operator_communication_frequency", "operator_addresses_failures",
    "lp_locked", "lp_lock_until", "total_supply", "circulating_supply",
    "team_allocation_pct", "mint_function_active", "top_10_wallet_pct", "presale_conducted",
    "market_cap_usd", "volume_24h_usd", "price_ath_usd", "price_current_usd",
    "holder_count", "volume_trend_7d", "project_age_days",
}

# Key fields for completeness reporting
SCORED_FIELDS = [
    "revenue_verified", "value_accrual_type", "treasury_total_usd",
    "agent_tech_stack", "product_count_live",
    "operator_real_name_known", "lp_locked", "top_10_wallet_pct",
    "market_cap_usd", "volume_24h_usd",
]


def _merge(blurb_data: dict, page_data: dict, urls_fetched: list) -> dict:
    """
    Merge blurb and page extractions.
    Page data wins for structured fields; blurb fills the rest.
    """
    merged = {}

    # Blurb data first (skip nulls and error keys)
    for k, v in blurb_data.items():
        if not k.startswith("_") and v is not None:
            merged[k] = v

    # Page data overrides for priority fields
    for k, v in page_data.items():
        if not k.startswith("_") and v is not None:
            if k in PAGE_PRIORITY_FIELDS:
                merged[k] = v
            elif k not in merged:
                merged[k] = v

    # Always inject narrative context from config
    merged["narrative_context"] = config.NARRATIVE_STATE

    # Completeness summary
    found   = [f for f in SCORED_FIELDS if merged.get(f) is not None]
    missing = [f for f in SCORED_FIELDS if merged.get(f) is None]

    merged["_scrape_meta"] = {
        "urls_fetched": urls_fetched,
        "fields_found": found,
        "fields_missing": missing,
        "completeness_pct": round(len(found) / len(SCORED_FIELDS) * 100),
    }

    return merged


# ─────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────

def scrape(blurb: str) -> dict:
    """
    Full scrape pipeline. Accepts raw blurb text (with embedded URLs).
    Returns merged field dict ready to pre-populate LiveLens form.

    Always returns a dict — never raises. Errors captured in _scrape_meta.
    """
    errors = []

    # Step 1 — extract URLs
    all_urls  = _extract_urls(blurb)
    fetchable = [u for u in all_urls if _is_fetchable(u)]
    skipped   = [u for u in all_urls if not _is_fetchable(u)]

    # Step 1b — extract real token contract via DexScreener API
    dex_data = {}
    contract_address = None
    # If a DexScreener URL is present, use its address (may be pair addr) — API resolves to token
    dex_pattern = re.search(r'dexscreener\.com/base/([a-fA-F0-9x]+)', blurb)
    if dex_pattern:
        dex_address = dex_pattern.group(1)
        dex_data = _fetch_dexscreener(dex_address)
        if dex_data.get("token_address"):
            contract_address = dex_data["token_address"]
    # Fallback: extract any 0x address directly from blurb
    if not contract_address:
        contract_address = _extract_contract_address(blurb)
        if contract_address and not dex_data:
            dex_data = _fetch_dexscreener(contract_address)
            if dex_data.get("token_address"):
                contract_address = dex_data["token_address"]

    # Step 2 — LLM pass on blurb
    try:
        blurb_data = _extract_from_blurb(blurb)
    except Exception as e:
        blurb_data = {}
        errors.append(f"Blurb extraction failed: {e}")

    # Step 3 — fetch pages
    fetched_content = {}
    urls_fetched    = []
    for url in fetchable:
        content = _fetch_url(url)
        if content:
            fetched_content[url] = content
            urls_fetched.append(url)

    # Step 4 — LLM pass on fetched content
    page_data = {}
    if fetched_content:
        combined = "\n\n---\n\n".join(
            f"SOURCE: {url}\n{text}"
            for url, text in fetched_content.items()
        )
        try:
            page_data = _extract_from_pages(combined)
        except Exception as e:
            errors.append(f"Page extraction failed: {e}")

    # Step 5 — merge
    merged = _merge(blurb_data, page_data, urls_fetched)

    # Step 6 — overlay DexScreener API data (only fills nulls, doesn't overwrite page data)
    for field in ["token_address", "ticker", "market_cap_usd", "volume_24h_usd",
                  "price_current_usd", "liquidity_usd", "project_name"]:
        if dex_data.get(field) and not merged.get(field):
            merged[field] = dex_data[field]
    # Always ensure token_address is set from confirmed contract
    if contract_address and not merged.get("token_address"):
        merged["token_address"] = contract_address

    # Attach skipped URLs and errors to meta
    merged["_scrape_meta"]["skipped_urls"] = skipped
    merged["_scrape_meta"]["errors"]       = errors

    return merged


# ─────────────────────────────────────────────────────────────
# FIELD MAP
# Maps scraper output keys → LiveLens form field names
# ─────────────────────────────────────────────────────────────

FIELD_MAP = {
    # Identity
    "agent_name":                   "agent_name",
    "project_name":                 "agent_name",  # fallback alias
    "operator_name":                "operator_name",
    "operator_real_name_known":     "operator_real_name_known",
    "token_address":                "token_address",
    "launch_platform":              "launch_platform",
    # Revenue
    "revenue_verified":             "revenue_verified",
    "revenue_sources":              "revenue_sources",
    "revenue_dashboard_url":        "revenue_dashboard_url",
    "revenue_dashboard_live":       "revenue_dashboard_live",
    "value_accrual_type":           "value_accrual_type",
    "burn_address":                 "burn_address",
    "burn_tx_count_30d":            "burn_tx_count_30d",
    "treasury_total_usd":           "treasury_total_usd",
    "treasury_dashboard_url":       "treasury_dashboard_url",
    "fee_structure":                "fee_structure",
    # Agent
    "agent_tech_stack":             "agent_tech_stack",
    "agent_subagent_count":         "agent_subagent_count",
    "agent_infrastructure":         "agent_infrastructure",
    "agent_output_examples":        "agent_output_examples",
    # Products
    "products":                     "products",
    "product_count_live":           "product_count_live",
    # Operator
    "operator_communication_frequency": "operator_communication_frequency",
    "operator_addresses_failures":  "operator_addresses_failures",
    "operator_background":          "operator_background",
    "operator_years_active":        "operator_years_active",
    "operator_prior_projects":      "operator_prior_projects",
    "operator_institutional_affiliations": "operator_institutional_affiliations",
    # Token / launch
    "lp_locked":                    "lp_locked",
    "lp_lock_until":                "lp_lock_until",
    "total_supply":                 "total_supply",
    "circulating_supply":           "circulating_supply",
    "team_allocation_pct":          "team_allocation_pct",
    "mint_function_active":         "mint_function_active",
    "top_10_wallet_pct":            "top_10_wallet_pct",
    "presale_conducted":            "presale_conducted",
    # Market
    "market_cap_usd":               "market_cap_usd",
    "volume_24h_usd":               "volume_24h_usd",
    "price_ath_usd":                "price_ath_usd",
    "price_current_usd":            "price_current_usd",
    "holder_count":                 "holder_count",
    "volume_trend_7d":              "volume_trend_7d",
    "project_age_days":             "project_age_days",
    # Activity & Community
    "community_engagement_quality": "community_engagement_quality",
    "ecosystem_collaborations":     "ecosystem_collaborations",
    "updates_last_30d":             "updates_last_30d",
    "last_product_update":          "last_product_update",
    "listed_on_aggregators":        "listed_on_aggregators",
    # Narrative
    "narrative_context":            "narrative_context",
}


def map_to_form_fields(scraped: dict) -> dict:
    """
    Convert scraper output to form-ready field dict.
    Only includes fields the form knows about.
    Strips internal _scrape_meta key (passed through separately).
    """
    form_fields = {}

    for scrape_key, form_key in FIELD_MAP.items():
        val = scraped.get(scrape_key)
        if val is not None:
            form_fields[form_key] = val

    # Always ensure narrative_context is present
    if "narrative_context" not in form_fields:
        form_fields["narrative_context"] = config.NARRATIVE_STATE

    # Pass token_utility through if present
    utility = scraped.get("token_utility")
    if utility:
        form_fields["_token_utility"] = utility

    # Pass scrape meta for UI feedback
    form_fields["_scrape_meta"] = scraped.get("_scrape_meta", {})

    return form_fields


# ─────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import types

    TEST_BLURB = """Felix is an AI agent operating as CEO of The Masinov Company.
Products: How to Hire an AI guide ($29), Done-For-You OpenClaw Setup ($2,499).
Token: $FELIX on Base. CA: 0xf30Bf00edd0C22db54C9274B90D2A4C21FC09b07
Website: https://felixcraft.ai
Docs: https://felixcraft.ai/dashboard
Twitter: https://x.com/FelixCraftAI
Operator: Nat Eliason"""

    # ── Mock _fetch_url to return empty string (no real HTTP) ──
    import scraper as _self_module
    _orig_fetch = _self_module._fetch_url
    _self_module._fetch_url = lambda url: ""

    failures = []

    # Test 1: URL extraction
    all_urls  = _extract_urls(TEST_BLURB)
    fetchable = [u for u in all_urls if _is_fetchable(u)]
    skipped   = [u for u in all_urls if not _is_fetchable(u)]

    if len(all_urls) != 4:
        failures.append(f"Expected 4 URLs, got {len(all_urls)}: {all_urls}")

    # Test 2: x.com is in skipped list
    if not any("x.com" in u for u in skipped):
        failures.append(f"x.com URL should be skipped, but skipped={skipped}")

    # Test 3: felixcraft.ai URLs are fetchable
    felix_fetchable = [u for u in fetchable if "felixcraft.ai" in u]
    if len(felix_fetchable) < 2:
        failures.append(f"Expected ≥2 felixcraft.ai URLs fetchable, got {felix_fetchable}")

    # Test 4: map_to_form_fields returns dict with narrative_context = config.NARRATIVE_STATE
    # (LLM passes will error out without API key — that's expected)
    result = scrape(TEST_BLURB)
    form   = map_to_form_fields(result)

    if "narrative_context" not in form:
        failures.append("narrative_context missing from map_to_form_fields output")
    elif form["narrative_context"] != config.NARRATIVE_STATE:
        failures.append(
            f"narrative_context mismatch: expected {config.NARRATIVE_STATE!r}, "
            f"got {form['narrative_context']!r}"
        )

    # Test 5: no crash (already passed if we're here)
    print(f"URLs found:      {len(all_urls)} — {all_urls}")
    print(f"Fetchable:       {fetchable}")
    print(f"Skipped:         {skipped}")
    print(f"narrative_context in form: {form.get('narrative_context')!r}")
    print(f"_scrape_meta errors: {form.get('_scrape_meta', {}).get('errors', [])}")
    print()

    if failures:
        for f in failures:
            print(f"FAIL — {f}")
        import sys; sys.exit(1)
    else:
        print("PASS")
