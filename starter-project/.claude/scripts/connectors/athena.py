"""
Athena connectivity core for AnalystOS.

Handles config loading, boto3 session construction (profile/keys/env auth modes,
including SSO), async query execution with polling, result pagination, and output
formatting. Designed to be imported by athena_connect.py (CLI) or any future code
that needs to execute Athena queries programmatically.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import boto3
import botocore.exceptions

from .common import (
    AuthError,
    ConfigError,
    NetworkError,
    QueryError,
    TimeoutError,
    find_active_connection,
    format_results,
    load_yaml_config,
)

# Re-export so callers can import everything from this module
__all__ = [
    "AthenaConfig",
    "AthenaRunner",
    "AuthError",
    "ConfigError",
    "NetworkError",
    "QueryError",
    "TimeoutError",
    "build_boto3_session",
    "extract_bytes_from_explain",
    "format_results",
    "load_config",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_ROW_LIMIT = 1000
POLL_INTERVAL_START = 1.0   # seconds
POLL_INTERVAL_MAX = 15.0    # seconds
POLL_BACKOFF_FACTOR = 1.5


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class AthenaConfig:
    region: str
    database: str
    s3_output_location: str
    workgroup: str = "primary"
    auth_mode: str = "env"                      # profile | keys | env
    profile_name: Optional[str] = None
    access_key_id_env: Optional[str] = None
    secret_access_key_env: Optional[str] = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_config(config_path: Path) -> AthenaConfig:
    """Load and validate the active Athena connection from active.yaml."""
    raw = load_yaml_config(config_path)
    conn = find_active_connection(raw, config_path)

    conn_name = conn.get("name", "")
    conn_type = conn.get("type", "")
    if conn_type != "athena":
        raise ConfigError(
            f"Active connection '{conn_name}' has type '{conn_type}', expected 'athena'.\n"
            "Switch to an Athena connection with /db-use or update active.yaml."
        )

    athena = conn.get("athena") or {}

    region = athena.get("region")
    database = athena.get("database")
    s3_output = athena.get("s3_output_location")

    missing = [k for k, v in [("region", region), ("database", database), ("s3_output_location", s3_output)] if not v]
    if missing:
        raise ConfigError(
            f"Athena config is missing required field(s): {', '.join(missing)}\n"
            f"Check the 'athena:' block for connection '{conn_name}' in {config_path}."
        )

    aws = athena.get("aws") or {}
    auth_mode = aws.get("auth", "env")
    if auth_mode not in ("profile", "keys", "env"):
        raise ConfigError(
            f"Unknown auth mode '{auth_mode}'. Must be one of: profile, keys, env."
        )

    return AthenaConfig(
        region=region,
        database=database,
        s3_output_location=s3_output,
        workgroup=athena.get("workgroup", "primary"),
        auth_mode=auth_mode,
        profile_name=aws.get("profile_name"),
        access_key_id_env=aws.get("access_key_id_env"),
        secret_access_key_env=aws.get("secret_access_key_env"),
        timeout_seconds=int(athena.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
    )


# ---------------------------------------------------------------------------
# Session construction
# ---------------------------------------------------------------------------

# botocore exception types that indicate an expired/missing SSO token
_SSO_TOKEN_ERRORS = (
    "SSOTokenLoadError",
    "UnauthorizedSSOTokenError",
    "TokenRetrievalError",
)


def _is_sso_token_error(exc: Exception) -> bool:
    return type(exc).__name__ in _SSO_TOKEN_ERRORS


def build_boto3_session(cfg: AthenaConfig) -> boto3.Session:
    """
    Build a boto3 Session using the auth mode from AthenaConfig.

    For 'profile' mode, credentials are eagerly resolved so that an expired SSO
    token raises AuthError immediately (before any API call is attempted).
    """
    if cfg.auth_mode == "profile":
        return _session_from_profile(cfg)
    elif cfg.auth_mode == "keys":
        return _session_from_keys(cfg)
    else:
        # env — rely on boto3's default credential chain
        return boto3.Session(region_name=cfg.region)


def _session_from_profile(cfg: AthenaConfig) -> boto3.Session:
    if not cfg.profile_name:
        raise ConfigError(
            "auth mode is 'profile' but 'profile_name' is not set in the aws: block."
        )
    try:
        session = boto3.Session(profile_name=cfg.profile_name)
        # Eagerly resolve so expired SSO tokens surface now, not at first API call
        creds = session.get_credentials()
        if creds is None:
            raise AuthError(
                f"No credentials found for AWS profile '{cfg.profile_name}'.\n"
                f"Check ~/.aws/config and ensure the profile is configured."
            )
        creds.get_frozen_credentials()
        return session
    except botocore.exceptions.ProfileNotFound:
        raise AuthError(
            f"AWS profile '{cfg.profile_name}' not found in ~/.aws/config.\n"
            "Add the profile or correct the profile_name in active.yaml."
        )
    except Exception as exc:
        if _is_sso_token_error(exc):
            raise AuthError(
                f"SSO token expired or missing for profile '{cfg.profile_name}'.\n"
                f"Run: aws sso login --profile {cfg.profile_name}\n"
                "Then retry your command."
            ) from exc
        # Re-raise unknown auth errors as AuthError so the CLI maps them correctly
        raise AuthError(f"Failed to resolve credentials for profile '{cfg.profile_name}': {exc}") from exc


def _session_from_keys(cfg: AthenaConfig) -> boto3.Session:
    key_id_var = cfg.access_key_id_env or "AWS_ACCESS_KEY_ID"
    secret_var = cfg.secret_access_key_env or "AWS_SECRET_ACCESS_KEY"

    key_id = os.environ.get(key_id_var)
    secret = os.environ.get(secret_var)

    if not key_id:
        raise AuthError(
            f"Environment variable '{key_id_var}' (access_key_id_env) is not set."
        )
    if not secret:
        raise AuthError(
            f"Environment variable '{secret_var}' (secret_access_key_env) is not set."
        )

    return boto3.Session(
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name=cfg.region,
    )


# ---------------------------------------------------------------------------
# Query runner
# ---------------------------------------------------------------------------


class AthenaRunner:
    """
    Executes SQL against Athena using a pre-built boto3 Session.

    Handles the async lifecycle: start → poll → fetch results.
    """

    def __init__(self, cfg: AthenaConfig, session: boto3.Session) -> None:
        self.cfg = cfg
        try:
            self.client = session.client("athena", region_name=cfg.region)
        except botocore.exceptions.BotoCoreError as exc:
            raise NetworkError(f"Failed to create Athena client: {exc}") from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_query(self, sql: str) -> str:
        """Submit a query to Athena. Returns the QueryExecutionId."""
        try:
            response = self.client.start_query_execution(
                QueryString=sql,
                QueryExecutionContext={"Database": self.cfg.database},
                ResultConfiguration={"OutputLocation": self.cfg.s3_output_location},
                WorkGroup=self.cfg.workgroup,
            )
        except botocore.exceptions.ClientError as exc:
            self._raise_client_error(exc, context="start_query_execution")
        except botocore.exceptions.BotoCoreError as exc:
            raise NetworkError(f"Network error submitting query: {exc}") from exc
        return response["QueryExecutionId"]

    def poll_until_terminal(self, execution_id: str) -> dict:
        """
        Poll get_query_execution until the query reaches a terminal state.

        Uses exponential backoff (1s → 15s). On timeout, attempts a best-effort
        cancel before raising TimeoutError.
        """
        deadline = time.monotonic() + self.cfg.timeout_seconds
        interval = POLL_INTERVAL_START

        while True:
            if time.monotonic() > deadline:
                self._cancel_query(execution_id)
                raise TimeoutError(
                    f"Query timed out after {self.cfg.timeout_seconds}s "
                    f"(execution_id={execution_id}).\n"
                    "Increase timeout_seconds in active.yaml or simplify the query."
                )

            try:
                response = self.client.get_query_execution(QueryExecutionId=execution_id)
            except botocore.exceptions.BotoCoreError as exc:
                raise NetworkError(f"Network error polling query status: {exc}") from exc

            execution = response["QueryExecution"]
            state = execution["Status"]["State"]

            if state in TERMINAL_STATES:
                if state == "FAILED":
                    reason = execution["Status"].get("StateChangeReason", "Unknown error")
                    raise QueryError(
                        f"Athena query failed: {reason}",
                        execution_id=execution_id,
                    )
                if state == "CANCELLED":
                    raise QueryError(
                        "Athena query was cancelled.",
                        execution_id=execution_id,
                    )
                return execution  # SUCCEEDED

            time.sleep(interval)
            interval = min(interval * POLL_BACKOFF_FACTOR, POLL_INTERVAL_MAX)

    def fetch_results_paginated(
        self, execution_id: str, row_limit: int = DEFAULT_ROW_LIMIT
    ) -> tuple[list[str], list[list[str]]]:
        """
        Retrieve query results from Athena with pagination.

        Returns (column_names, rows). Athena includes a header row in the first
        page — this method strips it. All values are returned as strings.
        If row_limit > 0, stops after that many data rows.
        """
        columns: list[str] = []
        rows: list[list[str]] = []
        next_token: Optional[str] = None
        first_page = True

        while True:
            kwargs: dict = {"QueryExecutionId": execution_id, "MaxResults": 1000}
            if next_token:
                kwargs["NextToken"] = next_token

            try:
                response = self.client.get_query_results(**kwargs)
            except botocore.exceptions.BotoCoreError as exc:
                raise NetworkError(f"Network error fetching results: {exc}") from exc

            result_set = response["ResultSet"]

            if first_page:
                # Extract column names from metadata
                columns = [
                    col["Label"] or col["Name"]
                    for col in result_set["ResultSetMetadata"]["ColumnInfo"]
                ]
                first_page = False
                # Skip header row (first row of first page duplicates column names)
                page_rows = result_set["Rows"][1:]
            else:
                page_rows = result_set["Rows"]

            for row in page_rows:
                rows.append([datum.get("VarCharValue", "") for datum in row["Data"]])
                if row_limit > 0 and len(rows) >= row_limit:
                    return columns, rows

            next_token = response.get("NextToken")
            if not next_token:
                break

        return columns, rows

    def run_explain(self, sql: str) -> str:
        """
        Run EXPLAIN {sql} and return all plan rows joined as a single string.
        """
        explain_sql = f"EXPLAIN {sql}"
        execution_id = self.start_query(explain_sql)
        self.poll_until_terminal(execution_id)
        _, rows = self.fetch_results_paginated(execution_id, row_limit=0)
        return "\n".join(row[0] for row in rows if row)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _cancel_query(self, execution_id: str) -> None:
        """Best-effort query cancellation — never raises."""
        try:
            self.client.stop_query_execution(QueryExecutionId=execution_id)
        except Exception:
            pass

    @staticmethod
    def _raise_client_error(exc: botocore.exceptions.ClientError, context: str) -> None:
        """Map ClientError codes to typed AthenaError subclasses."""
        code = exc.response.get("Error", {}).get("Code", "")
        msg = exc.response.get("Error", {}).get("Message", str(exc))

        auth_codes = {"UnauthorizedException", "AccessDeniedException", "InvalidClientTokenId", "AuthFailure"}
        if code in auth_codes:
            raise AuthError(f"Authentication failed ({code}): {msg}") from exc

        raise QueryError(f"Athena API error in {context} ({code}): {msg}") from exc


# ---------------------------------------------------------------------------
# EXPLAIN byte estimation
# ---------------------------------------------------------------------------


def extract_bytes_from_explain(explain_text: str) -> Optional[int]:
    """
    Parse estimated bytes scanned from an Athena/Presto EXPLAIN output.

    Returns raw bytes as an int, or None if the estimate cannot be determined.
    This is intentionally conservative — EXPLAIN output format varies by query
    type and Athena/Presto version.
    """
    # Pattern 1: "size = 2.5 GB" / "size = 512 MB" / "size = 1.0 TB"
    size_match = re.search(
        r"size\s*=\s*([\d.]+)\s*(B|KB|MB|GB|TB)", explain_text, re.IGNORECASE
    )
    if size_match:
        return _to_bytes(float(size_match.group(1)), size_match.group(2).upper())

    # Pattern 2: "dataSize=123456" (numeric bytes)
    data_match = re.search(r"dataSize\s*=\s*(\d+)", explain_text)
    if data_match:
        return int(data_match.group(1))

    # Pattern 3: "rows=X, size=Y" in fragment statistics
    frag_match = re.search(
        r"rows\s*=\s*[\d,]+.*?size\s*=\s*([\d.]+)\s*(B|KB|MB|GB|TB)",
        explain_text,
        re.IGNORECASE | re.DOTALL,
    )
    if frag_match:
        return _to_bytes(float(frag_match.group(1)), frag_match.group(2).upper())

    return None


def _to_bytes(value: float, unit: str) -> int:
    multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    return int(value * multipliers.get(unit, 1))


# format_results is imported from .common and re-exported via __all__
