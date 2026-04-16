#!/usr/bin/env python3
"""
Salesforce connectivity wrapper for AnalystOS.

Reads the active Salesforce connection from .claude/db-connections/active.yaml,
authenticates, and executes a SOQL query (or describe/count/auth-only operation),
writing results to stdout.

Usage:
    python scripts/salesforce_connect.py --query "SELECT Id, Name FROM Account LIMIT 10"
    python scripts/salesforce_connect.py --auth-only
    python scripts/salesforce_connect.py --describe Account
    python scripts/salesforce_connect.py --count Account [--where "CreatedDate = THIS_YEAR"]

Options:
    --query SOQL          SOQL query to execute (required unless --describe/--count/--auth-only)
    --describe OBJECT     Describe a Salesforce object (outputs JSON); no --query needed
    --count OBJECT        Count records in an object; combine with --where for filtered counts
    --where CLAUSE        WHERE clause for --count (omit the word WHERE)
    --auth-only           Authenticate and cache the token, then exit
    --include-deleted     Use query_all() to include soft-deleted records (IsDeleted = true)
    --format csv|json     Output format for --query (default: csv)
    --limit N             Max rows to return; 0 = unlimited (default: 1000)
    --config PATH         Path to active.yaml (default: .claude/db-connections/active.yaml)
    --no-header           Suppress CSV header row

Exit codes:
    0  Success
    1  Config or auth error
    2  Query / describe / count execution failed
    3  (Reserved)
    4  Network or connectivity error
    5  Unexpected internal error

Auth modes (set in active.yaml salesforce.auth_mode):
    sf_cli       — Reuse existing Salesforce CLI session from ~/.sfdx/{username}.json.
                   Run `sf org login web` once; works with SSO. Zero Connected App setup.
                   Set sf_cli_username in active.yaml if you have multiple authenticated orgs.
    oauth_web    — Browser OAuth 2.0 Authorization Code flow. Requires a Connected App.
                   Token is cached in .claude/db-connections/.sf_token_cache.json.
                   Run --auth-only once to pre-authenticate before using --query.
    password     — Username + password (with security token appended) in active.yaml or env vars.
                   No Connected App required; does not work for SSO-only orgs.
    access_token — Pre-obtained Bearer token + instance URL from env vars.
                   Grab the token from Salesforce Inspector, DevTools (sid cookie),
                   or Workbench → Info → Session Information. Lasts ~2 hours.
"""

import argparse
import json
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
from scripts.connectors.salesforce import (
    SalesforceRunner,
    build_connection,
    load_config,
)

_DEFAULT_CONFIG = Path(".claude/db-connections/active.yaml")

EXIT_SUCCESS = 0
EXIT_CONFIG_AUTH = 1
EXIT_QUERY_FAILED = 2
EXIT_TIMEOUT = 3       # reserved
EXIT_NETWORK = 4
EXIT_UNEXPECTED = 5

DEFAULT_ROW_LIMIT = 1000


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="salesforce_connect",
        description="Execute SOQL queries against Salesforce and write results to stdout.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Primary operation (mutually exclusive)
    op_group = parser.add_mutually_exclusive_group()
    op_group.add_argument(
        "--query",
        metavar="SOQL",
        help="SOQL query to execute",
    )
    op_group.add_argument(
        "--describe",
        metavar="OBJECT",
        help="Describe a Salesforce object (outputs JSON)",
    )
    op_group.add_argument(
        "--count",
        metavar="OBJECT",
        help="Count records in a Salesforce object",
    )
    op_group.add_argument(
        "--auth-only",
        action="store_true",
        help="Authenticate and cache the token, then exit (no query)",
    )

    # Query modifiers
    parser.add_argument(
        "--where",
        metavar="CLAUSE",
        default="",
        help="WHERE clause for --count (omit the word WHERE)",
    )
    parser.add_argument(
        "--include-deleted",
        action="store_true",
        help="Use query_all() to include soft-deleted records",
    )
    parser.add_argument(
        "--format",
        dest="fmt",
        choices=["csv", "json"],
        default="csv",
        help="Output format for --query (default: csv)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_ROW_LIMIT,
        metavar="N",
        help=f"Max rows to return; 0 = unlimited (default: {DEFAULT_ROW_LIMIT})",
    )
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="Suppress CSV header row",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        metavar="PATH",
        help=f"Path to active.yaml (default: {_DEFAULT_CONFIG})",
    )
    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Validate: at least one operation is required
    if not any([args.query, args.describe, args.count, args.auth_only]):
        parser.print_usage(sys.stderr)
        print(
            "salesforce_connect: error: specify one of --query, --describe, --count, or --auth-only",
            file=sys.stderr,
        )
        return EXIT_CONFIG_AUTH

    # Load config
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"[config error] {exc}", file=sys.stderr)
        return EXIT_CONFIG_AUTH

    # Build connection (triggers auth flow for oauth_web)
    try:
        sf = build_connection(cfg)
    except (ConfigError, AuthError) as exc:
        print(f"[auth error] {exc}", file=sys.stderr)
        return EXIT_CONFIG_AUTH
    except NetworkError as exc:
        print(f"[network error] {exc}", file=sys.stderr)
        return EXIT_NETWORK

    runner = SalesforceRunner(cfg, sf)

    try:
        if args.auth_only:
            return _run_auth_only(runner)
        elif args.query:
            return _run_query(runner, args)
        elif args.describe:
            return _run_describe(runner, args.describe)
        else:
            return _run_count(runner, args)
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


def _run_auth_only(runner: SalesforceRunner) -> int:
    """Verify auth succeeded and print identity info."""
    try:
        identity = runner.get_identity()
        username = (
            identity.get("preferred_username")
            or identity.get("Username")
            or identity.get("username")
            or "(unknown)"
        )
        sf_instance = getattr(runner._sf, "sf_instance", "")
        instance_url = f"https://{sf_instance}" if sf_instance else "(see active.yaml)"
        print(f"Authenticated as {username} @ {instance_url}")
    except Exception:
        print("Authenticated successfully.")
    return EXIT_SUCCESS


def _run_query(runner: SalesforceRunner, args: argparse.Namespace) -> int:
    columns, rows = runner.execute_query(
        args.query,
        row_limit=args.limit,
        include_deleted=args.include_deleted,
    )
    output = format_results(
        columns,
        rows,
        fmt=args.fmt,
        row_limit=args.limit,
        include_header=not args.no_header,
    )
    print(output, end="")
    return EXIT_SUCCESS


def _run_describe(runner: SalesforceRunner, object_name: str) -> int:
    desc = runner.describe(object_name)
    print(json.dumps(desc, indent=2, default=str))
    return EXIT_SUCCESS


def _run_count(runner: SalesforceRunner, args: argparse.Namespace) -> int:
    total = runner.count(args.count, where_clause=args.where)
    print(f"total_records: {total}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
