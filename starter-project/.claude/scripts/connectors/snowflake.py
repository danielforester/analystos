"""
Snowflake connectivity core for AnalystOS.

Uses snowflake-connector-python (the official low-level driver).
Supports four auth modes:
  - password  — user + password from env vars (most common)
  - keypair   — JWT via PEM private key file; no browser, best for CI/CD / service accounts
  - sso       — externalbrowser; opens a browser tab for interactive SSO login
  - oauth     — pre-obtained OAuth token from env var (external IdP / service accounts)

All execution is synchronous via the standard cursor API.
EXPLAIN uses EXPLAIN USING TABULAR to extract bytesAssigned and partitionsTotal
without executing the query.
"""

from __future__ import annotations

import io
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import snowflake.connector
    from snowflake.connector import DictCursor
    from snowflake.connector.errors import DatabaseError, OperationalError, ProgrammingError
except ImportError:
    snowflake = None  # type: ignore[assignment]
    DictCursor = None  # type: ignore[assignment]
    DatabaseError = Exception  # type: ignore[assignment]
    OperationalError = Exception  # type: ignore[assignment]
    ProgrammingError = Exception  # type: ignore[assignment]

from .common import (
    AuthError,
    ConfigError,
    NetworkError,
    QueryError,
    find_active_connection,
    format_results,
    load_yaml_config,
)

__all__ = [
    "SnowflakeConfig",
    "SnowflakeRunner",
    "AuthError",
    "ConfigError",
    "NetworkError",
    "QueryError",
    "build_connection",
    "format_results",
    "load_config",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_FETCH_SIZE = 1000
DEFAULT_LOGIN_TIMEOUT = 60  # seconds; relevant for SSO browser flow

# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class SnowflakeConfig:
    # Connection target
    account: str = ""           # e.g. "myorg-myaccount" or "myaccount.us-east-1"
    warehouse: str = ""
    database: str = ""
    schema: str = "PUBLIC"
    role: str = ""

    # Auth
    auth_mode: str = "password"         # password | keypair | sso | oauth
    user_env: str = "SNOWFLAKE_USER"    # used by password, keypair, sso, oauth
    password_env: str = "SNOWFLAKE_PASSWORD"

    # keypair mode
    private_key_path_env: str = ""      # env var holding path to PEM file
    private_key_passphrase_env: str = ""  # optional; omit if key is unencrypted

    # oauth mode
    token_env: str = ""                 # env var holding the pre-obtained OAuth token

    # sso mode
    login_timeout: int = DEFAULT_LOGIN_TIMEOUT

    # Runtime
    fetch_size: int = DEFAULT_FETCH_SIZE


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_config(config_path: Path) -> SnowflakeConfig:
    """Load and validate the active Snowflake connection from active.yaml."""
    raw = load_yaml_config(config_path)
    conn = find_active_connection(raw, config_path)

    conn_name = conn.get("name", "")
    conn_type = conn.get("type", "")
    if conn_type != "snowflake":
        raise ConfigError(
            f"Active connection '{conn_name}' has type '{conn_type}', expected 'snowflake'.\n"
            "Switch to a Snowflake connection with /db-use or update active.yaml."
        )

    sf = conn.get("snowflake") or {}
    auth_mode = sf.get("auth_mode", "password")

    if auth_mode not in ("password", "keypair", "sso", "oauth"):
        raise ConfigError(
            f"Unknown Snowflake auth_mode '{auth_mode}'. "
            "Must be: password, keypair, sso, or oauth."
        )

    _require(sf, ["account", "warehouse", "database"], conn_name, config_path)

    if auth_mode == "password":
        _require(sf, ["user_env", "password_env"], conn_name, config_path)
    elif auth_mode == "keypair":
        _require(sf, ["user_env", "private_key_path_env"], conn_name, config_path)
    elif auth_mode == "sso":
        _require(sf, ["user_env"], conn_name, config_path)
    elif auth_mode == "oauth":
        _require(sf, ["user_env", "token_env"], conn_name, config_path)

    return SnowflakeConfig(
        account=sf.get("account", ""),
        warehouse=sf.get("warehouse", ""),
        database=sf.get("database", ""),
        schema=sf.get("schema", "PUBLIC"),
        role=sf.get("role", ""),
        auth_mode=auth_mode,
        user_env=sf.get("user_env", "SNOWFLAKE_USER"),
        password_env=sf.get("password_env", "SNOWFLAKE_PASSWORD"),
        private_key_path_env=sf.get("private_key_path_env", ""),
        private_key_passphrase_env=sf.get("private_key_passphrase_env", ""),
        token_env=sf.get("token_env", ""),
        login_timeout=int(sf.get("login_timeout", DEFAULT_LOGIN_TIMEOUT)),
        fetch_size=int(sf.get("fetch_size", DEFAULT_FETCH_SIZE)),
    )


def _require(block: dict, keys: list[str], conn_name: str, config_path: Path) -> None:
    missing = [k for k in keys if not block.get(k)]
    if missing:
        raise ConfigError(
            f"Snowflake connection '{conn_name}' is missing required field(s): "
            f"{', '.join(missing)}\n"
            f"Check the 'snowflake:' block in {config_path}."
        )


# ---------------------------------------------------------------------------
# Connection construction
# ---------------------------------------------------------------------------


def _ensure_connector() -> None:
    if snowflake is None:
        raise ConfigError(
            "snowflake-connector-python is not installed.\n"
            "Install it with: pip install snowflake-connector-python"
        )


def build_connection(cfg: SnowflakeConfig) -> "snowflake.connector.SnowflakeConnection":
    """
    Build a Snowflake connection from SnowflakeConfig.

    Auth modes:
      password — standard user + password
      keypair  — JWT via PEM private key (no browser required; best for automation)
      sso      — externalbrowser; opens a browser tab for SAML/OAuth SSO login
      oauth    — pre-obtained bearer token passed as authenticator="oauth"
    """
    _ensure_connector()

    try:
        if cfg.auth_mode == "password":
            return _connect_password(cfg)
        elif cfg.auth_mode == "keypair":
            return _connect_keypair(cfg)
        elif cfg.auth_mode == "sso":
            return _connect_sso(cfg)
        else:
            return _connect_oauth(cfg)
    except (AuthError, ConfigError, NetworkError):
        raise
    except (DatabaseError, OperationalError) as exc:
        _raise_connector_error(exc, cfg)


def _common_kwargs(cfg: SnowflakeConfig) -> dict:
    """Base connection kwargs shared across all auth modes."""
    kwargs: dict = {
        "account": cfg.account,
        "warehouse": cfg.warehouse,
        "database": cfg.database,
        "schema": cfg.schema,
    }
    if cfg.role:
        kwargs["role"] = cfg.role
    return kwargs


def _connect_password(cfg: SnowflakeConfig) -> "snowflake.connector.SnowflakeConnection":
    user = _resolve_env(cfg.user_env)
    password = _resolve_env(cfg.password_env)
    return snowflake.connector.connect(
        user=user,
        password=password,
        **_common_kwargs(cfg),
    )


def _connect_keypair(cfg: SnowflakeConfig) -> "snowflake.connector.SnowflakeConnection":
    user = _resolve_env(cfg.user_env)
    key_path = _resolve_env(cfg.private_key_path_env)

    passphrase: Optional[bytes] = None
    if cfg.private_key_passphrase_env:
        raw = os.environ.get(cfg.private_key_passphrase_env)
        if raw:
            passphrase = raw.encode()

    pkb = _load_private_key_der(key_path, passphrase)
    return snowflake.connector.connect(
        user=user,
        private_key=pkb,
        **_common_kwargs(cfg),
    )


def _load_private_key_der(key_path: str, passphrase: Optional[bytes]) -> bytes:
    """Load a PEM private key file and return DER-encoded bytes for Snowflake."""
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
        )
    except ImportError:
        raise ConfigError(
            "The 'cryptography' package is required for key-pair auth.\n"
            "Install it with: pip install cryptography\n"
            "(It is normally included with snowflake-connector-python.)"
        )

    path = Path(key_path)
    if not path.exists():
        raise AuthError(
            f"Private key file not found: {key_path}\n"
            f"Check the path stored in the '{cfg_hint}' env var."
        )

    try:
        p_key = serialization.load_pem_private_key(
            path.read_bytes(),
            password=passphrase,
            backend=default_backend(),
        )
    except (ValueError, TypeError) as exc:
        raise AuthError(
            f"Failed to load private key from {key_path}: {exc}\n"
            "Check that the key file is valid PEM and the passphrase is correct."
        ) from exc

    return p_key.private_bytes(
        encoding=Encoding.DER,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )


# Module-level hint for error messages (set during _connect_keypair)
cfg_hint = "private_key_path_env"


def _connect_sso(cfg: SnowflakeConfig) -> "snowflake.connector.SnowflakeConnection":
    user = _resolve_env(cfg.user_env)
    print(
        f"[snowflake_connect] Opening browser for SSO login to {cfg.account}…\n"
        f"Complete the login within {cfg.login_timeout}s.",
        file=sys.stderr,
    )
    return snowflake.connector.connect(
        user=user,
        authenticator="externalbrowser",
        login_timeout=cfg.login_timeout,
        **_common_kwargs(cfg),
    )


def _connect_oauth(cfg: SnowflakeConfig) -> "snowflake.connector.SnowflakeConnection":
    user = _resolve_env(cfg.user_env)
    token = _resolve_env(cfg.token_env)
    return snowflake.connector.connect(
        user=user,
        authenticator="oauth",
        token=token,
        **_common_kwargs(cfg),
    )


def _resolve_env(var_name: str) -> str:
    value = os.environ.get(var_name)
    if not value:
        raise AuthError(f"Environment variable '{var_name}' is not set.")
    return value


def _raise_connector_error(
    exc: Exception, cfg: SnowflakeConfig
) -> None:
    """Map Snowflake connector errors to typed exceptions."""
    msg = str(exc)
    msg_lower = msg.lower()

    # Auth / credential failures
    if any(kw in msg_lower for kw in (
        "incorrect username or password",
        "authentication failed",
        "jwt token",
        "oauth",
        "incorrect login",
        "account does not exist",
        "token is expired",
    )):
        raise AuthError(f"Snowflake authentication failed: {msg}") from exc

    # Network / connectivity
    if any(kw in msg_lower for kw in (
        "failed to connect",
        "connection refused",
        "could not connect",
        "network error",
        "hostname",
        "socket",
    )):
        raise NetworkError(
            f"Could not connect to Snowflake account '{cfg.account}': {msg}\n"
            "Check the account identifier in active.yaml."
        ) from exc

    raise QueryError(f"Snowflake error: {msg}") from exc


# ---------------------------------------------------------------------------
# Query runner
# ---------------------------------------------------------------------------


class SnowflakeRunner:
    """
    Executes SQL against Snowflake using a pre-built connection.

    All execution is synchronous via the standard cursor API.
    """

    def __init__(
        self, cfg: SnowflakeConfig, connection: "snowflake.connector.SnowflakeConnection"
    ) -> None:
        self.cfg = cfg
        self.conn = connection

    def execute_query(
        self, sql: str, row_limit: int = DEFAULT_FETCH_SIZE
    ) -> tuple[list[str], list[list[str]]]:
        """
        Execute a SQL query and return (column_names, rows).

        Column names are lowercased. Values are coerced to strings; None → "".
        Fetches in batches of cfg.fetch_size, stopping at row_limit (if > 0).
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql)
        except (DatabaseError, ProgrammingError) as exc:
            cursor.close()
            _raise_connector_error(exc, self.cfg)

        columns = [col[0].lower() for col in cursor.description]
        rows: list[list[str]] = []

        while True:
            batch = cursor.fetchmany(self.cfg.fetch_size)
            if not batch:
                break
            for raw_row in batch:
                rows.append([_to_str(v) for v in raw_row])
                if row_limit > 0 and len(rows) >= row_limit:
                    cursor.close()
                    return columns, rows

        cursor.close()
        return columns, rows

    def run_explain(
        self, sql: str
    ) -> tuple[str, Optional[int], Optional[int]]:
        """
        Run EXPLAIN USING TABULAR on a query.

        Returns (plan_text, estimated_bytes, partitions_total).
        - plan_text: human-readable tabular plan formatted as CSV
        - estimated_bytes: sum of bytesAssigned across all plan rows (None if column absent)
        - partitions_total: max of partitionsTotal across all plan rows (None if column absent)

        EXPLAIN USING TABULAR does not execute the query — it is cost-free.
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute(f"EXPLAIN USING TABULAR {sql}")
        except (DatabaseError, ProgrammingError) as exc:
            cursor.close()
            _raise_connector_error(exc, self.cfg)

        col_names = [col[0].lower() for col in cursor.description]
        all_rows = cursor.fetchall()
        cursor.close()

        # Find column indices for cost fields
        bytes_idx = _col_index(col_names, ("bytesassigned", "bytes_assigned"))
        parts_idx = _col_index(col_names, ("partitionstotal", "partitions_total"))

        # Aggregate cost signals
        estimated_bytes: Optional[int] = None
        partitions_total: Optional[int] = None

        if bytes_idx is not None:
            total = sum(
                int(row[bytes_idx])
                for row in all_rows
                if row[bytes_idx] is not None
            )
            estimated_bytes = total

        if parts_idx is not None:
            vals = [int(row[parts_idx]) for row in all_rows if row[parts_idx] is not None]
            partitions_total = max(vals) if vals else None

        # Format plan as readable CSV text
        string_rows = [[_to_str(v) for v in row] for row in all_rows]
        plan_text = format_results(col_names, string_rows, fmt="csv", row_limit=0)

        return plan_text, estimated_bytes, partitions_total


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_str(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _col_index(
    col_names: list[str], candidates: tuple[str, ...]
) -> Optional[int]:
    """Find the index of the first matching column name (case-insensitive)."""
    for candidate in candidates:
        try:
            return col_names.index(candidate)
        except ValueError:
            continue
    return None
