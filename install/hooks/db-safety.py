#!/usr/bin/env python3
"""
db-safety — Pre-tool-use hook for AnalystOS.

Scans tool inputs for DDL/DML SQL keywords and blocks execution
unless the user has explicitly typed an override phrase.

Input  (stdin): JSON matching the Claude Code pre-tool-use hook schema
Output (stdout): JSON with {"action": "allow"} or {"action": "block", "message": "..."}

Claude Code hook schema reference:
  https://docs.anthropic.com/en/docs/claude-code/hooks
"""

import json
import re
import sys


# ── Blocked keyword patterns ─────────────────────────────────────────────────
# These match whole SQL keywords, case-insensitive, with word boundaries.
# Ordered from most destructive to least, for clearer error messages.

DDL_DML_PATTERNS = [
    # Data destruction
    r"\bDROP\s+(TABLE|VIEW|DATABASE|SCHEMA|INDEX|SEQUENCE|PROCEDURE|FUNCTION|TRIGGER)\b",
    r"\bTRUNCATE\b",
    r"\bDELETE\s+FROM\b",
    r"\bDELETE\b",
    # Data modification
    r"\bINSERT\s+INTO\b",
    r"\bINSERT\b",
    r"\bUPDATE\s+\w",
    r"\bMERGE\s+INTO\b",
    r"\bMERGE\b",
    # Schema modification
    r"\bCREATE\s+(TABLE|VIEW|DATABASE|SCHEMA|INDEX|SEQUENCE|PROCEDURE|FUNCTION|TRIGGER)\b",
    r"\bALTER\s+(TABLE|VIEW|DATABASE|SCHEMA|INDEX|SEQUENCE)\b",
    # SQLite-specific state changes
    r"\bATTACH\s+DATABASE\b",
    r"\bDETACH\s+DATABASE\b",
    r"PRAGMA\s+\w+\s*=",  # Any PRAGMA assignment (state change)
    # Oracle DDL
    r"\bRENAME\s+\w+\s+TO\b",
    r"\bCOMMENT\s+ON\b",
    # Transaction control that implies writes
    r"\bROLLBACK\b",
    r"\bCOMMIT\b",
    r"\bSAVEPOINT\b",
    # Privilege changes
    r"\bGRANT\b",
    r"\bREVOKE\b",
]

# Compile all patterns once
BLOCKED_REGEX = re.compile(
    "|".join(DDL_DML_PATTERNS),
    flags=re.IGNORECASE | re.DOTALL,
)

# ── Override phrase ───────────────────────────────────────────────────────────
# If the user's message contains this exact phrase, allow the blocked operation.
OVERRIDE_PHRASE = "override read-only"


def extract_sql_candidates(tool_input: dict) -> list[str]:
    """
    Pull strings from the tool input that might contain SQL.
    Handles common patterns: bash commands, MCP SQL tool inputs, raw query fields.
    """
    candidates = []

    # Bash tool: {"command": "..."}
    if "command" in tool_input:
        candidates.append(str(tool_input["command"]))

    # Generic SQL fields
    for field in ("query", "sql", "statement", "code", "text", "input"):
        if field in tool_input:
            candidates.append(str(tool_input[field]))

    # Nested structures (e.g., MCP tool args)
    for value in tool_input.values():
        if isinstance(value, str) and len(value) > 10:
            candidates.append(value)
        elif isinstance(value, dict):
            candidates.extend(extract_sql_candidates(value))

    return candidates


def find_blocked_keyword(sql_text: str) -> str | None:
    """Return the first blocked keyword/pattern found, or None if clean."""
    match = BLOCKED_REGEX.search(sql_text)
    if match:
        return match.group(0).strip()
    return None


def check_override_in_context(hook_data: dict) -> bool:
    """
    Check if the user's message contains the override phrase.
    The hook receives the full conversation context in some schemas.
    """
    # Check top-level user message if present
    user_message = hook_data.get("user_message", "") or ""
    if OVERRIDE_PHRASE in user_message.lower():
        return True

    # Check assistant message / reasoning
    assistant_message = hook_data.get("assistant_message", "") or ""
    if OVERRIDE_PHRASE in assistant_message.lower():
        return True

    return False


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            # No input — allow (not a DB tool call)
            print(json.dumps({"action": "allow"}))
            return

        hook_data = json.loads(raw)
    except json.JSONDecodeError as e:
        # Malformed input — fail open with a warning (don't block all tools)
        sys.stderr.write(f"db-safety: Could not parse hook input: {e}\n")
        print(json.dumps({"action": "allow"}))
        return

    tool_name = hook_data.get("tool", "") or hook_data.get("tool_name", "")
    tool_input = hook_data.get("input", {}) or hook_data.get("tool_input", {})

    if not isinstance(tool_input, dict):
        tool_input = {}

    # Extract SQL candidates from tool input
    candidates = extract_sql_candidates(tool_input)

    blocked_keyword = None
    for text in candidates:
        kw = find_blocked_keyword(text)
        if kw:
            blocked_keyword = kw
            break

    if blocked_keyword is None:
        # No blocked keywords found — allow
        print(json.dumps({"action": "allow"}))
        return

    # Blocked keyword found — check for override
    if check_override_in_context(hook_data):
        sys.stderr.write(
            f"db-safety: Read-only override accepted. Allowing '{blocked_keyword}'.\n"
        )
        print(json.dumps({"action": "allow"}))
        return

    # Block the operation
    message = (
        f"🛑 **Read-only protection triggered**\n\n"
        f"Detected a write/destructive SQL keyword: `{blocked_keyword}`\n\n"
        f"This framework operates in **read-only mode** by default. "
        f"Write operations are blocked to protect your data.\n\n"
        f"**To override:** Include the phrase `override read-only` in your message "
        f"and I will allow this operation.\n\n"
        f"Example: *\"override read-only — drop the temp table test_staging\""
    )

    print(json.dumps({"action": "block", "message": message}))


if __name__ == "__main__":
    main()
