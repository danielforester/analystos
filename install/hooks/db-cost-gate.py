#!/usr/bin/env python3
"""
db-cost-gate — Pre-tool-use hook for AnalystOS.

Inspects tool inputs that appear to contain SQL queries. For SELECT queries
on supported dialects (Oracle, Athena), it checks whether the active connection
has cost thresholds configured and, if so, blocks the query with an estimate
prompt so the user can confirm before proceeding.

This hook does NOT run the EXPLAIN itself — Claude/the LLM does that via the
db-cost-check skill. This hook's job is to intercept SQL tool calls and ask
Claude to run a cost check before proceeding.

Input  (stdin): JSON matching the Claude Code pre-tool-use hook schema
Output (stdout): JSON with {"action": "allow"} or {"action": "block", "message": "..."}
"""

import json
import re
import sys
from pathlib import Path


# ── Patterns that indicate this is a SELECT-style query worth checking ────────
SELECT_PATTERNS = re.compile(
    r"^\s*(SELECT|WITH\s+\w|EXPLAIN)\b",
    flags=re.IGNORECASE | re.MULTILINE,
)

# ── Confirmation phrase ───────────────────────────────────────────────────────
# If the user's message contains this, skip the cost gate (they already confirmed).
CONFIRM_PHRASES = [
    "confirmed",
    "confirm cost",
    "proceed anyway",
    "i understand the cost",
    "cost confirmed",
    "yes, proceed",
    "yes proceed",
]

# ── Dialects that have real cost signals ─────────────────────────────────────
COST_SIGNAL_DIALECTS = {"oracle", "athena"}

# ── Connections config path (relative to project root) ───────────────────────
CONNECTIONS_PATH = Path(".claude/db-connections/active.yaml")


def load_active_connection() -> dict | None:
    """Load and parse the active connection config."""
    try:
        import yaml  # type: ignore
    except ImportError:
        # yaml not available — skip cost gate
        sys.stderr.write("db-cost-gate: PyYAML not installed, skipping cost gate.\n")
        return None

    if not CONNECTIONS_PATH.exists():
        sys.stderr.write(
            f"db-cost-gate: {CONNECTIONS_PATH} not found, skipping cost gate.\n"
        )
        return None

    try:
        with open(CONNECTIONS_PATH) as f:
            config = yaml.safe_load(f)
    except Exception as e:
        sys.stderr.write(f"db-cost-gate: Could not read connections config: {e}\n")
        return None

    active_name = config.get("active")
    if not active_name:
        return None

    connections = config.get("connections", [])
    for conn in connections:
        if conn.get("name") == active_name:
            return conn

    return None


def extract_sql_query(tool_input: dict) -> str | None:
    """Extract a SQL query string from the tool input, if present."""
    for field in ("query", "sql", "statement"):
        if field in tool_input and isinstance(tool_input[field], str):
            return tool_input[field]

    # Bash commands: look for SQL-like content
    command = tool_input.get("command", "")
    if isinstance(command, str) and SELECT_PATTERNS.search(command):
        return command

    return None


def is_select_query(sql: str) -> bool:
    """Return True if the query looks like a SELECT (not DDL/DML)."""
    return bool(SELECT_PATTERNS.search(sql))


def user_confirmed(hook_data: dict) -> bool:
    """Check if the user has already acknowledged cost and confirmed."""
    user_message = (hook_data.get("user_message", "") or "").lower()
    for phrase in CONFIRM_PHRASES:
        if phrase in user_message:
            return True
    return False


def format_threshold(conn: dict) -> str:
    """Return a human-readable description of the cost threshold."""
    thresholds = conn.get("cost_thresholds") or {}
    db_type = conn.get("type", "")

    if db_type == "oracle":
        rows = thresholds.get("warn_rows")
        if rows:
            return f"{rows:,} rows"

    if db_type == "athena":
        raw_bytes = thresholds.get("warn_bytes")
        if raw_bytes:
            gb = raw_bytes / (1024 ** 3)
            return f"{gb:.1f} GB scanned"

    return "the configured threshold"


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            print(json.dumps({"action": "allow"}))
            return

        hook_data = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"db-cost-gate: Could not parse hook input: {e}\n")
        print(json.dumps({"action": "allow"}))
        return

    tool_input = hook_data.get("input", {}) or hook_data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        print(json.dumps({"action": "allow"}))
        return

    # Extract SQL from the tool call
    sql = extract_sql_query(tool_input)
    if not sql or not is_select_query(sql):
        # Not a SELECT — let db-safety handle any blocked keywords
        print(json.dumps({"action": "allow"}))
        return

    # Load the active connection to check type and thresholds
    conn = load_active_connection()
    if not conn:
        # No connection config — allow (can't check cost without knowing the DB)
        print(json.dumps({"action": "allow"}))
        return

    db_type = conn.get("type", "").lower()

    # SQLite: no cost gate
    if db_type not in COST_SIGNAL_DIALECTS:
        print(json.dumps({"action": "allow"}))
        return

    # Check if thresholds are configured
    thresholds = conn.get("cost_thresholds") or {}
    has_threshold = bool(
        thresholds.get("warn_rows") or thresholds.get("warn_bytes")
    )
    if not has_threshold:
        # Thresholds not configured — allow but note it
        sys.stderr.write(
            "db-cost-gate: No cost thresholds configured for this connection.\n"
        )
        print(json.dumps({"action": "allow"}))
        return

    # Check if user already confirmed
    if user_confirmed(hook_data):
        sys.stderr.write("db-cost-gate: Cost confirmed by user. Allowing.\n")
        print(json.dumps({"action": "allow"}))
        return

    # Block and ask Claude to run a cost check before proceeding
    threshold_desc = format_threshold(conn)
    display_name = conn.get("display_name", db_type.title())
    sql_preview = sql.strip()[:200] + ("..." if len(sql.strip()) > 200 else "")

    message = (
        f"⚠️ **Cost check required before executing**\n\n"
        f"**Connection:** {display_name} ({db_type.title()})\n"
        f"**Threshold:** {threshold_desc}\n\n"
        f"**Query preview:**\n```sql\n{sql_preview}\n```\n\n"
        f"Before running this query, please:\n"
        f"1. Use the `db-cost-check` skill to estimate how much data this query will scan\n"
        f"2. Show me the estimate\n"
        f"3. Ask me to confirm with **\"cost confirmed\"** before proceeding\n\n"
        f"*This check exists because {db_type.title()} charges based on data scanned. "
        f"Confirming cost estimates before execution protects against accidental large bills.*"
    )

    print(json.dumps({"action": "block", "message": message}))


if __name__ == "__main__":
    main()
