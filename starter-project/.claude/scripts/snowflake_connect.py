#!/usr/bin/env python3
"""
Snowflake connectivity wrapper for AnalystOS.

Reads the active Snowflake connection from .claude/db-connections/active.yaml,
executes a SQL query (or EXPLAIN USING TABULAR), and writes results to stdout.

Usage:
    python scripts/snowflake_connect.py --query "SELECT ..." [OPTIONS]

Options:
    --query TEXT          SQL to execute (required)
    --format csv|json     Output format (default: csv)
    --limit N             Max rows to return; 0 = unlimited (default: 1000)
    --explain             Run EXPLAIN USING TABULAR; print estimated_bytes + partitions_total; exit 0
    --config PATH         Path to active.yaml (default: .claude/db-connections/active.yaml)
    --no-header           Suppress CSV header row
    --warehouse NAME      Override the warehouse from the config for this session
    --schema SCHEMA       Override the default schema for this session
    --role ROLE           Override the role from the config for this session

Exit codes:
    0  Success
    1  Config or auth error
    2  Query execution failed
    3  (Reserved for API parity — Snowflake queries are synchronous)
    4  Network or connectivity error
    5  Unexpected internal error

Auth modes (set in active.yaml snowflake.auth_mode):
    password  — SNOWFLAKE_USER + SNOWFLAKE_PASSWORD env vars (or configured names)
    keypair   — JWT via PEM private key file; no browser, best for CI/CD / service accounts
    sso       — externalbrowser; opens a browser tab for interactive SSO login
    oauth     — pre-obtained OAuth token from env var (external IdP / service accounts)
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
from scripts.connectors.snowflake import (
    DEFAULT_FETCH_SIZE,
    SnowflakeRunner,
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
        prog="snowflake_connect",
        description="Execute SQL against Snowflake and write results to stdout.",
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
        help="Run EXPLAIN USING TABULAR and print estimated_bytes + partitions_total",
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
        "--warehouse",
        default=None,
        metavar="NAME",
        help="Override the warehouse from the config for this session",
    )
    parser.add_argument(
        "--schema",
        default=None,
        metavar="SCHEMA",
        help="Override the default schema for this session",
    )
    parser.add_argument(
        "--role",
        default=None,
        metavar="ROLE",
        help="Override the role from the config for this session",
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

    # Apply CLI overrides
    if args.warehouse:
        cfg.warehouse = args.warehouse
    if args.schema:
        cfg.schema = args.schema
    if args.role:
        cfg.role = args.role

    # Build connection (surfaces auth errors early)
    try:
        conn = build_connection(cfg)
    except (ConfigError, AuthError) as exc:
        print(f"[auth error] {exc}", file=sys.stderr)
        return EXIT_CONFIG_AUTH
    except NetworkError as exc:
        print(f"[network error] {exc}", file=sys.stderr)
        return EXIT_NETWORK

    runner = SnowflakeRunner(cfg, conn)

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


def _run_query(runner: SnowflakeRunner, args: argparse.Namespace) -> int:
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


def _run_explain(runner: SnowflakeRunner, sql: str) -> int:
    plan_text, estimated_bytes, partitions_total = runner.run_explain(sql)

    print("EXPLAIN output:")
    print(plan_text)
    print("---")
    if estimated_bytes is not None:
        estimated_gb = estimated_bytes / (1024 ** 3)
        print(f"estimated_bytes: {estimated_bytes}")
        print(f"estimated_gb: {estimated_gb:.2f}")
    else:
        print("estimated_bytes: unknown")
        print("estimated_gb: unknown")

    if partitions_total is not None:
        print(f"partitions_total: {partitions_total}")
    else:
        print("partitions_total: unknown")

    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
