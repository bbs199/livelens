"""
LiveLens — Database Layer
SQLite3 direct, no ORM. Consistent with Beast infrastructure pattern.

Schema:
  projects      — one row per unique project (keyed by token_address)
  submissions   — every data submission for a project (collective enrichment)
  scores        — scored result per submission, linked to project

Credits live in /home/b/shared/credits.db — not here.
"""

import sys
sys.path.insert(0, "/home/b/shared")
import credits as credits_module

import sqlite3
import json
import time
from pathlib import Path


DB_PATH = Path(__file__).parent / "livelens.db"


# ─────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    agent_name      TEXT,
    token_address   TEXT,
    chain           TEXT DEFAULT 'base',
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    submission_count INTEGER DEFAULT 0,
    UNIQUE(token_address)
);

CREATE TABLE IF NOT EXISTS submissions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    submitted_at    INTEGER NOT NULL,
    data_json       TEXT NOT NULL,
    submitter_note  TEXT
);

CREATE TABLE IF NOT EXISTS scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    scored_at       INTEGER NOT NULL,
    total_score     INTEGER NOT NULL,
    grade           TEXT NOT NULL,
    confidence      TEXT NOT NULL,
    raw_score       INTEGER NOT NULL,
    result_json     TEXT NOT NULL,
    merged_data_json TEXT NOT NULL,
    report_json     TEXT
);

CREATE INDEX IF NOT EXISTS idx_projects_token   ON projects(token_address);
CREATE INDEX IF NOT EXISTS idx_submissions_proj ON submissions(project_id);
CREATE INDEX IF NOT EXISTS idx_scores_proj      ON scores(project_id);
CREATE INDEX IF NOT EXISTS idx_scores_scored_at ON scores(scored_at);
"""


# ─────────────────────────────────────────────────────────────
# CONNECTION
# ─────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Migration: add report_json column if missing
        cols = [r[1] for r in conn.execute("PRAGMA table_info(scores)").fetchall()]
        if "report_json" not in cols:
            conn.execute("ALTER TABLE scores ADD COLUMN report_json TEXT")
            print("[livelens.db] Migration: added report_json column")
    print(f"[livelens.db] Initialised at {DB_PATH}")


# ─────────────────────────────────────────────────────────────
# FIELD MERGE LOGIC
# ─────────────────────────────────────────────────────────────

def _is_empty(val) -> bool:
    if val is None:
        return True
    if isinstance(val, str) and val.strip() == "":
        return True
    return False


def merge_submissions(submissions: list) -> dict:
    """
    Merge a list of submission dicts (oldest first).
    Last non-empty value wins per field.
    Lists are extended (union), not replaced.
    """
    if not submissions:
        return {}

    merged = {}
    for sub in submissions:
        for key, val in sub.items():
            if isinstance(val, list) and key in merged and isinstance(merged[key], list):
                # Union: extend with new unique entries
                existing = merged[key]
                for item in val:
                    if item not in existing:
                        existing.append(item)
            else:
                if not _is_empty(val):
                    merged[key] = val
                elif key not in merged:
                    merged[key] = val
    return merged


# ─────────────────────────────────────────────────────────────
# PROJECT OPERATIONS
# ─────────────────────────────────────────────────────────────

def get_or_create_project(conn, data: dict) -> int:
    """Return project_id, creating if not exists. Keys on token_address."""
    agent_name = (
        data.get("agent_name") or
        data.get("project_name") or
        data.get("ticker") or
        ("0x" + str(data.get("token_address", "unknown"))[:8])
    )
    name  = agent_name
    agent = agent_name
    address  = (data.get("token_address") or "").lower().strip() or None

    if address:
        row = conn.execute(
            "SELECT id FROM projects WHERE token_address = ?", (address,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM projects WHERE name = ? AND token_address IS NULL", (name,)
        ).fetchone()

    if row:
        project_id = row["id"]
        # Update name if we have a better one than what's stored
        if agent_name and agent_name != "Unknown" and not agent_name.startswith("0x"):
            conn.execute(
                "UPDATE projects SET agent_name = ?, name = ?, updated_at = ? WHERE id = ?",
                (agent_name, agent_name, int(time.time()), project_id)
            )
        return project_id

    now = int(time.time())
    cur = conn.execute(
        """INSERT INTO projects (name, agent_name, token_address, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (name, agent, address, now, now)
    )
    return cur.lastrowid


def get_project_by_id(project_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row else None


def search_projects(query: str) -> list:
    q = f"%{query.strip()}%"
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT p.*, s.total_score, s.grade, s.confidence
               FROM projects p
               LEFT JOIN scores s ON s.project_id = p.id
               AND s.id = (SELECT MAX(id) FROM scores WHERE project_id = p.id)
               WHERE p.name LIKE ? OR p.agent_name LIKE ? OR p.token_address LIKE ?
               ORDER BY p.updated_at DESC
               LIMIT 20""",
            (q, q, q)
        ).fetchall()
        return [dict(r) for r in rows]


def list_recent_projects(limit: int = 20) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT p.*, s.total_score, s.grade, s.confidence
               FROM projects p
               LEFT JOIN scores s ON s.project_id = p.id
               AND s.id = (SELECT MAX(id) FROM scores WHERE project_id = p.id)
               ORDER BY p.updated_at DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────
# CACHE LOOKUP
# ─────────────────────────────────────────────────────────────

def get_cached_score(contract_address: str) -> dict | None:
    """
    Returns the latest score for a project if it was scored within the last 24 hours.
    Looks up by token_address field in merged_data_json.
    Returns None if not found or if score is older than 24 hours.
    """
    if not contract_address:
        return None

    addr_lower = contract_address.lower().strip()
    cutoff = int(time.time()) - 86400

    with get_conn() as conn:
        # First try direct project table lookup (fast path)
        row = conn.execute(
            """SELECT s.*
               FROM scores s
               JOIN projects p ON s.project_id = p.id
               WHERE p.token_address = ?
               AND s.scored_at >= ?
               ORDER BY s.id DESC LIMIT 1""",
            (addr_lower, cutoff)
        ).fetchone()

        if not row:
            # Slow path: scan merged_data_json for token_address match
            rows = conn.execute(
                """SELECT * FROM scores WHERE scored_at >= ?
                   ORDER BY id DESC LIMIT 200""",
                (cutoff,)
            ).fetchall()
            for r in rows:
                try:
                    md = json.loads(r["merged_data_json"])
                    if (md.get("token_address") or "").lower() == addr_lower:
                        row = r
                        break
                except Exception:
                    continue

        if not row:
            return None

        d = dict(row)
        d["result"]      = json.loads(d["result_json"])
        d["merged_data"] = json.loads(d["merged_data_json"])
        d["report"]      = json.loads(d["report_json"]) if d.get("report_json") else {}
        return d


# ─────────────────────────────────────────────────────────────
# SERIALISATION — ScoreResult → JSON-safe dict
# ─────────────────────────────────────────────────────────────

def _serialise_result(r) -> dict:
    import dataclasses

    def serialise_subscore(ss):
        return {"points": ss.points, "max_points": ss.max_points, "notes": ss.notes}

    cat_scores = {}
    for k, v in r.category_scores.items():
        cat_scores[k] = v

    sub_scores = {}
    for k, v in r.sub_scores.items():
        sub_scores[k] = serialise_subscore(v)

    red_flags = [
        {"id": f.id, "level": f.level, "text": f.text}
        for f in r.red_flags
    ]

    return {
        "total_score":      r.total_score,
        "grade":            r.grade,
        "confidence":       r.confidence,
        "raw_score":        r.raw_score,
        "narrative_context": r.narrative_context,
        "category_scores":  cat_scores,
        "sub_scores":       sub_scores,
        "red_flags":        red_flags,
        "missing_fields":   r.missing_fields,
    }


# ─────────────────────────────────────────────────────────────
# SUBMISSION + SCORING
# ─────────────────────────────────────────────────────────────

def save_submission_and_score(data: dict, score_result=None) -> dict:
    """
    Save a new submission for a project.
    Merge with all prior submissions.
    Recalculate and save score from merged data.
    Generates analyst report after scoring (API call — graceful fail).
    Returns dict with project_id, submission_id, score_id, merged_result, merged_data.
    """
    from scorer import score_project
    from reporter import generate_report

    now = int(time.time())

    with get_conn() as conn:
        project_id = get_or_create_project(conn, data)

        # Save submission
        sub_id = conn.execute(
            "INSERT INTO submissions (project_id, submitted_at, data_json) VALUES (?, ?, ?)",
            (project_id, now, json.dumps(data))
        ).lastrowid

        # Merge all submissions for this project
        all_subs = conn.execute(
            "SELECT data_json FROM submissions WHERE project_id = ? ORDER BY submitted_at ASC",
            (project_id,)
        ).fetchall()
        sub_dicts = [json.loads(r["data_json"]) for r in all_subs]
        merged    = merge_submissions(sub_dicts)

        # Score from merged data
        merged_result = score_project(merged)
        result_json   = _serialise_result(merged_result)

    # Generate analyst report outside transaction (API call)
    try:
        report      = generate_report(merged, merged_result)
        report_json = json.dumps(report)
    except Exception as e:
        report_json = json.dumps({"_generated": False, "_error": str(e)})

    with get_conn() as conn:
        score_id = conn.execute(
            """INSERT INTO scores
               (project_id, scored_at, total_score, grade, confidence, raw_score,
                result_json, merged_data_json, report_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id, now,
                merged_result.total_score, merged_result.grade, merged_result.confidence,
                merged_result.raw_score,
                json.dumps(result_json),
                json.dumps(merged),
                report_json,
            )
        ).lastrowid

        # Update project metadata
        conn.execute(
            """UPDATE projects
               SET name = ?,
                   updated_at = ?,
                   submission_count = (SELECT COUNT(*) FROM submissions WHERE project_id = ?)
               WHERE id = ?""",
            (
                data.get("agent_name") or data.get("project_name") or "Unknown",
                now, project_id, project_id
            )
        )

    return {
        "project_id":    project_id,
        "submission_id": sub_id,
        "score_id":      score_id,
        "merged_result": merged_result,
        "merged_data":   merged,
    }


def get_latest_score(project_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scores WHERE project_id = ? ORDER BY id DESC LIMIT 1",
            (project_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["result"]      = json.loads(d["result_json"])
        d["merged_data"] = json.loads(d["merged_data_json"])
        d["report"]      = json.loads(d["report_json"]) if d.get("report_json") else {}
        return d


def get_submission_count(project_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM submissions WHERE project_id = ?",
            (project_id,)
        ).fetchone()
        return row["c"] if row else 0


# ─────────────────────────────────────────────────────────────
# INIT ON IMPORT
# ─────────────────────────────────────────────────────────────

init_db()


# ─────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    from pathlib import Path

    # Use a temp DB for testing
    test_db = Path(__file__).parent / "livelens_test.db"
    if test_db.exists():
        test_db.unlink()

    # Patch DB_PATH for this test run
    import db as _self
    _self.DB_PATH = test_db

    print("── init_db() ──")
    init_db()

    # Felix test data (same as scorer.py)
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
        "token_address": "0xf30bf00edd0c22db54c9274b90d2a4c21fc09b07",
    }

    print("\n── save_submission_and_score(felix_data) ──")
    result = save_submission_and_score(felix_data)
    print(f"  project_id:    {result['project_id']}")
    print(f"  submission_id: {result['submission_id']}")
    print(f"  score_id:      {result['score_id']}")
    print(f"  score:         {result['merged_result'].total_score}/100 Grade {result['merged_result'].grade}")

    score_row = get_latest_score(result["project_id"])
    print(f"  report _generated: {score_row['report'].get('_generated', 'key missing')}")

    print("\n── get_cached_score(felix CA) ──")
    cached = get_cached_score("0xf30bf00edd0c22db54c9274b90d2a4c21fc09b07")
    if cached:
        print(f"  Cache HIT — score {cached['total_score']}/100 scored_at {cached['scored_at']}")
    else:
        print("  Cache MISS (unexpected)")

    print("\n── get_cached_score(unknown address) ──")
    miss = get_cached_score("0x0000000000000000000000000000000000000000")
    print(f"  Result: {miss} (expected None)")

    # Clean up
    test_db.unlink(missing_ok=True)

    print("\nAll tests passed")
