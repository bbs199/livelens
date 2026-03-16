"""
LiveLens — Flask Application
Runs on port 8526. Consistent with Beast systemd service pattern.
"""

import sys
sys.path.insert(0, "/home/b/shared")
import credits as credits_module
import config as shared_config
import os

from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from scorer import score_project, ScoreResult
from db import (
    save_submission_and_score, get_latest_score, get_cached_score,
    list_recent_projects, search_projects, get_project_by_id,
    get_submission_count, _serialise_result,
)
from scraper import scrape, map_to_form_fields
from researcher import research_project, merge_research_with_scrape
import json
import urllib.parse

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "livelens-dev-secret")


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _parse_form(form) -> dict:
    """Parse Flask form data into scorer-compatible dict (LiveLens fields)."""

    def flt(key):
        val = form.get(key, "").strip()
        try:
            return float(val) if val else None
        except ValueError:
            return None

    def nt(key):
        val = form.get(key, "").strip()
        try:
            return int(float(val)) if val else None
        except ValueError:
            return None

    def txt(key):
        return form.get(key, "").strip() or None

    def sel(key):
        return form.get(key, "").strip() or None

    def chk(key):
        return form.get(key) == "on"

    def lst(key):
        """Parse a comma-separated text field into a list of strings."""
        val = form.get(key, "").strip()
        if not val:
            return []
        return [item.strip() for item in val.split(",") if item.strip()]

    # Products: submitted as JSON textarea
    products = []
    products_raw = form.get("products", "").strip()
    if products_raw:
        try:
            products = json.loads(products_raw)
            if not isinstance(products, list):
                products = []
        except Exception:
            products = []

    return {
        # Identity
        "agent_name":                       txt("agent_name"),
        "operator_name":                    txt("operator_name"),
        "operator_real_name_known":         chk("operator_real_name_known") if "operator_real_name_known" in form else None,
        "token_address":                    txt("token_address"),
        "launch_platform":                  sel("launch_platform"),

        # Revenue
        "revenue_verified":                 chk("revenue_verified") if "revenue_verified" in form else None,
        "revenue_sources":                  lst("revenue_sources"),
        "revenue_dashboard_url":            txt("revenue_dashboard_url"),
        "revenue_dashboard_live":           chk("revenue_dashboard_live") if "revenue_dashboard_live" in form else None,
        "value_accrual_type":               sel("value_accrual_type"),
        "burn_address":                     txt("burn_address"),
        "burn_tx_count_30d":                nt("burn_tx_count_30d"),
        "treasury_wallet_addresses":        lst("treasury_wallet_addresses"),
        "treasury_total_usd":               flt("treasury_total_usd"),
        "treasury_dashboard_url":           txt("treasury_dashboard_url"),
        "fee_structure":                    None,  # future: parse from textarea

        # Agent
        "agent_tech_stack":                 lst("agent_tech_stack"),
        "agent_subagent_count":             nt("agent_subagent_count"),
        "agent_infrastructure":             txt("agent_infrastructure"),
        "agent_output_examples":            lst("agent_output_examples"),

        # Products
        "products":                         products,
        "product_count_live":               nt("product_count_live"),

        # Operator
        "operator_communication_frequency": sel("operator_communication_frequency"),
        "operator_addresses_failures":      chk("operator_addresses_failures") if "operator_addresses_failures" in form else None,
        "operator_prior_projects":          lst("operator_prior_projects"),

        # Token / launch
        "lp_locked":                        chk("lp_locked") if "lp_locked" in form else None,
        "lp_lock_until":                    txt("lp_lock_until"),
        "total_supply":                     flt("total_supply"),
        "circulating_supply":               flt("circulating_supply"),
        "team_allocation_pct":              flt("team_allocation_pct"),
        "mint_function_active":             chk("mint_function_active") if "mint_function_active" in form else None,
        "top_10_wallet_pct":                flt("top_10_wallet_pct"),
        "presale_conducted":                chk("presale_conducted") if "presale_conducted" in form else None,

        # Activity
        "last_product_update":              txt("last_product_update"),
        "updates_last_30d":                 nt("updates_last_30d"),
        "community_platforms":              lst("community_platforms"),
        "community_engagement_quality":     sel("community_engagement_quality"),
        "ecosystem_collaborations":         lst("ecosystem_collaborations"),
        "listed_on_aggregators":            lst("listed_on_aggregators"),

        # Market
        "market_cap_usd":                   flt("market_cap_usd"),
        "volume_24h_usd":                   flt("volume_24h_usd"),
        "price_ath_usd":                    flt("price_ath_usd"),
        "price_current_usd":                flt("price_current_usd"),
        "holder_count":                     nt("holder_count"),
        "volume_trend_7d":                  sel("volume_trend_7d"),
        "project_age_days":                 nt("project_age_days"),

        # Narrative
        "narrative_context":                sel("narrative_context") or shared_config.NARRATIVE_STATE,
    }


# ─────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    recent = list_recent_projects(20)
    wallet  = session.get("wallet")
    balance = credits_module.get_balance(wallet) if wallet else 0
    return render_template("index.html", recent=recent, wallet=wallet, balance=balance)


@app.route("/search")
def search():
    if not session.get("wallet"):
        return redirect(url_for("index") + "?msg=connect_wallet")
    q       = request.args.get("q", "").strip()
    results = search_projects(q) if q else []
    wallet  = session.get("wallet")
    balance = credits_module.get_balance(wallet) if wallet else 0
    return render_template("search.html", results=results, query=q,
                           wallet=wallet, balance=balance)


@app.route("/scrape", methods=["GET"])
def scrape_form():
    if not session.get("wallet"):
        return redirect(url_for("index") + "?msg=connect_wallet")
    error  = request.args.get("error", "")
    wallet = session.get("wallet")
    balance = credits_module.get_balance(wallet) if wallet else 0
    return render_template("scrape.html", error=error, wallet=wallet, balance=balance)


@app.route("/scrape", methods=["POST"])
def scrape_submit():
    blurb = request.form.get("blurb", "").strip()
    if not blurb:
        return render_template("scrape.html", error="Please paste a project blurb.")
    try:
        scraped     = scrape(blurb)
        form_fields = map_to_form_fields(scraped)

        # Research mode — runs after scrape, costs 2 additional credits
        project_name     = scraped.get("project_name") or scraped.get("agent_name", "")
        contract_address = scraped.get("token_address", "")
        website_url      = scraped.get("website_url", "")
        operator_handle  = scraped.get("twitter_url", "")
        ticker           = scraped.get("ticker", "")

        print(f"SCRAPE DEBUG project_name='{project_name}' contract='{contract_address}'")
        print(f"SCRAPE DEBUG research will fire: {bool(project_name or contract_address)}")
        if project_name or contract_address:
            try:
                research_data = research_project(
                    project_name=project_name,
                    contract_address=contract_address,
                    website_url=website_url,
                    operator_handle=operator_handle,
                    ticker=ticker,
                )
                scraped     = merge_research_with_scrape(scraped, research_data)
                form_fields = map_to_form_fields(scraped)
                form_fields["_scrape_meta"]["research_confidence"] = scraped.get("_research_confidence", "low")
                form_fields["_scrape_meta"]["research_notes"]      = scraped.get("_research_notes", "")
            except Exception as e:
                app.logger.error(f"Research failed: {e}")

        # Score directly from research + scrape data
        from db import save_submission_and_score
        score_data = dict(form_fields)
        # Sanitise — remove None values so scorer uses conservative defaults
        score_data = {k: v for k, v in score_data.items()
                      if v is not None and not str(k).startswith('_')}
        result = save_submission_and_score(score_data, None)
        return redirect(url_for("project_result",
            project_id=result["project_id"]))
    except Exception as e:
        import traceback
        app.logger.error(f"Scrape failed: {traceback.format_exc()}")
        return render_template("scrape.html", error=f"Scrape failed: {e}")


@app.route("/analyse", methods=["GET"])
def analyse_form():
    if not session.get("wallet"):
        return redirect(url_for("index") + "?msg=connect_wallet")
    prefill_data = {}
    scrape_meta  = {}
    project_id   = request.args.get("project_id")
    error        = request.args.get("error", "")

    # Coming from "Add Data" on a result page
    if project_id:
        try:
            existing = get_latest_score(int(project_id))
            if existing and existing.get("merged_data"):
                prefill_data = {k: v for k, v in existing["merged_data"].items()
                                if not k.startswith("_")}
                scrape_meta  = {"prefill_source": "existing", "project_id": project_id}
        except Exception:
            pass

    # Coming from scraper
    elif request.args.get("prefill"):
        try:
            prefill_data = json.loads(urllib.parse.unquote(request.args.get("prefill")))
            scrape_meta  = prefill_data.pop("_scrape_meta", {})
            prefill_data.pop("_token_utility", None)
        except Exception:
            pass

    wallet  = session.get("wallet")
    balance = credits_module.get_balance(wallet) if wallet else 0
    return render_template("form.html", prefill=prefill_data, scrape_meta=scrape_meta,
                           error=error, wallet=wallet, balance=balance)


@app.route("/analyse", methods=["POST"])
def analyse_submit():
    contract_address = request.form.get("token_address", "").strip().lower()

    # Cache check — free for everyone
    if contract_address:
        cached = get_cached_score(contract_address)
        if cached:
            return redirect(url_for("project_result",
                                    project_id=cached["project_id"]))

    # Must be connected
    wallet = session.get("wallet")
    ip     = request.remote_addr
    if not wallet:
        return redirect(url_for("scrape_form",
                                error="Connect your wallet to score a project."))

    # Credits required — no free tier
    credits_to_use = 15
    credit_result  = credits_module.check_and_use_credit(wallet, credits_to_use)
    if not credit_result["allowed"]:
        return redirect(url_for("buy_credits",
                                message="Insufficient credits. Buy credits to score a project."))

    data   = _parse_form(request.form)
    result = save_submission_and_score(data, None)
    return redirect(url_for("project_result", project_id=result["project_id"]))


@app.route("/project/<int:project_id>")
def project_result(project_id):
    project = get_project_by_id(project_id)
    if not project:
        return "Project not found", 404
    score     = get_latest_score(project_id)
    sub_count = get_submission_count(project_id)
    wallet    = session.get("wallet")
    balance   = credits_module.get_balance(wallet) if wallet else 0
    return render_template("result.html", project=project, score=score,
                           sub_count=sub_count, wallet=wallet, balance=balance)


# ─────────────────────────────────────────────────────────────
# CREDITS / WALLET ROUTES
# ─────────────────────────────────────────────────────────────

@app.route("/buy")
def buy_credits():
    message = request.args.get("message", "")
    wallet  = session.get("wallet")
    balance = credits_module.get_balance(wallet) if wallet else 0
    tiers   = shared_config.CREDIT_TIERS
    receiving_wallet = shared_config.RECEIVING_WALLET
    return render_template("buy.html", message=message, wallet=wallet,
                           balance=balance, tiers=tiers,
                           receiving_wallet=receiving_wallet)


@app.route("/connect", methods=["POST"])
def connect_wallet():
    data    = request.get_json(force=True)
    address = (data.get("address") or "").strip().lower()
    if not address or not address.startswith("0x") or len(address) != 42:
        return jsonify({"error": "Invalid wallet address"}), 400
    credits_module.get_or_create_wallet(address)
    session["wallet"] = address
    balance = credits_module.get_balance(address)
    return jsonify({"address": address, "balance": balance})


@app.route("/disconnect", methods=["POST"])
def disconnect_wallet():
    session.pop("wallet", None)
    return jsonify({"ok": True})


@app.route("/api/balance")
def api_balance():
    wallet = session.get("wallet")
    if not wallet:
        return jsonify({"balance": 0, "connected": False})
    return jsonify({"balance": credits_module.get_balance(wallet), "connected": True})


@app.route("/buy/create_order", methods=["POST"])
def buy_create_order():
    wallet = session.get("wallet")
    if not wallet:
        return jsonify({"success": False, "reason": "not_connected"}), 401
    data = request.get_json(force=True)
    tier_index = data.get("tier_index")
    if tier_index is None or tier_index not in range(4):
        return jsonify({"success": False, "reason": "invalid_tier"}), 400
    try:
        order = credits_module.create_pending_order(wallet, tier_index)
        return jsonify({"success": True, **order})
    except Exception as e:
        return jsonify({"success": False, "reason": str(e)}), 500


@app.route("/buy/verify", methods=["POST"])
def buy_verify():
    wallet = session.get("wallet")
    if not wallet:
        return jsonify({"success": False, "reason": "not_connected"}), 401
    data    = request.get_json(force=True)
    tx_hash = (data.get("tx_hash") or "").strip()
    if not tx_hash:
        return jsonify({"success": False, "reason": "no_tx_hash"}), 400
    result = credits_module.claim_order(tx_hash, wallet, "livelens")
    return jsonify(result)


# ─────────────────────────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────────────────────────

@app.route("/api/score/<contract_address>")
def api_score_by_address(contract_address):
    """Returns latest score JSON for a contract address."""
    cached = get_cached_score(contract_address.lower().strip())
    if not cached:
        return jsonify({"error": "Not found or score expired"}), 404
    return jsonify({
        "contract_address": contract_address,
        "project_id":       cached["project_id"],
        "total_score":      cached["total_score"],
        "grade":            cached["grade"],
        "confidence":       cached["confidence"],
        "scored_at":        cached["scored_at"],
        "result":           cached["result"],
    })


# ─────────────────────────────────────────────────────────────
# TWEET GENERATOR
# ─────────────────────────────────────────────────────────────

@app.route("/project/<int:project_id>/tweet", methods=["POST"])
def generate_tweet_route(project_id):
    wallet = session.get("wallet")
    if not wallet:
        return jsonify({"error": "wallet_required"}), 401

    # Check and deduct 2 credits
    credit_result = credits_module.check_and_use_credit(wallet, 2)
    if not credit_result.get("allowed"):
        return jsonify({"error": "insufficient_credits", "balance": credit_result.get("balance", 0)}), 402

    project = get_project_by_id(project_id)
    if not project:
        return jsonify({"error": "project_not_found"}), 404

    score_row = get_latest_score(project_id)
    if not score_row:
        return jsonify({"error": "no_score"}), 404

    result      = score_row.get("result", {})
    merged_data = score_row.get("merged_data", {}) or {}

    from reporter import generate_tweet
    try:
        tweet = generate_tweet(
            project_name=project.get("agent_name") or project.get("name", "Unknown"),
            score=result.get("total_score", 0),
            grade=result.get("grade", "F"),
            score_result=result,
            merged_data=merged_data,
        )
        return jsonify({"tweet": tweet, "credits_remaining": credit_result.get("balance", 0)})
    except Exception as e:
        app.logger.error(f"Tweet generation failed: {e}")
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8526, debug=False)
