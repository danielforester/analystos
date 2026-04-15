#!/usr/bin/env python3
"""
Athena connectivity wrapper for AnalystOS.

Reads the active Athena connection from .claude/db-connections/active.yaml,
executes a SQL query (or EXPLAIN), and writes results to stdout.

Usage:
    python scripts/athena_connect.py --query "SELECT ..." [OPTIONS]

Options:
    --query TEXT       SQL to execute (required unless --explain-only)
    --format csv|json  Output format (default: csv)
    --limit N          Max rows to return; 0 = unlimited (default: 1000)
    --explain          Run EXPLAIN on the query; print byte estimate; exit 0
    --config PATH      Path to active.yaml (default: .claude/db-connections/active.yaml)
    --timeout N        Override timeout_seconds from config
    --no-header        Suppress CSV header row
    --workgroup NAME   Override workgroup from config

Exit codes:
    0  Success
    1  Config or auth error
    2  Query execution failed (Athena FAILED or CANCELLED)
    3  Timeout waiting for query completion
    4  Network or connectivity error
    5  Unexpected internal error

Manual SSO verification steps:
    1. Set active.yaml to profile auth with a known SSO profile
    2. Run: aws sso logout --profile <profile>   (force token expiry)
    3. Run: python scripts/athena_connect.py --query "SELECT 1"
    4. Verify the error says: aws sso login --profile <profile>
    5. Run: aws sso login --profile <profile>
    6. Run: python scripts/athena_connect.py --query "SELECT 1"
    7. Verify: CSV output with one row containing "1"
"""

import argparse
import sys
from pathlib import Path

# Allow running as a script from the project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.connectors.athena import (
    DEFAULT_ROW_LIMIT,
    AthenaRunner,
    AuthError,
    ConfigError,
    NetworkError,
    QueryError,
    TimeoutError,
    build_boto3_session,
    extract_bytes_from_explain,
    format_results,
    load_config,
)

_DEFAULT_CONFIG = Path(".claude/db-connections/active.yaml")

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_SUCCESS = 0
EXIT_CONFIG_AUTH = 1
EXIT_QUERY_FAILED = 2
EXIT_TIMEOUT = 3
EXIT_NETWORK = 4
EXIT_UNEXPECTED = 5


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="athena_connect",
        description="Execute SQL against AWS Athena and write results to stdout.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--query", required=True, help="SQL query to execute")
    parser.add_argument(
        "--format",
        dest="fmt",
        choices=["csv", "json"],
        default="csv",
        help="Output format (default: csv)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_ROW_LIMIT,
        metavar="N",
        help=f"Max rows to return; 0 = unlimited (default: {DEFAULT_ROW_LIMIT})",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Run EXPLAIN on the query and print the estimated bytes scanned",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        metavar="PATH",
        help=f"Path to active.yaml (default: {_DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        metavar="N",
        help="Timeout in seconds (overrides timeout_seconds from config)",
    )
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="Suppress CSV header row",
    )
    parser.add_argument(
        "--workgroup",
        default=None,
        metavar="NAME",
        help="Override Athena workgroup from config",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Load config
    # ------------------------------------------------------------------
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"[config error] {exc}", file=sys.stderr)
        return EXIT_CONFIG_AUTH

    # Apply CLI overrides
    if args.timeout is not None:
        cfg.timeout_seconds = args.timeout
    if args.workgroup is not None:
        cfg.workgroup = args.workgroup

    # ------------------------------------------------------------------
    # Build session (surfaces auth errors early)
    # ------------------------------------------------------------------
    try:
        session = build_boto3_session(cfg)
    except (ConfigError, AuthError) as exc:
        print(f"[auth error] {exc}", file=sys.stderr)
        return EXIT_CONFIG_AUTH

    # ------------------------------------------------------------------
    # Run query
    # ------------------------------------------------------------------
    runner = AthenaRunner(cfg, session)

    try:
        if args.explain:
            return _run_explain(runner, args.query)
        else:
            return _run_query(runner, args)
    except AuthError as exc:
        print(f"[auth error] {exc}", file=sys.stderr)
        return EXIT_CONFIG_AUTH
    except QueryError as exc:
        print(f"[query error] {exc}", file=sys.stderr)
        return EXIT_QUERY_FAILED
    except TimeoutError as exc:
        print(f"[timeout] {exc}", file=sys.stderr)
        return EXIT_TIMEOUT
    except NetworkError as exc:
        print(f"[network error] {exc}", file=sys.stderr)
        return EXIT_NETWORK
    except Exception as exc:
        print(f"[unexpected error] {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_UNEXPECTED


def _run_query(runner: AthenaRunner, args: argparse.Namespace) -> int:
    execution_id = runner.start_query(args.query)
    runner.poll_until_terminal(execution_id)
    columns, rows = runner.fetch_results_paginated(execution_id, row_limit=args.limit)
    output = format_results(
        columns,
        rows,
        fmt=args.fmt,
        row_limit=args.limit,
        include_header=not args.no_header,
    )
    print(output, end="")
    return EXIT_SUCCESS


def _run_explain(runner: AthenaRunner, sql: str) -> int:
    explain_text = runner.run_explain(sql)
    estimated_bytes = extract_bytes_from_explain(explain_text)

    print("EXPLAIN output:")
    print(explain_text)
    print("---")
    if estimated_bytes is not None:
        estimated_gb = estimated_bytes / (1024**3)
        print(f"estimated_bytes: {estimated_bytes}")
        print(f"estimated_gb: {estimated_gb:.2f}")
    else:
        print("estimated_bytes: unknown")
        print("estimated_gb: unknown")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
