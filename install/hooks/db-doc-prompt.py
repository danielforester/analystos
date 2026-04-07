#!/usr/bin/env python3
"""
db-doc-prompt — Post-tool-use hook for the DB Analyst Framework.

After a tool call that produced documentation-worthy output (a /db-explain or
/db-orient result), offers to persist the explanation as a KB markdown entry.

Only prompts once per session per table to avoid repetitive interruptions.
Respects the `suppress_doc_prompt` flag in active.yaml.

Input  (stdin): JSON matching the Claude Code post-tool-use hook schema
Output (stdout): JSON with {"action": "allow"} or {"action": "block", "message": "..."}

Claude Code hook schema reference:
  https://docs.anthropic.com/en/docs/claude-code/hooks
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path


# ── Session deduplication ─────────────────────────────────────────────────────
# One flag file per session in the system temp dir. Tracks which tables we have
# already offered to document so we don't prompt on every subsequent tool call.
SESSION_FLAG_PREFIX = "db-doc-prompt-offered-"


# ── Signals in assistant output that indicate doc-worthy content ─────────────
# These patterns detect /db-explain and /db-orient output formats.
DOC_SIGNALS = [
    # /db-explain table output: "## {TABLE} — What It Is"
    re.compile(r"##\s+\S+\s+—\s+What It Is", re.IGNORECASE),
    # /db-orient output: "# {SCHEMA} — Schema Overview"
    re.compile(r"#\s+\S+\s+—\s+Schema Overview", re.IGNORECASE),
    # /db-explain KB entry format header
    re.compile(r"\*\*Schema:\*\*.*\n.*\*\*Last (Updated|Documented):\*\*", re.DOTALL),
    # Table explanation section markers
    re.compile(r"###\s+(Business Purpose|Key Columns|Grain)\b", re.IGNORECASE),
]

# Pattern to extract the table name from doc-worthy assistant output.
TABLE_NAME_PATTERNS = [
    # "## ORDERS — What It Is"
    re.compile(r"##\s+(\w+)\s+—\s+What It Is", re.IGNORECASE),
    # "# SCHEMA_NAME — Schema Overview" — extract the schema name
    re.compile(r"#\s+(\w+)\s+—\s+Schema Overview", re.IGNORECASE),
    # "**Schema:** `sales`\n# TABLE_NAME" → fallback: first H1 after metadata
    re.compile(r"^#\s+([A-Z_][A-Z0-9_]*)\s*$", re.MULTILINE),
]

# ── Config path ───────────────────────────────────────────────────────────────
CONNECTIONS_PATH = Path(".claude/db-connections/active.yaml")


def load_suppress_flag() -> bool:
    """Return True if suppress_doc_prompt is set in active.yaml."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return False

    if not CONNECTIONS_PATH.exists():
        return False

    try:
        with open(CONNECTIONS_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        active_name = config.get("active")
        if not active_name:
            return False
        for conn in config.get("connections", []):
            if conn.get("name") == active_name:
                return bool(conn.get("suppress_doc_prompt", False))
    except Exception:
        pass

    return False


def get_session_flag_path(table_name: str) -> Path:
    """Return the path of the per-session dedup flag for this table name."""
    # Use PID-based session identification so flags clear when Claude exits.
    # In practice we use parent PID so child processes share the session.
    ppid = os.getppid()
    safe_name = re.sub(r"[^\w]", "_", table_name.lower())
    return Path(tempfile.gettempdir()) / f"{SESSION_FLAG_PREFIX}{ppid}_{safe_name}"


def already_offered(table_name: str) -> bool:
    """Return True if we have already offered to document this table this session."""
    return get_session_flag_path(table_name).exists()


def mark_offered(table_name: str) -> None:
    """Record that we have offered to document this table this session."""
    try:
        get_session_flag_path(table_name).touch()
    except OSError:
        pass  # Non-fatal; worst case is a repeat prompt


def is_doc_worthy(assistant_message: str) -> bool:
    """Return True if the assistant message contains doc-worthy content."""
    if not assistant_message:
        return False
    return any(pattern.search(assistant_message) for pattern in DOC_SIGNALS)


def extract_table_name(assistant_message: str) -> str | None:
    """Attempt to extract the table or schema name from the assistant message."""
    for pattern in TABLE_NAME_PATTERNS:
        match = pattern.search(assistant_message)
        if match:
            name = match.group(1).strip()
            # Skip generic section headers that aren't table names
            if name.upper() not in ("QUERY", "SQL", "NOTE", "SUMMARY", "OVERVIEW"):
                return name
    return None


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            print(json.dumps({"action": "allow"}))
            return

        hook_data = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"db-doc-prompt: Could not parse hook input: {e}\n")
        print(json.dumps({"action": "allow"}))
        return

    # Check suppression flag first — cheapest check
    if load_suppress_flag():
        sys.stderr.write("db-doc-prompt: Suppressed via active.yaml. Allowing.\n")
        print(json.dumps({"action": "allow"}))
        return

    # Inspect the assistant message for doc-worthy signals
    assistant_message = hook_data.get("assistant_message", "") or ""

    if not is_doc_worthy(assistant_message):
        print(json.dumps({"action": "allow"}))
        return

    # Extract what was documented
    table_name = extract_table_name(assistant_message)
    if not table_name:
        # Can't identify a specific table — skip the prompt
        print(json.dumps({"action": "allow"}))
        return

    # Check session dedup — don't prompt for the same table twice
    if already_offered(table_name):
        sys.stderr.write(
            f"db-doc-prompt: Already offered to document `{table_name}` this session. Skipping.\n"
        )
        print(json.dumps({"action": "allow"}))
        return

    mark_offered(table_name)

    # Produce the prompt
    message = (
        f"📝 **Save explanation to knowledge base?**\n\n"
        f"I just explained `{table_name}`. Would you like to persist this as a KB entry "
        f"so future analysts don't have to rediscover it?\n\n"
        f"**Run `/db-document {table_name}`** to create a structured KB entry — I'll use "
        f"what I just learned to pre-fill the draft.\n\n"
        f"*(To suppress this prompt in future sessions, add `suppress_doc_prompt: true` "
        f"under your active connection in `.claude/db-connections/active.yaml`.)*"
    )

    # Use "block" to surface the message before the next tool executes.
    # This is advisory — the analyst can proceed without documenting.
    print(json.dumps({"action": "block", "message": message}))


if __name__ == "__main__":
    main()
