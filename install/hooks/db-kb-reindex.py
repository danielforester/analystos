#!/usr/bin/env python3
"""
db-kb-reindex — Post-tool-use hook for AnalystOS.

Fires after every Write tool call. If the written file is inside db-knowledge/,
runs kb_search.py --build --incremental as a non-blocking background subprocess
to keep the semantic index up to date after documentation writes.

Never blocks: always returns {"action": "allow"} immediately.
Respects `rag.suppress_reindex_prompt: true` in active.yaml.

Input  (stdin): JSON matching the Claude Code post-tool-use hook schema
Output (stdout): JSON with {"action": "allow"}
"""

import json
import subprocess
import sys
from pathlib import Path


CONNECTIONS_PATH = Path(".claude/db-connections/active.yaml")
KB_PATH = Path("db-knowledge/")
KB_SEARCH_SCRIPT = Path(".claude/scripts/kb_search.py")


def _allow():
    print(json.dumps({"action": "allow"}))


def load_yaml_flag(key_path: list, default):
    """
    Read a nested key from active.yaml. key_path is a list of keys to traverse.
    Returns default on any failure (missing file, no yaml, parse error).
    """
    if not CONNECTIONS_PATH.exists():
        return default
    try:
        import yaml  # type: ignore
        with open(CONNECTIONS_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        node = config
        for key in key_path:
            if not isinstance(node, dict):
                return default
            node = node.get(key, default)
        return node
    except ImportError:
        pass
    except Exception:
        pass
    return default


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            _allow()
            return
        hook_data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        _allow()
        return

    # Only act on Write tool calls
    tool_name = (
        hook_data.get("tool_name")
        or hook_data.get("tool")
        or ""
    )
    if tool_name != "Write":
        _allow()
        return

    # Get the file path that was written
    tool_input = hook_data.get("tool_input") or hook_data.get("input") or {}
    file_path_str = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path_str:
        _allow()
        return

    # Check if the written file is inside db-knowledge/
    try:
        written = Path(file_path_str)
        kb_abs = KB_PATH.resolve()
        written_abs = (written if written.is_absolute() else Path.cwd() / written).resolve()
        if not str(written_abs).startswith(str(kb_abs)):
            _allow()
            return
    except Exception:
        _allow()
        return

    # Check if RAG is enabled (default: True if no rag block)
    rag_enabled = load_yaml_flag(["rag", "enabled"], True)
    if not rag_enabled:
        _allow()
        return

    # Check suppress flag
    suppress = load_yaml_flag(["rag", "suppress_reindex_prompt"], False)
    if suppress:
        sys.stderr.write("db-kb-reindex: suppressed via active.yaml\n")
        _allow()
        return

    # Check script exists
    if not KB_SEARCH_SCRIPT.exists():
        sys.stderr.write(f"db-kb-reindex: script not found: {KB_SEARCH_SCRIPT}\n")
        _allow()
        return

    # Launch incremental reindex as a detached background subprocess (non-blocking)
    try:
        subprocess.Popen(
            [sys.executable, str(KB_SEARCH_SCRIPT), "--build", "--incremental", "--kb", str(KB_PATH)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        sys.stderr.write("db-kb-reindex: incremental reindex started in background\n")
    except Exception as e:
        sys.stderr.write(f"db-kb-reindex: could not launch reindex: {e}\n")

    _allow()


if __name__ == "__main__":
    main()
