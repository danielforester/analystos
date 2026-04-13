"""
Oracle connectivity core for AnalystOS.

Uses python-oracledb in thin mode by default (no Oracle Instant Client required).
Supports three auth modes:
  - password  — user + password stored inline in active.yaml (on-prem, standard)
  - wallet    — mTLS wallet, no username/password (Oracle Cloud Autonomous DB)
  - tns       — named TNS alias with tnsnames.ora (on-prem centralised config)

Thick mode (Instant Client) is available via OracleConfig.thick_mode = True.

Oracle queries are synchronous — no polling required.
"""

from __future__ import annotations

import getpass
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import oracledb
except ImportError:
    oracledb = None  # type: ignore[assignment]

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
    "OracleConfig",
    "OracleRunner",
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

# ORA- codes that indicate credential / certificate problems
_AUTH_ORA_CODES = {
    "ORA-01017",  # invalid username/password
    "ORA-28000",  # account locked
    "ORA-28001",  # password expired
    "ORA-28860",  # wallet certificate expired
    "ORA-28861",  # SSL handshake failed (expired cert)
    "ORA-12541",  # no listener (surfaces quickly, treat as network)
}

# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class OracleConfig:
    # Connection target
    host: str = ""
    port: int = 1521
    service_name: str = ""          # preferred over sid
    sid: str = ""                   # legacy fallback

    # Auth
    auth_mode: str = "password"     # password | wallet | tns
    user: str = ""                  # username (set in active.yaml)
    password: str = ""              # password (set in active.yaml — file is gitignored)
    wallet_location: str = ""
    wallet_password: str = ""       # wallet password (set in active.yaml)
    tns_alias: str = ""
    tns_admin_env: str = ""         # env var pointing to TNS_ADMIN directory (not a secret)

    # Runtime
    schema_scope: list[str] = field(default_factory=list)
    fetch_size: int = DEFAULT_FETCH_SIZE
    thick_mode: bool = False


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_config(config_path: Path) -> OracleConfig:
    """Load and validate the active Oracle connection from active.yaml."""
    raw = load_yaml_config(config_path)
    conn = find_active_connection(raw, config_path)

    conn_name = conn.get("name", "")
    conn_type = conn.get("type", "")
    if conn_type != "oracle":
        raise ConfigError(
            f"Active connection '{conn_name}' has type '{conn_type}', expected 'oracle'.\n"
            "Switch to an Oracle connection with /db-use or update active.yaml."
        )

    ora = conn.get("oracle") or {}
    auth_mode = ora.get("auth_mode", "password")

    if auth_mode not in ("password", "wallet", "tns"):
        raise ConfigError(
            f"Unknown Oracle auth_mode '{auth_mode}'. Must be: password, wallet, or tns."
        )

    # Validate required fields per auth mode.
    # user/password are not required here — they may be absent and prompted at connect time.
    if auth_mode == "password":
        _require_one_of(ora, ["service_name", "sid", "host"], conn_name, config_path)
    elif auth_mode == "wallet":
        _require(ora, ["wallet_location"], conn_name, config_path)
        _require_one_of(ora, ["service_name", "tns_alias", "host"], conn_name, config_path)
    elif auth_mode == "tns":
        _require(ora, ["tns_alias"], conn_name, config_path)

    return OracleConfig(
        host=ora.get("host", ""),
        port=int(ora.get("port", 1521)),
        service_name=ora.get("service_name", ""),
        sid=ora.get("sid", ""),
        auth_mode=auth_mode,
        user=ora.get("user", ""),
        password=ora.get("password", ""),
        wallet_location=ora.get("wallet_location", ""),
        wallet_password=ora.get("wallet_password", ""),
        tns_alias=ora.get("tns_alias", ""),
        tns_admin_env=ora.get("tns_admin_env", ""),
        schema_scope=ora.get("schema_scope") or [],
        fetch_size=int(ora.get("fetch_size", DEFAULT_FETCH_SIZE)),
        thick_mode=bool(ora.get("thick_mode", False)),
    )


def _require(block: dict, keys: list[str], conn_name: str, config_path: Path) -> None:
    missing = [k for k in keys if not block.get(k)]
    if missing:
        raise ConfigError(
            f"Oracle connection '{conn_name}' is missing required field(s): {', '.join(missing)}\n"
            f"Check the 'oracle:' block in {config_path}."
        )


def _require_one_of(block: dict, keys: list[str], conn_name: str, config_path: Path) -> None:
    if not any(block.get(k) for k in keys):
        raise ConfigError(
            f"Oracle connection '{conn_name}' requires at least one of: {', '.join(keys)}\n"
            f"Check the 'oracle:' block in {config_path}."
        )


# ---------------------------------------------------------------------------
# Connection construction
# ---------------------------------------------------------------------------


def _ensure_oracledb() -> None:
    if oracledb is None:
        raise ConfigError(
            "python-oracledb is not installed.\n"
            "Install it with: pip install oracledb"
        )


def build_connection(cfg: OracleConfig) -> "oracledb.Connection":
    """
    Build an oracledb Connection from OracleConfig.

    Thin mode is the default (no Oracle Instant Client required).
    Set cfg.thick_mode = True and ensure Oracle Instant Client is installed to
    enable thick mode for advanced features.
    """
    _ensure_oracledb()

    if cfg.thick_mode:
        try:
            oracledb.init_oracle_client()
        except Exception as exc:
            raise ConfigError(
                f"Failed to initialise Oracle thick mode (Instant Client): {exc}\n"
                "Ensure Oracle Instant Client is installed, or set thick_mode: false."
            ) from exc

    try:
        if cfg.auth_mode == "password":
            return _connect_password(cfg)
        elif cfg.auth_mode == "wallet":
            return _connect_wallet(cfg)
        else:
            return _connect_tns(cfg)
    except (AuthError, ConfigError, NetworkError):
        raise  # don't wrap typed connector errors in DatabaseError handler
    except oracledb.DatabaseError as exc:
        _raise_db_error(exc, cfg)


def _connect_password(cfg: OracleConfig) -> "oracledb.Connection":
    user = cfg.user or _prompt_credential("Oracle username")
    password = cfg.password or _prompt_credential("Oracle password", secret=True)

    if cfg.service_name:
        return oracledb.connect(
            user=user, password=password,
            host=cfg.host, port=cfg.port, service_name=cfg.service_name,
        )
    elif cfg.sid:
        dsn = oracledb.makedsn(cfg.host, cfg.port, sid=cfg.sid)
        return oracledb.connect(user=user, password=password, dsn=dsn)
    else:
        # tns_alias style but password auth
        return oracledb.connect(user=user, password=password, dsn=cfg.host)


def _connect_wallet(cfg: OracleConfig) -> "oracledb.Connection":
    # DSN is either tns_alias or service_name
    dsn = cfg.tns_alias or cfg.service_name or cfg.host
    kwargs: dict = {
        "dsn": dsn,
        "wallet_location": cfg.wallet_location,
    }
    if cfg.wallet_password:
        kwargs["wallet_password"] = cfg.wallet_password

    return oracledb.connect(**kwargs)


def _connect_tns(cfg: OracleConfig) -> "oracledb.Connection":
    if cfg.tns_admin_env:
        tns_admin_dir = os.environ.get(cfg.tns_admin_env)
        if tns_admin_dir:
            os.environ["TNS_ADMIN"] = tns_admin_dir

    user = cfg.user or _prompt_credential("Oracle username")
    password = cfg.password or _prompt_credential("Oracle password", secret=True)
    return oracledb.connect(user=user, password=password, dsn=cfg.tns_alias)


def _prompt_credential(label: str, secret: bool = False) -> str:
    """Prompt for a credential interactively if stdin is a TTY; raise AuthError otherwise."""
    field = label.lower().replace(" ", "_")
    if not sys.stdin.isatty():
        raise AuthError(
            f"Oracle {label.lower()} is not set in active.yaml and no interactive terminal is available.\n"
            f"Add '{field}: <value>' to the oracle: block in active.yaml (the file is gitignored)."
        )
    value = getpass.getpass(f"{label}: ") if secret else input(f"{label}: ")
    if not value:
        raise AuthError(f"Oracle {label.lower()} cannot be empty.")
    return value


def _raise_db_error(exc: "oracledb.DatabaseError", cfg: OracleConfig) -> None:
    """Map ORA- codes to typed exceptions."""
    msg = str(exc)
    code = _extract_ora_code(msg)

    if code in ("ORA-28860", "ORA-28861"):
        raise AuthError(
            f"Oracle wallet certificate has expired or is invalid ({code}).\n"
            "Renew the wallet via Oracle Cloud Console or your DBA."
        ) from exc
    if code in ("ORA-01017", "ORA-28000", "ORA-28001"):
        raise AuthError(
            f"Oracle authentication failed ({code}): {msg}\n"
            "Check username/password in active.yaml and account status."
        ) from exc
    if code == "ORA-12541":
        raise NetworkError(
            f"Oracle listener not found ({code}): {msg}\n"
            f"Check host={cfg.host}, port={cfg.port} in active.yaml."
        ) from exc
    raise QueryError(f"Oracle database error: {msg}") from exc


def _extract_ora_code(message: str) -> str:
    import re
    m = re.search(r"ORA-\d+", message)
    return m.group(0) if m else ""


# ---------------------------------------------------------------------------
# Query runner
# ---------------------------------------------------------------------------


class OracleRunner:
    """
    Executes SQL against Oracle using a pre-built oracledb Connection.

    All execution is synchronous — no polling required.
    """

    def __init__(self, cfg: OracleConfig, connection: "oracledb.Connection") -> None:
        self.cfg = cfg
        self.conn = connection

    def execute_query(
        self, sql: str, row_limit: int = DEFAULT_FETCH_SIZE
    ) -> tuple[list[str], list[list[str]]]:
        """
        Execute a SQL query and return (column_names, rows).

        Fetches in batches of cfg.fetch_size. Stops when row_limit is reached
        (if row_limit > 0) or all rows are consumed.
        All values are coerced to strings for uniform output handling.
        """
        try:
            cursor = self.conn.cursor()
            cursor.arraysize = self.cfg.fetch_size
            cursor.execute(sql)
        except oracledb.DatabaseError as exc:
            _raise_db_error(exc, self.cfg)

        columns = [col[0].lower() for col in cursor.description]
        rows: list[list[str]] = []
        batch_size = self.cfg.fetch_size

        while True:
            batch = cursor.fetchmany(batch_size)
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
    ) -> tuple[str, Optional[int], list[str]]:
        """
        Run EXPLAIN PLAN FOR {sql} and return (plan_text, estimated_rows, full_table_scans).

        All three steps run on the same connection so plan_table is visible.
        estimated_rows is None if cardinality could not be read.
        full_table_scans is a list of table names with TABLE ACCESS FULL operations.
        """
        cursor = self.conn.cursor()
        try:
            # Step 1: generate the plan (does not execute the query)
            cursor.execute(f"EXPLAIN PLAN FOR {sql}")

            # Step 2: read the formatted plan text
            cursor.execute("SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY())")
            plan_rows = cursor.fetchall()
            plan_text = "\n".join(str(row[0]) for row in plan_rows if row)

            # Step 3: read root-level cardinality from plan_table
            cursor.execute(
                "SELECT cardinality FROM plan_table "
                "WHERE plan_id = (SELECT MAX(plan_id) FROM plan_table) "
                "AND id = 0"
            )
            card_row = cursor.fetchone()
            estimated_rows = int(card_row[0]) if card_row and card_row[0] is not None else None

            # Step 4: detect full table scans
            cursor.execute(
                "SELECT object_name FROM plan_table "
                "WHERE plan_id = (SELECT MAX(plan_id) FROM plan_table) "
                "AND operation = 'TABLE ACCESS' AND options = 'FULL' "
                "ORDER BY id"
            )
            fts_rows = cursor.fetchall()
            full_table_scans = [str(r[0]) for r in fts_rows if r and r[0]]

        except oracledb.DatabaseError as exc:
            cursor.close()
            _raise_db_error(exc, self.cfg)
        finally:
            cursor.close()

        return plan_text, estimated_rows, full_table_scans


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_str(value: object) -> str:
    """Convert any Oracle column value to a plain string."""
    if value is None:
        return ""
    return str(value)
