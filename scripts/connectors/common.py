"""
Shared utilities for AnalystOS database connectors.

Provides the exception hierarchy, output formatting, and YAML config loading
used by all connector modules (athena.py, oracle.py, etc.).
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class ConnectorError(Exception):
    """Base exception for all AnalystOS connector errors."""


class ConfigError(ConnectorError):
    """Bad or missing configuration (active.yaml, missing fields, wrong type)."""


class AuthError(ConnectorError):
    """Credential or authentication failure."""


class QueryError(ConnectorError):
    """Query execution failed at the database level."""

    def __init__(self, message: str, execution_id: str = "") -> None:
        super().__init__(message)
        self.execution_id = execution_id


class TimeoutError(ConnectorError):
    """Query did not complete within the configured timeout."""


class NetworkError(ConnectorError):
    """Network or endpoint connectivity failure."""


# ---------------------------------------------------------------------------
# YAML config loading
# ---------------------------------------------------------------------------


def load_yaml_config(config_path: Path) -> dict:
    """
    Load and parse a YAML config file.

    Raises ConfigError on missing file or parse failure.
    """
    if not config_path.exists():
        raise ConfigError(
            f"Connection config not found: {config_path}\n"
            "Copy connections.example.yaml to active.yaml and configure it."
        )
    try:
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse {config_path}: {exc}") from exc


def find_active_connection(raw: dict, config_path: Path) -> dict:
    """
    Given a parsed active.yaml dict, return the active connection profile dict.

    Raises ConfigError if active name is missing or not found in connections list.
    """
    active_name = raw.get("active")
    if not active_name:
        raise ConfigError(f"'active:' field is missing or empty in {config_path}")

    connections = raw.get("connections", [])
    conn = next((c for c in connections if c.get("name") == active_name), None)
    if conn is None:
        raise ConfigError(
            f"Active connection '{active_name}' not found in connections list."
        )
    return conn


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

DEFAULT_ROW_LIMIT = 1000


def format_results(
    columns: list[str],
    rows: list[list[str]],
    fmt: str = "csv",
    row_limit: int = DEFAULT_ROW_LIMIT,
    include_header: bool = True,
) -> str:
    """
    Format query results as CSV or JSON.

    Truncates to row_limit (if > 0) and appends a trailing notice when cut.
    All values are kept as strings for maximum fidelity.
    """
    truncated = False
    if row_limit > 0 and len(rows) > row_limit:
        rows = rows[:row_limit]
        truncated = True

    if fmt == "json":
        data = [dict(zip(columns, row)) for row in rows]
        result = json.dumps(data, indent=2, default=str)
        if truncated:
            result += f"\n// [output truncated at {row_limit} rows]"
        return result

    # Default: CSV
    buf = io.StringIO()
    writer = csv.writer(buf)
    if include_header:
        writer.writerow(columns)
    writer.writerows(rows)
    result = buf.getvalue()
    if truncated:
        result += f"# [output truncated at {row_limit} rows]\n"
    return result
