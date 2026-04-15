#!/usr/bin/env python3
"""
Oracle connectivity wrapper for AnalystOS.

Reads the active Oracle connection from .claude/db-connections/active.yaml,
executes a SQL query (or EXPLAIN PLAN), and writes results to stdout.

Usage:
    python scripts/oracle_connect.py --query "SELECT ..." [OPTIONS]

Options:
    --query TEXT       SQL to execute (required)
    --format csv|json  Output format (default: csv)
    --limit N          Max rows to return; 0 = unlimited (default: 1000)
    --explain          Run EXPLAIN PLAN; print estimated_rows + full_table_scans; exit 0
    --config PATH      Path to active.yaml (default: .claude/db-connections/active.yaml)
    --no-header        Suppress CSV header row
    --schema SCHEMA    Set current schema for the session (ALTER SESSION SET CURRENT_SCHEMA)
    --thick            Enable thick mode (requires Oracle Instant Client on PATH)

Exit codes:
    0  Success
    1  Config or auth error
    2  Query execution failed
    3  Timeout (not applicable for Oracle, reserved for parity)
    4  Network or connectivity error
    5  Unexpected internal error

Auth modes (set in active.yaml oracle.auth_mode):
    password  — ORACLE_USER + ORACLE_PASSWORD env vars (or configured names)
    wallet    — mTLS wallet, no username/password (Oracle Cloud Autonomous DB)
    tns       — TNS alias from tnsnames.ora

Manual wallet verification steps:
    1. Set active.yaml to wallet auth with wallet_location pointing to an expired wallet
    2. Run: python scripts/oracle_connect.py --query "SELECT 1 FROM DUAL"
    3. Verify error says: "wallet certificate has expired"
    4. Renew wallet, retry — verify CSV output: 1
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.connectors.common import (
    AuthError,
    ConfigError,
    NetworkError,
    QueryError,
    format_results,
)
from scripts.connectors.oracle import (
    DEFAULT_FETCH_SIZE,
    OracleRunner,
    build_connection,
    load_config,
)

_DEFAULT_CONFIG = Path(".claude/db-connections/active.yaml")

EXIT_SUCCESS = 0
EXIT_CONFIG_AUTH = 1
EXIT_QUERY_FAILED = 2
EXIT_TIMEOUT = 3       # reserved for API parity with athena_connect
EXIT_NETWORK = 4
EXIT_UNEXPECTED = 5


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oracle_connect",
        description="Execute SQL against Oracle and write results to stdout.",
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
        default=DEFAULT_FETCH_SIZE,
        metavar="N",
        help=f"Max rows to return; 0 = unlimited (default: {DEFAULT_FETCH_SIZE})",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Run EXPLAIN PLAN and print estimated_rows + full_table_scans",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        metavar="PATH",
        help=f"Path to active.yaml (default: {_DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="Suppress CSV header row",
    )
    parser.add_argument(
        "--schema",
        default=None,
        metavar="SCHEMA",
        help="Set current schema for the session (ALTER SESSION SET CURRENT_SCHEMA)",
    )
    parser.add_argument(
        "--thick",
        action="store_true",
        help="Enable thick mode (requires Oracle Instant Client)",
    )
    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Load config
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"[config error] {exc}", file=sys.stderr)
        return EXIT_CONFIG_AUTH

    if args.thick:
        cfg.thick_mode = True

    # Build connection (surfaces auth errors early)
    try:
        conn = build_connection(cfg)
    except (ConfigError, AuthError) as exc:
        print(f"[auth error] {exc}", file=sys.stderr)
        return EXIT_CONFIG_AUTH
    except NetworkError as exc:
        print(f"[network error] {exc}", file=sys.stderr)
        return EXIT_NETWORK

    runner = OracleRunner(cfg, conn)

    # Set session schema if requested
    if args.schema:
        try:
            runner.execute_query(f"ALTER SESSION SET CURRENT_SCHEMA = {args.schema}", row_limit=0)
        except QueryError as exc:
            print(f"[query error] Failed to set schema '{args.schema}': {exc}", file=sys.stderr)
            return EXIT_QUERY_FAILED

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
    except NetworkError as exc:
        print(f"[network error] {exc}", file=sys.stderr)
        return EXIT_NETWORK
    except Exception as exc:
        print(f"[unexpected error] {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_UNEXPECTED
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _run_query(runner: OracleRunner, args: argparse.Namespace) -> int:
    columns, rows = runner.execute_query(args.query, row_limit=args.limit)
    output = format_results(
        columns,
        rows,
        fmt=args.fmt,
        row_limit=args.limit,
        include_header=not args.no_header,
    )
    print(output, end="")
    return EXIT_SUCCESS


def _run_explain(runner: OracleRunner, sql: str) -> int:
    plan_text, estimated_rows, full_table_scans = runner.run_explain(sql)

    print("EXPLAIN PLAN output:")
    print(plan_text)
    print("---")
    if estimated_rows is not None:
        print(f"estimated_rows: {estimated_rows:,}")
    else:
        print("estimated_rows: unknown")

    if full_table_scans:
        print(f"full_table_scans: {', '.join(full_table_scans)}")
    else:
        print("full_table_scans: none")

    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
