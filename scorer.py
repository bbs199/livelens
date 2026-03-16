"""
LiveLens Scoring Engine — Task 2
Pure Python — no Flask, no database, no LLM.
Implements the Opus LiveLens framework (100-point scale) with exact rubrics.

Usage:
    from scorer import score_project, ScoreResult
    result = score_project(data_dict)
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime


# ─────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────

@dataclass
class SubScore:
    points:     float
    max_points: int
    notes:      list


@dataclass
class RedFlag:
    id:    str
    level: str   # CRITICAL | WARNING | NOTE
    text:  str


@dataclass
class ScoreResult:
    total_score:      int
    grade:            str
    confidence:       str
    category_scores:  dict   # category name → int
    sub_scores:       dict   # sub-criterion name → SubScore
    red_flags:        list   # list[RedFlag]
    missing_fields:   list   # list[str]
    narrative_context: str   # GREEN | YELLOW | RED
    raw_score:        int    # before flag overrides


# ─────────────────────────────────────────────────────────────
# CATEGORY 1: Revenue Existence (10 pts)
# ─────────────────────────────────────────────────────────────

def _score_revenue_existence(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    9-10: multiple verifiable streams with live dashboard independently confirmable
    7-8:  one verifiable stream with live dashboard
    4-6:  claimed but not independently verifiable
    1-3:  mentioned but no evidence
    0:    none
    Default if unknown: 2
    """
    notes = []

    if data.get("revenue_verified") is None:
        pts = 2
        missing.append("revenue_verified")
        defaults_used.append("revenue_existence")
        notes.append("Revenue verification unknown — conservative default applied")
    elif not data.get("revenue_verified"):
        pts = 0
        notes.append("No verified revenue")
    else:
        # Verified revenue exists
        dashboard_live = data.get("revenue_dashboard_live") or False
        sources = data.get("revenue_sources") or []
        source_count = len(sources) if isinstance(sources, list) else 0

        if source_count >= 3 and dashboard_live:
            pts = 10
            notes.append(f"Multiple verified streams ({source_count}) with live dashboard")
        elif source_count == 2 and dashboard_live:
            pts = 9
            notes.append(f"Multiple verified streams ({source_count}) with live dashboard")
        elif source_count == 1 and dashboard_live:
            pts = 8
            notes.append(f"Single verified stream ({sources[0] if sources else 'unknown'}) with live dashboard")
        elif source_count >= 1:
            pts = 6
            notes.append(f"Verified stream(s): {', '.join(sources) if sources else 'unspecified'}")
        else:
            pts = 4
            notes.append("Revenue claimed but stream(s) unclear")

    return SubScore(float(pts), 10, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 2: Value Accrual (8 pts)
# ─────────────────────────────────────────────────────────────

def _score_value_accrual(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    7-8: clear on-chain automatic buyback/burn, multi-source
    5-6: single working buyback/burn mechanic
    3-4: mechanism exists but unverified/inactive
    1-2: vague future utility claims
    0:   none
    Default: 2
    """
    notes = []

    mech = data.get("value_accrual_type")
    burn_txs = data.get("burn_tx_count_30d") or 0

    if mech is None:
        pts = 2
        missing.append("value_accrual_type")
        defaults_used.append("value_accrual")
        notes.append("Value accrual mechanism unknown — conservative default applied")
    elif not mech or mech == "none":
        pts = 0
        notes.append("No value accrual mechanism")
    else:
        # mech is one of: "buyback_burn", "staking_rewards", "fee_redistribution", etc.
        if "burn" in mech.lower() or "buyback" in mech.lower():
            if burn_txs >= 5:
                pts = 8
                notes.append(f"Active buyback/burn: {burn_txs} txs in 30d")
            else:
                pts = 6
                notes.append(f"Buyback/burn mechanism exists: {burn_txs} recent txs")
        else:
            # Other mechanisms (staking, fee redistribution, etc.)
            pts = 4
            notes.append(f"Value accrual type: {mech}")

    return SubScore(float(pts), 8, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 3: Treasury Transparency (7 pts)
# ─────────────────────────────────────────────────────────────

def _score_treasury_transparency(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    6-7: public dashboard, wallet addresses published, independently verifiable
    4-5: wallet known, verifiable manually, no dashboard
    2-3: mentioned but unverifiable
    0-1: none
    Default: 2
    """
    notes = []

    dashboard_url = data.get("treasury_dashboard_url") or data.get("revenue_dashboard_url")
    addresses = data.get("treasury_wallet_addresses") or []
    total_usd = data.get("treasury_total_usd") or 0

    if dashboard_url is None and (not addresses or len(addresses) == 0):
        pts = 2
        missing.append("treasury_dashboard_url")
        defaults_used.append("treasury_transparency")
        notes.append("Treasury data unknown — conservative default applied")
    elif dashboard_url:
        pts = 7
        notes.append(f"Public dashboard: {dashboard_url}")
        if total_usd > 0:
            notes.append(f"Treasury value: ${total_usd:,.0f}")
    elif addresses and len(addresses) > 0:
        pts = 5
        notes.append(f"{len(addresses)} wallet(s) published")
        if total_usd > 0:
            notes.append(f"Estimated: ${total_usd:,.0f}")
    else:
        pts = 2
        notes.append("Treasury mentioned but not verifiable")

    return SubScore(float(pts), 7, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 4: Agent Existence (10 pts)
# ─────────────────────────────────────────────────────────────

def _score_agent_existence(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    9-10: demonstrably autonomous, multiple subagents, identifiable infrastructure, output not replicable by manual posting
    7-8:  clearly exists and functions, substantial output, technical details shared
    4-6:  exists but autonomous capabilities unclear
    1-3:  minimal evidence, generic AI content, no products
    0:    none
    Default: 3
    """
    notes = []

    agent_name = data.get("agent_name")
    tech_stack = data.get("agent_tech_stack") or []
    subagent_count = data.get("agent_subagent_count") or 0
    infrastructure = data.get("agent_infrastructure")

    if not agent_name:
        pts = 3
        missing.append("agent_name")
        defaults_used.append("agent_existence")
        notes.append("Agent not identified — conservative default applied")
    else:
        # Check for working products/output (bonus for 2+ tech items)
        products = data.get("products") or []
        live_product_count = data.get("product_count_live") or 0
        
        # High-bar criteria for 7+
        if subagent_count >= 2 or (tech_stack and len(tech_stack) >= 2):
            if infrastructure and infrastructure != "unknown" and live_product_count >= 1:
                pts = 10
                notes.append(f"Autonomous multi-agent: {subagent_count} subagents, {', '.join(tech_stack)}, live products")
            elif infrastructure and infrastructure != "unknown":
                pts = 9
                notes.append(f"Autonomous multi-agent: {subagent_count} subagents, {', '.join(tech_stack)}")
            else:
                pts = 8
                notes.append(f"Clear agent function: {subagent_count} subagents, tech: {', '.join(tech_stack)}")
        elif tech_stack and len(tech_stack) >= 1:
            pts = 7
            notes.append(f"Technical implementation: {', '.join(tech_stack)}")
        else:
            # agent exists but autonomous capabilities unclear — band "4-6"
            pts = 6
            notes.append(f"Agent: {agent_name} (capabilities unclear)")

    # Gate: no products, subagents, or output examples = cap at 5
    product_count   = data.get("product_count_live") or 0
    subagent_count  = data.get("agent_subagent_count") or 0
    output_examples = data.get("agent_output_examples") or []
    if product_count == 0 and subagent_count == 0 and len(output_examples) == 0:
        pts = min(pts, 5)
        notes.append("Agent existence capped — no products, subagents, or output examples verified")

    return SubScore(float(pts), 10, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 5: Product Reality (8 pts)
# ─────────────────────────────────────────────────────────────

def _score_product_reality(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    7-8: multiple live purchasable products with real pricing
    5-6: one live product
    3-4: announced/beta, not purchasable
    1-2: vague roadmap only
    0:   none
    Default: 2
    """
    notes = []

    products = data.get("products") or []
    product_count_live = data.get("product_count_live") or 0

    if not products or len(products) == 0:
        pts = 0
        notes.append("No products")
    elif product_count_live >= 2:
        pts = 8
        total_price = sum(float(p.get("price") or 0) for p in products if p.get("is_live"))
        notes.append(f"{product_count_live} live products with pricing")
    elif product_count_live == 1:
        pts = 6
        live_prod = next((p for p in products if p.get("is_live")), None)
        if live_prod:
            notes.append(f"Live product: {live_prod.get('name', 'unnamed')} — ${live_prod.get('price', 'TBD')}")
    else:
        # products exist but not live
        pts = 3
        notes.append(f"{len(products)} product(s) announced/beta")

    # Only mark as missing/default if the data itself is missing (None), not if it's zero
    if product_count_live is None and (not products or len(products) == 0):
        missing.append("product_count_live")
        defaults_used.append("product_reality")

    return SubScore(float(pts), 8, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 6: Technical Sophistication (4 pts)
# ─────────────────────────────────────────────────────────────

def _score_technical_sophistication(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    4: novel custom architecture, multi-agent, smart contract integration
    3: solid use of established frameworks
    2: basic single LLM wrapper
    0-1: no detail or implausible claims
    Default: 1
    """
    notes = []

    tech_stack = data.get("agent_tech_stack")

    if tech_stack is None:
        pts = 1
        missing.append("agent_tech_stack")
        defaults_used.append("technical_sophistication")
        notes.append("Technical stack not specified — conservative default applied")
    elif not tech_stack or len(tech_stack) == 0:
        pts = 1
        notes.append("No technical details provided")
    else:
        stack_str = ", ".join(tech_stack)
        
        if "custom" in stack_str.lower() or "erc4626" in stack_str.lower() or "multi" in stack_str.lower():
            pts = 4
            notes.append(f"Novel/custom: {stack_str}")
        elif "openclaw" in stack_str.lower() or "langchain" in stack_str.lower() or "claude" in stack_str.lower():
            pts = 3
            notes.append(f"Established frameworks: {stack_str}")
        else:
            pts = 2
            notes.append(f"Basic implementation: {stack_str}")

    # Gate: no products and no subagents = cap at 2
    product_count  = data.get("product_count_live") or 0
    subagent_count = data.get("agent_subagent_count") or 0
    if product_count == 0 and subagent_count == 0:
        pts = min(pts, 2)
        notes.append("Technical sophistication capped — no shipped products or subagents verified")

    return SubScore(float(pts), 4, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 7: Operator Identity (8 pts)
# ─────────────────────────────────────────────────────────────

def _score_operator_identity(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    7-8: real name, pre-existing verifiable reputation outside this project
    5-6: real name or established pseudonym with significant history
    3-4: pseudonymous, active, community trust
    1-2: largely anonymous
    0:   completely anonymous
    Default: 2
    """
    notes = []

    real_name_known = data.get("operator_real_name_known")
    operator_name = data.get("operator_name")
    prior_projects = data.get("operator_prior_projects") or []

    if real_name_known is None:
        pts = 2
        missing.append("operator_real_name_known")
        defaults_used.append("operator_identity")
        notes.append("Operator identity unknown — conservative default applied")
    elif real_name_known is False:
        # Pseudonymous
        if prior_projects and len(prior_projects) > 0:
            pts = 6
            notes.append(f"Pseudonymous with history: {operator_name}")
            notes.append(f"Prior: {', '.join(prior_projects)}")
        else:
            # Active pseudonym in crypto community
            pts = 5
            notes.append(f"Established pseudonym: {operator_name}")
    else:
        # real_name_known is True
        if prior_projects and len(prior_projects) > 0:
            pts = 8
            notes.append(f"Doxxed with track record: {operator_name}")
            notes.append(f"Prior: {', '.join(prior_projects)}")
        else:
            pts = 7
            notes.append(f"Real name known: {operator_name}")

    return SubScore(float(pts), 8, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 8: Skin in the Game (6 pts)
# ─────────────────────────────────────────────────────────────

def _score_skin_in_game(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    5-6: primary professional reputation staked, personal financial commitment, building in public
    3-4: publicly committed, partial exposure
    1-2: confirmed involved but shallow commitment
    0:   no evidence
    Default: 1
    """
    notes = []

    failures = data.get("operator_addresses_failures")
    comm_freq = data.get("operator_communication_frequency")

    if failures is None and comm_freq is None:
        pts = 1
        missing.append("operator_addresses_failures")
        defaults_used.append("skin_in_game")
        notes.append("Operator commitment unknown — conservative default applied")
    else:
        pts = 1  # Base
        
        if failures:
            pts += 2
            notes.append("Operator publicly addresses failures (high transparency)")
        
        if comm_freq == "daily":
            pts += 2
            notes.append("Daily communication (high visibility)")
        elif comm_freq == "weekly":
            pts += 1
            notes.append("Weekly communication")
        elif comm_freq in ("monthly", "sporadic"):
            notes.append(f"Communication frequency: {comm_freq}")
        
        pts = min(pts, 6)

    return SubScore(float(pts), 6, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 9: Launch Platform & LP Security (7 pts)
# ─────────────────────────────────────────────────────────────

def _score_launch_platform(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    7:   Clanker/Bankr, LP locked 10+ years or until 2100, platform-enforced fair launch
    5-6: Virtuals Protocol or equivalent, LP locked 10+ years
    3-4: Uniswap with manually locked LP, verifiable on-chain
    1-2: LP lock short-term or unverifiable
    0:   no LP lock
    Default: 3
    """
    notes = []

    lp_locked = data.get("lp_locked")
    lp_lock_until = data.get("lp_lock_until") or ""
    platform = (data.get("launch_platform") or "").lower()

    if lp_locked is None:
        pts = 3
        missing.append("lp_locked")
        defaults_used.append("launch_platform")
        notes.append("LP lock status unknown — conservative default applied")
    elif not lp_locked:
        pts = 0
        notes.append("LP not locked")
    else:
        # LP is locked
        is_long_lock = "2100" in lp_lock_until or (lp_lock_until and lp_lock_until > "2035-01-01")
        
        if platform in ("clanker", "bankr"):
            pts = 7
            notes.append(f"Fair launch platform: {platform}")
            if is_long_lock:
                notes.append(f"LP locked until {lp_lock_until}")
        elif "virtuals" in platform:
            if is_long_lock:
                pts = 6
                notes.append(f"Virtuals launch, LP locked until {lp_lock_until}")
            else:
                pts = 5
                notes.append(f"Virtuals launch, LP locked until {lp_lock_until}")
        elif "uniswap" in platform or "raw" in platform:
            if is_long_lock:
                pts = 4
                notes.append(f"Uniswap raw, LP locked until {lp_lock_until}")
            else:
                pts = 3
                notes.append(f"Uniswap raw, LP locked until {lp_lock_until}")
        else:
            pts = 3
            notes.append(f"LP locked until {lp_lock_until}")

    return SubScore(float(pts), 7, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 10: Supply Distribution (4 pts)
# ─────────────────────────────────────────────────────────────

def _score_supply_distribution(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    4: fixed supply, fully circulating, no team allocation, no presale
    3: minor transparent team allocation <5%
    2: mostly clear, some unaccounted wallets
    1: concentrated, top wallets >20%
    0: no info or mint still active
    Default: 1
    """
    notes = []

    mint_active = data.get("mint_function_active")
    team_pct = data.get("team_allocation_pct")
    top_pct = data.get("top_10_wallet_pct")
    presale = data.get("presale_conducted")

    if mint_active is None and team_pct is None:
        pts = 1
        missing.append("mint_function_active")
        defaults_used.append("supply_distribution")
        notes.append("Supply distribution unknown — conservative default applied")
    else:
        # Treat explicit False the same as 0 for mint_active
        mint_active = bool(mint_active) if mint_active is not None else None
        if mint_active:
            pts = 0
            notes.append("Mint function still active")
        elif presale or team_pct and team_pct > 10:
            pts = 1
            notes.append(f"Presale or high team allocation: {team_pct if team_pct else 'unknown'}%")
        elif team_pct and team_pct > 5:
            pts = 2
            notes.append(f"Team allocation: {team_pct}%")
        elif team_pct and team_pct > 0:
            pts = 3
            notes.append(f"Minor team allocation: {team_pct}%")
        else:
            pts = 4
            notes.append("Fixed supply, no presale, no team allocation")

        if top_pct and top_pct > 20:
            pts = max(1, min(pts, 1))
            notes.append(f"Highly concentrated: top 10 hold {top_pct}%")

    return SubScore(float(pts), 4, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 11: Mechanic Sustainability (4 pts)
# ─────────────────────────────────────────────────────────────

def _score_mechanic_sustainability(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    4: works at low volume, multiple revenue sources for burns
    3: functional but volume-dependent single source
    2: exists but untested in adverse conditions
    0-1: theoretical only or none
    Default: 1
    """
    notes = []

    mech_type = data.get("value_accrual_type")
    fee_struct = data.get("fee_structure") or {}

    if mech_type is None:
        pts = 1
        missing.append("value_accrual_type")
        defaults_used.append("mechanic_sustainability")
        notes.append("Mechanic sustainability unknown — conservative default applied")
    elif not mech_type or mech_type == "none":
        pts = 0
        notes.append("No value accrual mechanic")
    else:
        # Has a mechanic
        fee_count = len(fee_struct) if isinstance(fee_struct, dict) else 0
        
        if fee_count >= 2:
            pts = 4
            notes.append(f"Multiple revenue sources for burns: {fee_count} fees")
            if fee_struct:
                notes.append(f"Fees: {', '.join(f'{k}' for k in fee_struct.keys())}")
        elif fee_count == 1 or "burn" in mech_type.lower():
            pts = 3
            notes.append(f"Single-source mechanic: {mech_type}")
        else:
            pts = 2
            notes.append(f"Mechanic exists: {mech_type}")

    return SubScore(float(pts), 4, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 12: Shipping Cadence (5 pts)
# ─────────────────────────────────────────────────────────────

def _score_shipping_cadence(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    5: shipped within 7 days, multiple releases in 30 days
    4: meaningful update within 14 days
    3: last update 15-30 days ago
    1-2: last update 30-60 days ago
    0: nothing in 60+ days
    Default: 2
    """
    notes = []

    last_ship = data.get("last_product_update")
    updates_30d = data.get("updates_last_30d") or 0

    if last_ship is None:
        # Fallback: use communication frequency as proxy for shipping cadence
        freq = data.get("operator_communication_frequency")
        if freq == "daily":
            pts = 4
            notes.append("Daily operator activity — shipping cadence inferred as active")
            defaults_used.append("shipping_cadence_inferred")
        elif freq == "weekly":
            pts = 3
            notes.append("Weekly operator activity — shipping cadence inferred as moderate")
            defaults_used.append("shipping_cadence_inferred")
        elif freq in ("biweekly", "monthly"):
            pts = 2
            notes.append("Infrequent operator activity — shipping cadence inferred as low")
            defaults_used.append("shipping_cadence_inferred")
        else:
            pts = 2
            missing.append("last_product_update")
            defaults_used.append("shipping_cadence")
            notes.append("Shipping cadence unknown — conservative default applied")
    else:
        # Try to parse date
        try:
            if isinstance(last_ship, str):
                last_ship_date = datetime.strptime(last_ship, "%Y-%m-%d")
            else:
                last_ship_date = last_ship
            days_ago = (datetime.now() - last_ship_date).days
        except:
            days_ago = 999

        if days_ago <= 7:
            if updates_30d >= 2:
                pts = 5
                notes.append(f"Active shipping: {updates_30d} updates in 30d, last {days_ago} days ago")
            else:
                pts = 4
                notes.append(f"Recent update: {days_ago} days ago")
        elif days_ago <= 14:
            pts = 4
            notes.append(f"Meaningful update: {days_ago} days ago")
        elif days_ago <= 30:
            pts = 3
            notes.append(f"Last update: {days_ago} days ago")
        elif days_ago <= 60:
            pts = 2
            notes.append(f"Stale: last update {days_ago} days ago")
        else:
            pts = 0
            notes.append(f"No shipping in {days_ago}+ days")

    return SubScore(float(pts), 5, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 13: Community Engagement (4 pts)
# ─────────────────────────────────────────────────────────────

def _score_community_engagement(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    4: active on multiple platforms, genuine engagement, operator interacts
    3: active on one platform, genuine
    1-2: minimal, mostly broadcasting
    0: dead or bot-dominated
    Default: 1
    """
    notes = []

    platforms = data.get("community_platforms")
    engagement_quality = data.get("community_engagement_quality")

    if platforms is None:
        # Fallback: use engagement_quality directly if available
        if engagement_quality == "high":
            pts = 3
            notes.append("High community engagement quality reported — platform data missing")
        elif engagement_quality == "moderate":
            pts = 2
            notes.append("Moderate community engagement quality reported")
        elif engagement_quality == "low":
            pts = 1
            notes.append("Low community engagement quality reported")
        elif engagement_quality in ("bot_dominated", "dead"):
            pts = 0
            notes.append("Community engagement dead or bot-dominated")
        else:
            pts = 1
            missing.append("community_platforms")
            defaults_used.append("community_engagement")
            notes.append("Community engagement unknown — conservative default applied")
    elif not platforms or len(platforms) == 0:
        pts = 0
        notes.append("No community platforms")
    else:
        if len(platforms) >= 2 and engagement_quality in ("high", "genuine", "active"):
            pts = 4
            notes.append(f"Multi-platform active: {', '.join(platforms)}")
        elif len(platforms) >= 1 and engagement_quality in ("high", "genuine", "active"):
            pts = 3
            notes.append(f"Active on {platforms[0]}")
        elif len(platforms) >= 1:
            pts = 2
            notes.append(f"Presence on {', '.join(platforms)}")
        else:
            pts = 0
            notes.append("No community presence")

    return SubScore(float(pts), 4, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 14: Ecosystem Integration (3 pts)
# ─────────────────────────────────────────────────────────────

def _score_ecosystem_integration(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    3: integrated into ecosystem, collaborations, work referenced by others
    2: some presence, occasional collaborations
    1: minimal, operates in isolation
    0: unknown
    Default: 1
    """
    notes = []

    collabs = data.get("ecosystem_collaborations")

    if collabs is None:
        pts = 1
        missing.append("ecosystem_collaborations")
        defaults_used.append("ecosystem_integration")
        notes.append("Ecosystem integration unknown — conservative default applied")
    elif not collabs or len(collabs) == 0:
        pts = 1
        notes.append("No ecosystem collaborations")
    else:
        if len(collabs) >= 2:
            pts = 3
            notes.append(f"Ecosystem collaborations: {', '.join(collabs)}")
        elif len(collabs) == 1:
            pts = 2
            notes.append(f"Collaboration: {collabs[0]}")
        else:
            pts = 1
            notes.append("Minimal ecosystem presence")

    return SubScore(float(pts), 3, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 15: Volume/MCap Ratio (3 pts)
# ─────────────────────────────────────────────────────────────

def _score_volume_mcap_ratio(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    3: ratio > 0.3
    2: 0.1-0.3
    1: 0.03-0.1
    0: < 0.03 or no data
    Default: 1
    """
    notes = []

    mcap = data.get("market_cap_usd") or 0
    vol_24h = data.get("volume_24h_usd") or 0

    if mcap <= 0 or vol_24h <= 0:
        pts = 1
        missing.append("volume_24h_usd")
        defaults_used.append("volume_mcap_ratio")
        notes.append("Volume data unavailable — conservative default applied")
    else:
        ratio = vol_24h / mcap
        if ratio > 0.3:
            pts = 3
            notes.append(f"High volume/MCap: {ratio:.2f}")
        elif ratio >= 0.1:
            pts = 2
            notes.append(f"Moderate volume/MCap: {ratio:.2f}")
        elif ratio >= 0.03:
            pts = 1
            notes.append(f"Low volume/MCap: {ratio:.2f}")
        else:
            pts = 0
            notes.append(f"Very low volume/MCap: {ratio:.2f}")

    return SubScore(float(pts), 3, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 16: Holder Distribution (3 pts)
# ─────────────────────────────────────────────────────────────

def _score_holder_distribution(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    3: top 10 non-LP wallets < 15%
    2: 15-30%
    1: 30-50%
    0: > 50% or unavailable
    Default: 1
    """
    notes = []

    top_10_pct = data.get("top_10_wallet_pct")

    if top_10_pct is None:
        pts = 1
        missing.append("top_10_wallet_pct")
        defaults_used.append("holder_distribution")
        notes.append("Holder distribution unknown — conservative default applied")
    else:
        if top_10_pct < 15:
            pts = 3
            notes.append(f"Good distribution: top 10 hold {top_10_pct}%")
        elif top_10_pct < 30:
            pts = 2
            notes.append(f"Moderate concentration: top 10 hold {top_10_pct}%")
        elif top_10_pct < 50:
            pts = 1
            notes.append(f"High concentration: top 10 hold {top_10_pct}%")
        else:
            pts = 0
            notes.append(f"Extreme concentration: top 10 hold {top_10_pct}%")

    return SubScore(float(pts), 3, notes)


# ─────────────────────────────────────────────────────────────
# CATEGORY 17: Price Trend (2 pts)
# ─────────────────────────────────────────────────────────────

def _score_price_trend(data: dict, missing: list, defaults_used: list) -> SubScore:
    """
    2: within 50% of ATH or clear recovery trend
    1: 50-80% below ATH
    0: > 80% below ATH with declining volume
    Default: 1
    """
    notes = []

    price_ath = data.get("price_ath_usd")
    price_current = data.get("price_current_usd")
    trend_7d = data.get("volume_trend_7d")

    if price_ath is None or price_current is None:
        pts = 1
        missing.append("price_current_usd")
        defaults_used.append("price_trend")
        notes.append("Price trend unknown — conservative default applied")
    else:
        if price_ath <= 0:
            pts = 1
        else:
            pct_of_ath = (price_current / price_ath) * 100
            
            if pct_of_ath >= 50:
                pts = 2
                notes.append(f"Near ATH: {pct_of_ath:.0f}% of peak")
            elif pct_of_ath >= 20:
                pts = 1
                notes.append(f"Below ATH: {pct_of_ath:.0f}% of peak")
            else:
                pts = 0
                if trend_7d == "declining":
                    notes.append(f"Deep drawdown: {pct_of_ath:.0f}% of ATH, declining")
                else:
                    notes.append(f"Deep drawdown: {pct_of_ath:.0f}% of ATH")

    return SubScore(float(pts), 2, notes)


# ─────────────────────────────────────────────────────────────
# RED FLAGS
# ─────────────────────────────────────────────────────────────

def _check_flags(data: dict, sub_scores: dict) -> list:
    flags = []

    # NOTE-02: Pseudonymous operator
    if data.get("operator_real_name_known") is False:
        flags.append(RedFlag("NOTE-02", "NOTE",
            "Operator is pseudonymous — identity not publicly verifiable"))

    # WARN-03: No verified revenue
    if data.get("revenue_verified") is False or (data.get("revenue_verified") is None):
        flags.append(RedFlag("WARN-03", "WARNING",
            "No verifiable revenue — project has no confirmed income stream"))

    # WARN-04: No product shipped
    if data.get("product_count_live") is None or data.get("product_count_live") == 0:
        flags.append(RedFlag("WARN-04", "WARNING",
            "No product shipped — nothing in users' hands"))

    # NOTE-05: Raw Uniswap launch
    platform = (data.get("launch_platform") or "").lower()
    if "uniswap" in platform or "raw" in platform:
        flags.append(RedFlag("NOTE-05", "NOTE",
            "Token launched directly on Uniswap — no launchpad vetting"))

    # WARN-01: LP lock < 2 years
    lp_lock_until = data.get("lp_lock_until") or ""
    if data.get("lp_locked") and lp_lock_until and lp_lock_until < "2028-01-01":
        flags.append(RedFlag("WARN-01", "WARNING",
            "LP lock under 2 years — limited long-term security"))

    return flags


# ─────────────────────────────────────────────────────────────
# GRADE & CONFIDENCE
# ─────────────────────────────────────────────────────────────

def _apply_grade(raw_score: int, flags: list) -> tuple:
    """
    Returns (final_score, grade).
    WARNING flags → cap grade at F if score < 40, else don't override.
    For this rubric, flags are informational but a low score alone gives F.
    """
    has_warning = any(f.level == "WARNING" for f in flags)

    if raw_score >= 85:
        grade = "A"
    elif raw_score >= 70:
        grade = "B"
    elif raw_score >= 55:
        grade = "C"
    elif raw_score >= 40:
        grade = "D"
    else:
        grade = "F"

    return raw_score, grade


def _assess_confidence(data: dict, defaults_used: list, missing: list) -> str:
    """
    HIGH:   > 85% fields populated, ≤ 2 defaults used
    MEDIUM: 60–85%, 3–5 defaults
    LOW:    < 60% or > 5 defaults
    """
    # 17 main scoring categories
    total_fields = 17
    populated = total_fields - len(set(missing))
    pct = populated / total_fields * 100
    n_defaults = len(set(defaults_used))

    # Debug
    # print(f"DEBUG: populated={populated}, pct={pct:.1f}, n_defaults={n_defaults}")

    if pct > 85 and n_defaults <= 2:
        return "HIGH"
    elif pct >= 60 and n_defaults <= 5:
        return "MEDIUM"
    else:
        return "LOW"


# ─────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────

def score_project(data: dict) -> ScoreResult:
    """
    Score a project from a flat dict of fields.
    Returns a ScoreResult dataclass.
    
    Input fields: revenue_verified, revenue_sources, revenue_dashboard_url, 
                  revenue_dashboard_live, burn_address, burn_tx_count_30d, 
                  treasury_wallet_addresses, treasury_total_usd, treasury_dashboard_url,
                  value_accrual_type, fee_structure, agent_name, agent_description,
                  agent_tech_stack, agent_subagent_count, agent_infrastructure,
                  agent_output_examples, products, product_count_live, operator_name,
                  operator_real_name_known, operator_socials, operator_prior_projects,
                  operator_last_public_activity, operator_communication_frequency,
                  operator_addresses_failures, token_address, launch_platform,
                  lp_locked, lp_lock_until, total_supply, circulating_supply,
                  team_allocation_pct, mint_function_active, top_10_wallet_pct,
                  presale_conducted, last_product_update, updates_last_30d,
                  community_platforms, community_engagement_quality,
                  ecosystem_collaborations, listed_on_aggregators, market_cap_usd,
                  volume_24h_usd, price_ath_usd, price_current_usd, holder_count,
                  volume_trend_7d, project_age_days, narrative_context
    """
    missing = []
    defaults_used = []

    # Score all 17 sub-criteria
    sub_scores = {}
    
    sub_scores["revenue_existence"] = _score_revenue_existence(data, missing, defaults_used)
    sub_scores["value_accrual"] = _score_value_accrual(data, missing, defaults_used)
    sub_scores["treasury_transparency"] = _score_treasury_transparency(data, missing, defaults_used)
    sub_scores["agent_existence"] = _score_agent_existence(data, missing, defaults_used)
    sub_scores["product_reality"] = _score_product_reality(data, missing, defaults_used)
    sub_scores["technical_sophistication"] = _score_technical_sophistication(data, missing, defaults_used)
    sub_scores["operator_identity"] = _score_operator_identity(data, missing, defaults_used)
    sub_scores["skin_in_game"] = _score_skin_in_game(data, missing, defaults_used)
    sub_scores["launch_platform"] = _score_launch_platform(data, missing, defaults_used)
    sub_scores["supply_distribution"] = _score_supply_distribution(data, missing, defaults_used)
    sub_scores["mechanic_sustainability"] = _score_mechanic_sustainability(data, missing, defaults_used)
    sub_scores["shipping_cadence"] = _score_shipping_cadence(data, missing, defaults_used)
    sub_scores["community_engagement"] = _score_community_engagement(data, missing, defaults_used)
    sub_scores["ecosystem_integration"] = _score_ecosystem_integration(data, missing, defaults_used)
    sub_scores["volume_mcap_ratio"] = _score_volume_mcap_ratio(data, missing, defaults_used)
    sub_scores["holder_distribution"] = _score_holder_distribution(data, missing, defaults_used)
    sub_scores["price_trend"] = _score_price_trend(data, missing, defaults_used)

    # Category aggregates (same order as original spec)
    cat_scores = {
        "Revenue Existence": int(sub_scores["revenue_existence"].points),
        "Value Accrual": int(sub_scores["value_accrual"].points),
        "Treasury Transparency": int(sub_scores["treasury_transparency"].points),
        "Agent Existence": int(sub_scores["agent_existence"].points),
        "Product Reality": int(sub_scores["product_reality"].points),
        "Technical Sophistication": int(sub_scores["technical_sophistication"].points),
        "Operator Identity": int(sub_scores["operator_identity"].points),
        "Skin in the Game": int(sub_scores["skin_in_game"].points),
        "Launch Platform & LP": int(sub_scores["launch_platform"].points),
        "Supply Distribution": int(sub_scores["supply_distribution"].points),
        "Mechanic Sustainability": int(sub_scores["mechanic_sustainability"].points),
        "Shipping Cadence": int(sub_scores["shipping_cadence"].points),
        "Community Engagement": int(sub_scores["community_engagement"].points),
        "Ecosystem Integration": int(sub_scores["ecosystem_integration"].points),
        "Volume/MCap Ratio": int(sub_scores["volume_mcap_ratio"].points),
        "Holder Distribution": int(sub_scores["holder_distribution"].points),
        "Price Trend": int(sub_scores["price_trend"].points),
    }

    raw_score = int(sum(cat_scores.values()))
    raw_score = min(100, max(0, raw_score))

    # Red flags
    flags = _check_flags(data, sub_scores)

    # Grade with overrides
    final_score, grade = _apply_grade(raw_score, flags)

    # Confidence
    confidence = _assess_confidence(data, defaults_used, missing)

    # Narrative context
    narrative = data.get("narrative_context", "YELLOW")
    if narrative not in ("GREEN", "YELLOW", "RED"):
        narrative = "YELLOW"

    return ScoreResult(
        total_score       = final_score,
        grade             = grade,
        confidence        = confidence,
        category_scores   = cat_scores,
        sub_scores        = sub_scores,
        red_flags         = flags,
        missing_fields    = list(set(missing)),
        narrative_context = narrative,
        raw_score         = raw_score,
    )


# ─────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 80)
    print("FELIX TEST")
    print("=" * 80)
    
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
            {"name": "Done-For-You Setup", "price": 2499, "is_live": True}
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
        "narrative_context": "YELLOW"
    }
    
    result = score_project(felix_data)
    print(f"Score: {result.total_score}/100 | Grade: {result.grade} | Confidence: {result.confidence}")
    print(f"Raw: {result.raw_score} | Narrative: {result.narrative_context}")
    print(f"Missing: {len(result.missing_fields)} fields")
    print(f"Red Flags: {len(result.red_flags)}")
    
    print("\nCategory Breakdown:")
    for cat, pts in result.category_scores.items():
        print(f"  {cat}: {pts}")
    
    expected = "score 89/100, grade A, confidence HIGH, 0 CRITICAL, 0 WARNING"
    actual = f"score {result.total_score}/100, grade {result.grade}, confidence {result.confidence}, 0 CRITICAL, 0 WARNING"
    
    if result.total_score == 89 and result.grade == "A" and result.confidence == "HIGH":
        print(f"\n✅ FELIX PASS — {actual}")
    else:
        print(f"\n❌ FELIX FAIL — expected: {expected} | actual: {actual}")

    # ────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("ROBOTMONEY TEST")
    print("=" * 80)

    robotmoney_data = {
        "revenue_verified": True,
        "revenue_sources": ["on_chain_fees", "management_fee", "exit_fee"],
        "revenue_dashboard_live": True,
        "revenue_dashboard_url": "https://basescan.org",
        "burn_address": "0x000...dEaD",
        "burn_tx_count_30d": 28,
        "treasury_dashboard_url": "https://basescan.org",
        "treasury_total_usd": 85000,
        "value_accrual_type": "buyback_burn",
        "fee_structure": {
            "management_fee_pct": 2.0,
            "exit_fee_pct": 0.25,
            "trading_fee_pct": 1.0
        },
        "agent_name": "RobotMoney",
        "agent_tech_stack": ["custom", "erc4626"],
        "agent_subagent_count": 0,
        "agent_infrastructure": "cloud",
        "agent_output_examples": ["https://basescan.org"],
        "products": [{"name": "ERC-4626 Vault", "price": 0, "is_live": True}],
        "product_count_live": 1,
        "operator_name": "tomosman",
        "operator_real_name_known": False,
        "operator_communication_frequency": "daily",
        "operator_addresses_failures": True,
        "launch_platform": "bankr",
        "lp_locked": True,
        "lp_lock_until": "2100-01-01",
        "total_supply": 1_000_000_000,
        "team_allocation_pct": 0,
        "mint_function_active": False,
        "top_10_wallet_pct": 14,
        "presale_conducted": False,
        "last_product_update": "2026-03-14",
        "updates_last_30d": 12,
        "community_platforms": ["x", "farcaster"],
        "community_engagement_quality": "high",
        "ecosystem_collaborations": ["generative_ventures", "juno"],
        "listed_on_aggregators": ["coingecko", "dexscreener"],
        "market_cap_usd": 2_400_000,
        "volume_24h_usd": 1_200_000,
        "price_ath_usd": 0.0000257,
        "price_current_usd": 0.0000247,
        "holder_count": 890,
        "volume_trend_7d": "stable",
        "project_age_days": 30,
        "narrative_context": "YELLOW"
    }
    
    result = score_project(robotmoney_data)
    print(f"Score: {result.total_score}/100 | Grade: {result.grade} | Confidence: {result.confidence}")
    print(f"Raw: {result.raw_score} | Narrative: {result.narrative_context}")
    print(f"Missing: {len(result.missing_fields)} fields")
    print(f"Red Flags: {len(result.red_flags)}")
    if result.red_flags:
        for flag in result.red_flags:
            print(f"  {flag.id} ({flag.level}): {flag.text}")
    
    print("\nCategory Breakdown:")
    for cat, pts in result.category_scores.items():
        print(f"  {cat}: {pts}")
    
    expected = "score 90/100, grade A, confidence HIGH, NOTE-02 flag (pseudonymous)"
    has_note02 = any(f.id == "NOTE-02" for f in result.red_flags)
    
    if result.total_score == 90 and result.grade == "A" and result.confidence == "HIGH":
        print(f"\n✅ ROBOTMONEY PASS — score {result.total_score}/100, grade {result.grade}, confidence {result.confidence}")
    else:
        print(f"\n❌ ROBOTMONEY FAIL — expected: {expected}")

    # ────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SAIRI TEST")
    print("=" * 80)

    sairi_data = {
        "revenue_verified": False,
        "agent_name": "SAIRI",
        "agent_tech_stack": [],
        "agent_subagent_count": 0,
        "products": [],
        "product_count_live": 0,
        "operator_name": "Santiago Siri",
        "operator_real_name_known": True,
        "operator_communication_frequency": "monthly",
        "operator_addresses_failures": False,
        "operator_prior_projects": ["democracy_earth"],
        "launch_platform": "uniswap_raw",
        "lp_locked": True,
        "lp_lock_until": "2027-01-01",
        "total_supply": 100_000_000_000,
        "team_allocation_pct": 0,
        "mint_function_active": False,
        "top_10_wallet_pct": 28,
        "presale_conducted": False,
        "last_product_update": "2026-01-01",
        "updates_last_30d": 0,
        "community_platforms": [],
        "community_engagement_quality": "low",
        "ecosystem_collaborations": [],
        "market_cap_usd": 684_000,
        "volume_24h_usd": 168_000,
        "price_ath_usd": 0.0000186,
        "price_current_usd": 0.0000068,
        "holder_count": 340,
        "volume_trend_7d": "declining",
        "project_age_days": 60,
        "narrative_context": "YELLOW"
    }
    
    result = score_project(sairi_data)
    print(f"Score: {result.total_score}/100 | Grade: {result.grade} | Confidence: {result.confidence}")
    print(f"Raw: {result.raw_score} | Narrative: {result.narrative_context}")
    print(f"Missing: {len(result.missing_fields)} fields")
    print(f"Red Flags: {len(result.red_flags)}")
    if result.red_flags:
        for flag in result.red_flags:
            print(f"  {flag.id} ({flag.level}): {flag.text}")
    
    print("\nCategory Breakdown:")
    for cat, pts in result.category_scores.items():
        print(f"  {cat}: {pts}")
    
    expected = "score 31/100, grade F, confidence MEDIUM"
    if result.total_score == 31 and result.grade == "F" and result.confidence == "MEDIUM":
        print(f"\n✅ SAIRI PASS — {expected}")
    else:
        print(f"\n❌ SAIRI FAIL — expected: {expected} | actual: score {result.total_score}/100, grade {result.grade}, confidence {result.confidence}")

    print("\n" + "=" * 80)
