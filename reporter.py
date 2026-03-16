"""
LiveLens — Analyst Report Generator
Generates a 5-section analyst report from LiveLens score data.

Report sections:
  1. verdict               — plain English summary of score and investor meaning
  2. agent_assessment      — is this a real autonomous agent or a persona?
  3. revenue_reality       — is the revenue real, sustainable, growing?
  4. key_risk              — single most likely structural reason this fails
  5. what_would_make_this_better — specific missing things that would improve confidence
"""

import sys
sys.path.insert(0, "/home/b/shared")

import json
import re
import os
import urllib.request
import urllib.error

import config


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = (
    "You are a senior crypto fund analyst writing internal research notes. "
    "You are analysing AI agent tokens on Base — not VC-backed token launches. "
    "These projects have no seed rounds or institutional investors. "
    "Revenue comes from product sales, trading fees, and protocol mechanics. "
    "Write like a smart experienced investor. "
    "Short sentences. No bullet points — prose paragraphs only. "
    "Never use: importantly, leverage, ecosystem, it is worth noting. "
    "Return ONLY a JSON object with exactly these keys: "
    "verdict, agent_assessment, revenue_reality, key_risk, what_would_make_this_better."
)


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
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            return data["content"][0]["text"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if hasattr(e, 'read') else str(e)
        raise RuntimeError(f"HTTP Error {e.code}: {e.reason} — {error_body[:200]}")


# ─────────────────────────────────────────────────────────────
# CONTEXT BUILDER
# ─────────────────────────────────────────────────────────────

def _fmt_usd(val) -> str:
    if val is None:
        return "Unknown"
    v = float(val)
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:.0f}"


def _build_context(project_data: dict, score_result: dict) -> str:
    def get(key, default="Unknown"):
        val = project_data.get(key)
        return val if val is not None else default

    agent_name = get("agent_name")
    operator_name = get("operator_name")
    real_name = get("operator_real_name_known")
    platform = get("launch_platform")
    lp_locked = get("lp_locked")

    rev_verified = get("revenue_verified")
    rev_sources = project_data.get("revenue_sources") or []
    mech = get("value_accrual_type")
    burn_txs = get("burn_tx_count_30d", 0)
    treasury = project_data.get("treasury_total_usd")

    product_count = get("product_count_live", 0)
    products = project_data.get("products") or []

    subagents = get("agent_subagent_count", 0)
    infra = get("agent_infrastructure")
    tech_stack = project_data.get("agent_tech_stack") or []

    mcap = project_data.get("market_cap_usd")
    vol = project_data.get("volume_24h_usd")
    ath = project_data.get("price_ath_usd")
    price = project_data.get("price_current_usd")
    age = get("project_age_days")
    comm_quality = get("community_engagement_quality")
    updates_30d = get("updates_last_30d", 0)
    collabs = project_data.get("ecosystem_collaborations") or []

    # Derived
    vol_mcap_ratio = f"{vol / mcap:.3f}" if mcap and vol and mcap > 0 else "Unknown"
    pct_of_ath = f"{(price / ath * 100):.1f}%" if ath and price and ath > 0 else "Unknown"
    identity_str = (
        "real name publicly known" if real_name is True else
        "pseudonymous" if real_name is False else
        "identity unknown"
    )

    product_lines = []
    for p in products:
        name = p.get("name", "Unnamed")
        price_p = p.get("price")
        live = p.get("is_live", False)
        status = "live" if live else "not live"
        product_lines.append(f"    - {name} (${price_p}) — {status}")

    flags = score_result.get("red_flags") or []
    flag_lines = [f"  [{f.level if hasattr(f, 'level') else f.get('level','?')}] {f.text if hasattr(f, 'text') else f.get('text','?')}" for f in flags]

    cat_scores = score_result.get("category_scores") or {}
    cat_lines = [f"  {k}: {v}" for k, v in cat_scores.items()]

    lines = [
        f"AGENT: {agent_name}",
        f"OPERATOR: {operator_name} ({identity_str})",
        f"LAUNCH PLATFORM: {platform}",
        f"LP LOCKED: {lp_locked}",
        "",
        "REVENUE:",
        f"  Verified: {rev_verified}",
        f"  Sources: {', '.join(rev_sources) if rev_sources else 'None'}",
        f"  Value accrual mechanism: {mech}",
        f"  Burn transactions (30d): {burn_txs}",
        f"  Treasury: {_fmt_usd(treasury)}",
        "",
        "PRODUCTS:",
        f"  Live product count: {product_count}",
    ] + (product_lines if product_lines else ["  None"]) + [
        "",
        "AGENT TECH:",
        f"  Tech stack: {', '.join(tech_stack) if tech_stack else 'Not specified'}",
        f"  Subagent count: {subagents}",
        f"  Infrastructure: {infra}",
        "",
        "MARKET DATA:",
        f"  Market cap: {_fmt_usd(mcap)}",
        f"  24h volume: {_fmt_usd(vol)}",
        f"  Volume/MCap ratio: {vol_mcap_ratio}",
        f"  Current vs ATH: {pct_of_ath}",
        f"  Project age: {age} days",
        "",
        "COMMUNITY:",
        f"  Engagement quality: {comm_quality}",
        f"  Updates in last 30d: {updates_30d}",
        f"  Collaborations: {', '.join(collabs) if collabs else 'None'}",
        "",
        f"OVERALL SCORE: {score_result.get('total_score')}/100 — Grade {score_result.get('grade')}",
        f"CONFIDENCE: {score_result.get('confidence')}",
        f"NARRATIVE CONTEXT: {score_result.get('narrative_context')}",
        f"MARKET STATE: {config.NARRATIVE_STATE}",
        "",
        "CATEGORY SCORES:",
    ] + cat_lines + [
        "",
        "RED FLAGS:",
    ] + (flag_lines if flag_lines else ["  None"])

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# REPORT GENERATION
# ─────────────────────────────────────────────────────────────

def generate_report(project_data: dict, score_result) -> dict:
    """
    Generate full analyst report from project data and score result.

    Args:
        project_data: flat dict of LiveLens input fields
        score_result: ScoreResult dataclass or dict

    Returns:
        dict with keys: verdict, agent_assessment, revenue_reality,
                        key_risk, what_would_make_this_better, _generated
    """
    # Normalise: accept both ScoreResult dataclass and plain dict
    if hasattr(score_result, "__dataclass_fields__"):
        import dataclasses
        sr = dataclasses.asdict(score_result)
    else:
        sr = score_result

    # Truncate long fields before building prompt
    safe_data = {}
    for k, v in project_data.items():
        if isinstance(v, str) and len(v) > 500:
            safe_data[k] = v[:500] + "..."
        elif isinstance(v, list) and len(str(v)) > 500:
            safe_data[k] = v[:5]  # Keep first 5 items only
        else:
            safe_data[k] = v
    project_data = safe_data

    context = _build_context(project_data, sr)

    prompt = f"""Write an analyst report for this AI agent token on Base.

PROJECT AND SCORE DATA:
{context}

Return a JSON object with exactly these five keys:

{{
  "verdict": "2-3 sentence plain English verdict. What did this project score, what grade is that, and what does it mean for a potential investor right now. Be direct — state whether this is investable, speculative, or avoid.",

  "agent_assessment": "2-3 sentences on whether this is a real autonomous agent or a persona with an AI label. What has it actually produced? Does the output require genuine agent infrastructure or could a person do this manually? Be specific about the evidence.",

  "revenue_reality": "2-3 sentences on whether the revenue is real, sustainable, and growing. What happens to the burn/buyback mechanic if trading volume drops 80%? Is the treasury sufficient to survive a bear market? Name specific numbers if they exist in the data.",

  "key_risk": "1-2 sentences on the single most likely structural reason this investment fails. Not a generic risk. The specific weak point in this particular project that would cause it to go to zero.",

  "what_would_make_this_better": "2-3 sentences on the specific missing data or improvements that would increase analyst confidence. Not generic advice — name the exact things absent from this project's data that matter."
}}"""

    try:
        raw = _call_claude(prompt, SYSTEM_PROMPT, max_tokens=2000)
        raw = re.sub(r"```json|```", "", raw).strip()
        report = json.loads(raw)
        report["_generated"] = True
        return report
    except Exception as e:
        return {
            "verdict": f"Report generation failed: {e}",
            "agent_assessment": "",
            "revenue_reality": "",
            "key_risk": "",
            "what_would_make_this_better": "",
            "_generated": False,
            "_error": str(e),
        }


# ─────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/b/livelens")
    from scorer import score_project

    felix_data = {
        "revenue_verified": True,
        "revenue_sources": ["stripe", "product_sales"],
        "revenue_dashboard_live": True,
        "revenue_dashboard_url": "https://felixcraft.ai/dashboard",
        "burn_address": "0x000...dEaD",
        "burn_tx_count_30d": 12,
        "treasury_dashboard_url": "https://felixcraft.ai/dashboard",
        "treasury_total_usd": 4500,
        "value_accrual_type": "buyback_burn",
        "agent_name": "Felix",
        "agent_tech_stack": ["openclaw"],
        "agent_subagent_count": 0,
        "agent_infrastructure": "cloud",
        "agent_output_examples": ["https://felixcraft.ai/dashboard"],
        "products": [
            {"name": "How to Hire an AI", "price": 29, "is_live": True},
            {"name": "Done-For-You Setup", "price": 2499, "is_live": True},
        ],
        "product_count_live": 2,
        "operator_name": "Nat Eliason",
        "operator_real_name_known": True,
        "operator_communication_frequency": "daily",
        "operator_addresses_failures": True,
        "operator_prior_projects": ["published author", "content business"],
        "launch_platform": "clanker",
        "lp_locked": True,
        "lp_lock_until": "2100-01-01",
        "total_supply": 100_000_000_000,
        "team_allocation_pct": 0,
        "mint_function_active": False,
        "top_10_wallet_pct": 12,
        "presale_conducted": False,
        "last_product_update": "2026-03-13",
        "updates_last_30d": 8,
        "community_platforms": ["x", "farcaster"],
        "community_engagement_quality": "high",
        "ecosystem_collaborations": ["claw_mart", "openclaw"],
        "listed_on_aggregators": ["coingecko", "dexscreener"],
        "market_cap_usd": 800_000,
        "volume_24h_usd": 280_000,
        "price_ath_usd": 0.000012,
        "price_current_usd": 0.000010,
        "holder_count": 1200,
        "volume_trend_7d": "stable",
        "project_age_days": 45,
        "narrative_context": "YELLOW",
    }

    print("Scoring Felix...")
    score = score_project(felix_data)
    print(f"Score: {score.total_score}/100 Grade {score.grade} Confidence {score.confidence}")
    print()

    print("Generating report (live API call)...")
    report = generate_report(felix_data, score)

    if not report.get("_generated"):
        print(f"ERROR: {report.get('_error')}")
        sys.exit(1)

    sections = ["verdict", "agent_assessment", "revenue_reality", "key_risk", "what_would_make_this_better"]
    all_ok = True
    for section in sections:
        text = report.get(section, "")
        if not text:
            print(f"FAIL — section '{section}' is empty")
            all_ok = False
        else:
            print(f"── {section.upper()} ──")
            print(text)
            print()

    if all_ok:
        print("✅ All 5 sections generated successfully")
    else:
        print("❌ One or more sections empty")
        sys.exit(1)
