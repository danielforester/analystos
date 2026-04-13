"""
Unit tests for scripts/connectors/oracle.py.

All tests mock oracledb — no Oracle DB or Oracle Client required.
Run with: python -m pytest scripts/connectors/tests/ -v
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import yaml

from scripts.connectors.common import (
    AuthError,
    ConfigError,
    NetworkError,
    QueryError,
    format_results,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "active.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _minimal_oracle_yaml(auth_mode: str = "password", **oracle_overrides) -> dict:
    base = {
        "host": "db.example.com",
        "port": 1521,
        "service_name": "ORCL",
        "auth_mode": auth_mode,
        "user": "testuser",
        "password": "testpass",
    }
    base.update(oracle_overrides)
    return {
        "active": "test",
        "connections": [
            {
                "name": "test",
                "type": "oracle",
                "display_name": "Test Oracle",
                "read_only": True,
                "transport": "direct",
                "oracle": base,
            }
        ],
    }


def _mock_connection() -> MagicMock:
    """Return a mock oracledb connection."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def _make_cursor_results(cursor: MagicMock, columns: list[tuple], rows: list[tuple]) -> None:
    """Configure cursor.description and fetchmany/fetchall on a mock cursor."""
    cursor.description = [(col, None, None, None, None, None, None) for col in columns]
    cursor.fetchmany.side_effect = [rows, []]  # first call returns rows, second signals EOF
    cursor.fetchall.return_value = rows
    cursor.fetchone.return_value = rows[0] if rows else None


# ---------------------------------------------------------------------------
# Import oracle module with mocked oracledb
# ---------------------------------------------------------------------------

# Patch oracledb at module level for all tests that need it
@pytest.fixture(autouse=True)
def mock_oracledb():
    """Provide a mock oracledb module for all tests."""
    mock_mod = MagicMock()
    mock_mod.DatabaseError = Exception  # make it catchable
    with patch.dict("sys.modules", {"oracledb": mock_mod}):
        # Re-import oracle module with mocked oracledb in place
        import importlib
        import scripts.connectors.oracle as oracle_mod
        oracle_mod.oracledb = mock_mod
        yield mock_mod


# ---------------------------------------------------------------------------
# load_config tests
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_missing_file_raises_config_error(self, tmp_path):
        from scripts.connectors.oracle import load_config
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.yaml")

    def test_wrong_type_raises_config_error(self, tmp_path):
        from scripts.connectors.oracle import load_config
        data = {
            "active": "myconn",
            "connections": [{"name": "myconn", "type": "athena"}],
        }
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="athena"):
            load_config(p)

    def test_unknown_auth_mode_raises_config_error(self, tmp_path):
        from scripts.connectors.oracle import load_config
        data = _minimal_oracle_yaml(auth_mode="magic")
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="Unknown Oracle auth_mode"):
            load_config(p)

    def test_password_mode_missing_host_raises_config_error(self, tmp_path):
        from scripts.connectors.oracle import load_config
        data = _minimal_oracle_yaml(auth_mode="password")
        del data["connections"][0]["oracle"]["host"]
        del data["connections"][0]["oracle"]["service_name"]
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="service_name"):
            load_config(p)

    def test_wallet_mode_missing_wallet_location_raises_config_error(self, tmp_path):
        from scripts.connectors.oracle import load_config
        data = _minimal_oracle_yaml(auth_mode="wallet")
        # wallet mode requires wallet_location; no default set
        del data["connections"][0]["oracle"]["user"]
        del data["connections"][0]["oracle"]["password"]
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="wallet_location"):
            load_config(p)

    def test_tns_mode_missing_tns_alias_raises_config_error(self, tmp_path):
        from scripts.connectors.oracle import load_config
        data = _minimal_oracle_yaml(auth_mode="tns")
        # tns_alias not set
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="tns_alias"):
            load_config(p)

    def test_valid_password_config(self, tmp_path):
        from scripts.connectors.oracle import load_config, OracleConfig
        p = _write_yaml(tmp_path, _minimal_oracle_yaml())
        cfg = load_config(p)
        assert cfg.host == "db.example.com"
        assert cfg.port == 1521
        assert cfg.service_name == "ORCL"
        assert cfg.auth_mode == "password"
        assert cfg.user == "testuser"
        assert cfg.password == "testpass"
        assert cfg.fetch_size == 1000
        assert cfg.thick_mode is False

    def test_valid_wallet_config(self, tmp_path):
        from scripts.connectors.oracle import load_config
        data = _minimal_oracle_yaml(
            auth_mode="wallet",
            wallet_location="/path/to/wallet",
        )
        del data["connections"][0]["oracle"]["user"]
        del data["connections"][0]["oracle"]["password"]
        p = _write_yaml(tmp_path, data)
        cfg = load_config(p)
        assert cfg.auth_mode == "wallet"
        assert cfg.wallet_location == "/path/to/wallet"

    def test_valid_tns_config(self, tmp_path):
        from scripts.connectors.oracle import load_config
        data = _minimal_oracle_yaml(
            auth_mode="tns",
            tns_alias="MY_DB",
            tns_admin_env="TNS_ADMIN",
        )
        p = _write_yaml(tmp_path, data)
        cfg = load_config(p)
        assert cfg.auth_mode == "tns"
        assert cfg.tns_alias == "MY_DB"
        assert cfg.tns_admin_env == "TNS_ADMIN"
        assert cfg.user == "testuser"
        assert cfg.password == "testpass"

    def test_custom_fetch_size(self, tmp_path):
        from scripts.connectors.oracle import load_config
        data = _minimal_oracle_yaml(fetch_size=500)
        p = _write_yaml(tmp_path, data)
        cfg = load_config(p)
        assert cfg.fetch_size == 500

    def test_schema_scope_parsed(self, tmp_path):
        from scripts.connectors.oracle import load_config
        data = _minimal_oracle_yaml()
        data["connections"][0]["oracle"]["schema_scope"] = ["SALES", "HR"]
        p = _write_yaml(tmp_path, data)
        cfg = load_config(p)
        assert cfg.schema_scope == ["SALES", "HR"]


# ---------------------------------------------------------------------------
# build_connection tests
# ---------------------------------------------------------------------------


class TestBuildConnection:
    def test_password_mode_calls_connect_with_credentials(self, mock_oracledb):
        from scripts.connectors.oracle import build_connection, OracleConfig
        cfg = OracleConfig(
            host="db.example.com", port=1521, service_name="ORCL",
            auth_mode="password", user="testuser", password="testpass",
        )
        mock_oracledb.connect.return_value = MagicMock()
        build_connection(cfg)
        mock_oracledb.connect.assert_called_once_with(
            user="testuser", password="testpass",
            host="db.example.com", port=1521, service_name="ORCL",
        )

    def test_password_mode_missing_credentials_non_tty_raises_auth_error(self, mock_oracledb, monkeypatch):
        from scripts.connectors.oracle import build_connection, OracleConfig
        import scripts.connectors.oracle as oracle_mod
        monkeypatch.setattr(oracle_mod.sys.stdin, "isatty", lambda: False)
        cfg = OracleConfig(
            host="db.example.com", port=1521, service_name="ORCL",
            auth_mode="password", user="", password="",
        )
        with pytest.raises(AuthError, match="active.yaml"):
            build_connection(cfg)

    def test_wallet_mode_calls_connect_without_user_password(self, mock_oracledb):
        from scripts.connectors.oracle import build_connection, OracleConfig
        cfg = OracleConfig(
            service_name="mydb_high",
            auth_mode="wallet",
            wallet_location="/opt/wallet",
        )
        mock_oracledb.connect.return_value = MagicMock()
        build_connection(cfg)
        call_kwargs = mock_oracledb.connect.call_args.kwargs
        assert "user" not in call_kwargs
        assert "password" not in call_kwargs
        assert call_kwargs["wallet_location"] == "/opt/wallet"

    def test_wallet_mode_with_inline_wallet_password(self, mock_oracledb):
        from scripts.connectors.oracle import build_connection, OracleConfig
        cfg = OracleConfig(
            service_name="mydb_high",
            auth_mode="wallet",
            wallet_location="/opt/wallet",
            wallet_password="walletpass123",
        )
        mock_oracledb.connect.return_value = MagicMock()
        build_connection(cfg)
        call_kwargs = mock_oracledb.connect.call_args.kwargs
        assert call_kwargs["wallet_password"] == "walletpass123"

    def test_wallet_mode_no_wallet_password_omits_kwarg(self, mock_oracledb):
        from scripts.connectors.oracle import build_connection, OracleConfig
        cfg = OracleConfig(
            service_name="mydb_high",
            auth_mode="wallet",
            wallet_location="/opt/wallet",
            wallet_password="",
        )
        mock_oracledb.connect.return_value = MagicMock()
        build_connection(cfg)
        call_kwargs = mock_oracledb.connect.call_args.kwargs
        assert "wallet_password" not in call_kwargs

    def test_tns_mode_sets_tns_admin_env(self, mock_oracledb, monkeypatch):
        from scripts.connectors.oracle import build_connection, OracleConfig
        monkeypatch.setenv("TNS_ADMIN", "/etc/oracle/network/admin")
        cfg = OracleConfig(
            auth_mode="tns",
            tns_alias="MY_DB",
            tns_admin_env="TNS_ADMIN",
            user="tnsuser",
            password="tnspass",
        )
        mock_oracledb.connect.return_value = MagicMock()
        build_connection(cfg)
        assert os.environ.get("TNS_ADMIN") == "/etc/oracle/network/admin"
        mock_oracledb.connect.assert_called_once_with(
            user="tnsuser", password="tnspass", dsn="MY_DB"
        )

    def test_expired_wallet_cert_raises_auth_error(self, mock_oracledb, monkeypatch):
        from scripts.connectors.oracle import build_connection, OracleConfig
        # Simulate ORA-28860 from DatabaseError
        exc = Exception("ORA-28860: Fatal SSL error")
        mock_oracledb.connect.side_effect = mock_oracledb.DatabaseError(str(exc))
        mock_oracledb.DatabaseError = type("DatabaseError", (Exception,), {})
        mock_oracledb.connect.side_effect = mock_oracledb.DatabaseError("ORA-28860: Fatal SSL error")

        cfg = OracleConfig(
            service_name="mydb_high",
            auth_mode="wallet",
            wallet_location="/opt/wallet",
        )
        with pytest.raises(AuthError, match="wallet certificate"):
            build_connection(cfg)

    def test_invalid_credentials_raises_auth_error(self, mock_oracledb):
        from scripts.connectors.oracle import build_connection, OracleConfig
        mock_oracledb.DatabaseError = type("DatabaseError", (Exception,), {})
        mock_oracledb.connect.side_effect = mock_oracledb.DatabaseError(
            "ORA-01017: invalid username/password; logon denied"
        )
        cfg = OracleConfig(
            host="db.example.com", port=1521, service_name="ORCL",
            auth_mode="password", user="baduser", password="badpass",
        )
        with pytest.raises(AuthError, match="authentication failed"):
            build_connection(cfg)

    def test_no_listener_raises_network_error(self, mock_oracledb):
        from scripts.connectors.oracle import build_connection, OracleConfig
        mock_oracledb.DatabaseError = type("DatabaseError", (Exception,), {})
        mock_oracledb.connect.side_effect = mock_oracledb.DatabaseError(
            "ORA-12541: TNS:no listener"
        )
        cfg = OracleConfig(
            host="db.example.com", port=1521, service_name="ORCL",
            auth_mode="password", user="user", password="pass",
        )
        with pytest.raises(NetworkError, match="listener"):
            build_connection(cfg)

    def test_oracledb_not_installed_raises_config_error(self, monkeypatch):
        import scripts.connectors.oracle as oracle_mod
        original = oracle_mod.oracledb
        oracle_mod.oracledb = None
        try:
            from scripts.connectors.oracle import build_connection, OracleConfig
            cfg = OracleConfig(auth_mode="password")
            with pytest.raises(ConfigError, match="python-oracledb is not installed"):
                build_connection(cfg)
        finally:
            oracle_mod.oracledb = original


# ---------------------------------------------------------------------------
# OracleRunner.execute_query tests
# ---------------------------------------------------------------------------


class TestExecuteQuery:
    def _make_runner(self, mock_oracledb):
        from scripts.connectors.oracle import OracleRunner, OracleConfig
        cfg = OracleConfig(fetch_size=1000)
        conn, cursor = _mock_connection()
        mock_oracledb.DatabaseError = type("DatabaseError", (Exception,), {})
        runner = OracleRunner(cfg, conn)
        return runner, conn, cursor

    def test_returns_columns_and_rows(self, mock_oracledb):
        runner, conn, cursor = self._make_runner(mock_oracledb)
        cursor.description = [
            ("ID", None, None, None, None, None, None),
            ("NAME", None, None, None, None, None, None),
        ]
        cursor.fetchmany.side_effect = [[(1, "Alice"), (2, "Bob")], []]
        cols, rows = runner.execute_query("SELECT id, name FROM employees", row_limit=0)
        assert cols == ["id", "name"]
        assert rows == [["1", "Alice"], ["2", "Bob"]]

    def test_row_limit_stops_early(self, mock_oracledb):
        runner, conn, cursor = self._make_runner(mock_oracledb)
        cursor.description = [("N", None, None, None, None, None, None)]
        cursor.fetchmany.side_effect = [
            [(i,) for i in range(10)],
            [],
        ]
        _, rows = runner.execute_query("SELECT n FROM t", row_limit=5)
        assert len(rows) == 5

    def test_none_values_become_empty_string(self, mock_oracledb):
        runner, conn, cursor = self._make_runner(mock_oracledb)
        cursor.description = [("VAL", None, None, None, None, None, None)]
        cursor.fetchmany.side_effect = [[(None,)], []]
        _, rows = runner.execute_query("SELECT val FROM t", row_limit=0)
        assert rows == [[""]]

    def test_column_names_lowercased(self, mock_oracledb):
        runner, conn, cursor = self._make_runner(mock_oracledb)
        cursor.description = [("EMPLOYEE_ID", None, None, None, None, None, None)]
        cursor.fetchmany.side_effect = [[("42",)], []]
        cols, _ = runner.execute_query("SELECT employee_id FROM emp", row_limit=0)
        assert cols == ["employee_id"]


# ---------------------------------------------------------------------------
# OracleRunner.run_explain tests
# ---------------------------------------------------------------------------


class TestRunExplain:
    def _make_runner(self, mock_oracledb):
        from scripts.connectors.oracle import OracleRunner, OracleConfig
        cfg = OracleConfig(fetch_size=1000)
        conn, cursor = _mock_connection()
        mock_oracledb.DatabaseError = type("DatabaseError", (Exception,), {})
        runner = OracleRunner(cfg, conn)
        return runner, conn, cursor

    def test_returns_plan_text_and_cardinality(self, mock_oracledb):
        runner, conn, cursor = self._make_runner(mock_oracledb)

        plan_rows = [
            ("Plan hash value: 12345678",),
            ("---------------------------------",),
            ("| Id | Operation         | Rows |",),
            ("|  0 | SELECT STATEMENT  | 5000 |",),
        ]
        cursor.fetchall.return_value = plan_rows
        cursor.fetchone.return_value = (5000,)  # cardinality

        # fetchall for full table scans
        cursor.fetchall.side_effect = [plan_rows, []]  # plan text, then no FTS

        plan_text, estimated_rows, fts = runner.run_explain("SELECT * FROM emp")

        assert "Plan hash value" in plan_text
        assert estimated_rows == 5000
        assert fts == []

    def test_detects_full_table_scans(self, mock_oracledb):
        runner, conn, cursor = self._make_runner(mock_oracledb)

        plan_rows = [("| TABLE ACCESS FULL | ORDERS |",)]
        fts_rows = [("ORDERS",), ("CUSTOMERS",)]

        cursor.fetchall.side_effect = [plan_rows, fts_rows]
        cursor.fetchone.return_value = (1000000,)

        _, _, fts = runner.run_explain("SELECT * FROM orders, customers")
        assert "ORDERS" in fts
        assert "CUSTOMERS" in fts

    def test_returns_none_when_cardinality_unavailable(self, mock_oracledb):
        runner, conn, cursor = self._make_runner(mock_oracledb)

        cursor.fetchall.side_effect = [[("-- Plan text --",)], []]
        cursor.fetchone.return_value = None  # no cardinality row

        _, estimated_rows, _ = runner.run_explain("SELECT 1 FROM DUAL")
        assert estimated_rows is None


# ---------------------------------------------------------------------------
# format_results (from common) — Oracle-specific value types
# ---------------------------------------------------------------------------


class TestFormatResultsWithOracleValues:
    """Spot-check format_results with Oracle-style string-coerced values."""

    def test_csv_with_numeric_strings(self):
        cols = ["id", "salary"]
        rows = [["1001", "75000"], ["1002", "82500"]]
        output = format_results(cols, rows, fmt="csv")
        assert "id,salary" in output
        assert "1001,75000" in output

    def test_json_output(self):
        import json
        cols = ["owner", "table_name"]
        rows = [["SALES", "ORDERS"], ["HR", "EMPLOYEES"]]
        output = format_results(cols, rows, fmt="json")
        data = json.loads(output)
        assert data[0] == {"owner": "SALES", "table_name": "ORDERS"}

    def test_empty_string_for_null(self):
        cols = ["comment"]
        rows = [[""], ["some comment"]]
        output = format_results(cols, rows, fmt="csv")
        lines = output.strip().splitlines()
        # csv.writer quotes empty strings as "" (valid RFC 4180 representation)
        assert lines[1] in ("", '""')
