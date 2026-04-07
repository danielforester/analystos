#!/usr/bin/env python3
"""
db-capture-prompt — Post-tool-use hook for the DB Analyst Framework.

After a tool call that returned a significant query result (non-trivial row count,
large result set, or analyst-added context), offers to save the query to the
knowledge base via /db-capture.

"Significant" is defined as: result contains more rows than the configurable
`capture_row_floor` threshold (default: 10 rows). The heuristic is intentionally
simple — better to prompt and be dismissed than to silently let valuable queries
disappear.

Only prompts once per session per query signature to avoid repetition.
Respects the `suppress_capture_prompt` flag in active.yaml.

Input  (stdin): JSON matching the Claude Code post-tool-use hook schema
Output (stdout): JSON with {"action": "allow"} or {"action": "block", "message": "..."}

Claude Code hook schema reference:
  https://docs.anthropic.com/en/docs/claude-code/hooks
"""

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path


# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_CAPTURE_ROW_FLOOR = 10  # Prompt if result has more rows than this

# ── Session deduplication ─────────────────────────────────────────────────────
SESSION_FLAG_PREFIX = "db-capture-prompt-offered-"

# ── Patterns that suggest a meaningful query result is present ────────────────
# These detect typical tabular output formats that Claude produces.
RESULT_SIGNALS = [
    # Markdown table with header separator row: | --- | --- |
    re.compile(r"\|\s*[-:]+\s*\|", re.MULTILINE),
    # "N rows" pattern (e.g., "Returned 47 rows", "showing 25 rows")
    re.compile(r"\b(\d+)\s+rows?\b", re.IGNORECASE),
    # Code block with comma-separated values (CSV-style output)
    re.compile(r"```\n\w+,\w+", re.MULTILINE),
]

# Pattern to extract row count from result signals
ROW_COUNT_PATTERN = re.compile(r"\b(\d+)\s+rows?\b", re.IGNORECASE)

# Pattern to extract SQL from assistant message or tool input
SQL_PATTERNS = re.compile(
    r"```sql\s*(.*?)```",
    re.IGNORECASE | re.DOTALL,
)

# ── Config path ───────────────────────────────────────────────────────────────
CONNECTIONS_PATH = Path(".claude/db-connections/active.yaml")


def load_connection_config() -> dict:
    """Load relevant fields from active.yaml. Returns empty dict on any failure."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}

    if not CONNECTIONS_PATH.exists():
        return {}

    try:
        with open(CONNECTIONS_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        active_name = config.get("active")
        if not active_name:
            return {}
        for conn in config.get("connections", []):
            if conn.get("name") == active_name:
                return conn
    except Exception:
        pass

    return {}


def get_row_floor(conn: dict) -> int:
    """Return the configured row floor for capture prompts."""
    floor = conn.get("capture_row_floor", DEFAULT_CAPTURE_ROW_FLOOR)
    try:
        return int(floor)
    except (TypeError, ValueError):
        return DEFAULT_CAPTURE_ROW_FLOOR


def query_signature(sql: str) -> str:
    """Return a short hash of the SQL for dedup purposes."""
    normalized = re.sub(r"\s+", " ", sql.strip().lower())
    return hashlib.sha1(normalized.encode()).hexdigest()[:12]


def get_session_flag_path(sig: str) -> Path:
    """Return the path of the per-session dedup flag for this query signature."""
    ppid = os.getppid()
    return Path(tempfile.gettempdir()) / f"{SESSION_FLAG_PREFIX}{ppid}_{sig}"


def already_offered(sig: str) -> bool:
    """Return True if we have already offered to capture this query this session."""
    return get_session_flag_path(sig).exists()


def mark_offered(sig: str) -> None:
    """Record that we have offered to capture this query this session."""
    try:
        get_session_flag_path(sig).touch()
    except OSError:
        pass


def extract_row_count(text: str) -> int | None:
    """Try to parse a row count from the assistant message or tool result."""
    # Count rows in a markdown table (subtract header and separator rows)
    table_rows = re.findall(r"^\|[^|]+\|", text, re.MULTILINE)
    if len(table_rows) > 2:
        # Subtract header row + separator row
        return len(table_rows) - 2

    # Look for an explicit row count statement
    match = ROW_COUNT_PATTERN.search(text)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass

    return None


def has_result_signal(text: str) -> bool:
    """Return True if the text appears to contain a meaningful query result."""
    return any(pattern.search(text) for pattern in RESULT_SIGNALS)


def extract_sql(hook_data: dict) -> str | None:
    """Try to find SQL in the assistant message or tool input."""
    # Check assistant message for a SQL code block
    assistant_message = hook_data.get("assistant_message", "") or ""
    match = SQL_PATTERNS.search(assistant_message)
    if match:
        return match.group(1).strip()

    # Check tool input fields
    tool_input = hook_data.get("input", {}) or hook_data.get("tool_input", {}) or {}
    for field in ("query", "sql", "statement"):
        if field in tool_input and isinstance(tool_input[field], str):
            return tool_input[field].strip()

    return None


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            print(json.dumps({"action": "allow"}))
            return

        hook_data = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"db-capture-prompt: Could not parse hook input: {e}\n")
        print(json.dumps({"action": "allow"}))
        return

    # Load config — needed for suppress flag and row floor
    conn = load_connection_config()

    # Check suppression flag
    if conn.get("suppress_capture_prompt", False):
        sys.stderr.write(
            "db-capture-prompt: Suppressed via active.yaml. Allowing.\n"
        )
        print(json.dumps({"action": "allow"}))
        return

    row_floor = get_row_floor(conn)

    # Inspect the assistant message for a significant result
    assistant_message = hook_data.get("assistant_message", "") or ""
    tool_result = hook_data.get("tool_result", "") or ""

    # Check the tool result first (raw output), then the assistant message
    check_text = tool_result if tool_result else assistant_message

    if not has_result_signal(check_text):
        print(json.dumps({"action": "allow"}))
        return

    # Check row count against floor
    row_count = extract_row_count(check_text)
    if row_count is not None and row_count <= row_floor:
        sys.stderr.write(
            f"db-capture-prompt: Result has {row_count} rows (<= floor of {row_floor}). Skipping.\n"
        )
        print(json.dumps({"action": "allow"}))
        return

    # Try to find the SQL that produced this result
    sql = extract_sql(hook_data)
    if not sql:
        # No SQL found — can't make a useful capture prompt
        print(json.dumps({"action": "allow"}))
        return

    # Dedup: don't prompt for the same query twice this session
    sig = query_signature(sql)
    if already_offered(sig):
        sys.stderr.write(
            "db-capture-prompt: Already offered to capture this query this session. Skipping.\n"
        )
        print(json.dumps({"action": "allow"}))
        return

    mark_offered(sig)

    # Build the prompt
    row_desc = f"{row_count} rows" if row_count is not None else "a non-trivial result set"
    sql_preview = sql[:200] + ("..." if len(sql) > 200 else "")

    message = (
        f"💾 **Save this query to the knowledge base?**\n\n"
        f"This query returned {row_desc}. If it was useful, saving it now means "
        f"future analysts can find it without starting from scratch.\n\n"
        f"**Query:**\n```sql\n{sql_preview}\n```\n\n"
        f"**Run `/db-capture`** to save it with a name, purpose, and notes.\n\n"
        f"*(To suppress this prompt, add `suppress_capture_prompt: true` under your "
        f"active connection in `.claude/db-connections/active.yaml`.)*"
    )

    print(json.dumps({"action": "block", "message": message}))


if __name__ == "__main__":
    main()
