"""
Unit tests for scripts/connectors/snowflake.py.

All tests mock snowflake.connector — no Snowflake account required.
Run with: python -m pytest scripts/connectors/tests/ -v
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _minimal_sf_yaml(auth_mode: str = "password", **sf_overrides) -> dict:
    base = {
        "account": "myorg-myaccount",
        "warehouse": "ANALYST_WH",
        "database": "ANALYTICS",
        "schema": "PUBLIC",
        "auth_mode": auth_mode,
        "user_env": "SNOWFLAKE_USER",
        "password_env": "SNOWFLAKE_PASSWORD",
    }
    base.update(sf_overrides)
    return {
        "active": "test",
        "connections": [
            {
                "name": "test",
                "type": "snowflake",
                "display_name": "Test Snowflake",
                "read_only": True,
                "snowflake": base,
            }
        ],
    }


def _mock_connection() -> tuple[MagicMock, MagicMock]:
    """Return (mock_conn, mock_cursor)."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


# ---------------------------------------------------------------------------
# Module-level mock fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_snowflake_connector():
    """Provide a mock snowflake.connector module for all tests."""
    mock_mod = MagicMock()
    mock_mod.DatabaseError = type("DatabaseError", (Exception,), {})
    mock_mod.OperationalError = type("OperationalError", (Exception,), {})
    mock_mod.ProgrammingError = type("ProgrammingError", (Exception,), {})
    mock_connector_pkg = MagicMock()
    mock_connector_pkg.connector = mock_mod
    mock_connector_pkg.connector.DictCursor = MagicMock()
    mock_connector_pkg.connector.errors = MagicMock()
    mock_connector_pkg.connector.errors.DatabaseError = mock_mod.DatabaseError
    mock_connector_pkg.connector.errors.OperationalError = mock_mod.OperationalError
    mock_connector_pkg.connector.errors.ProgrammingError = mock_mod.ProgrammingError

    with patch.dict(
        "sys.modules",
        {
            "snowflake": mock_connector_pkg,
            "snowflake.connector": mock_mod,
            "snowflake.connector.errors": mock_connector_pkg.connector.errors,
        },
    ):
        import importlib
        import scripts.connectors.snowflake as sf_mod
        sf_mod.snowflake = mock_connector_pkg
        sf_mod.DatabaseError = mock_mod.DatabaseError
        sf_mod.OperationalError = mock_mod.OperationalError
        sf_mod.ProgrammingError = mock_mod.ProgrammingError
        yield mock_mod


# ---------------------------------------------------------------------------
# load_config tests
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_missing_file_raises_config_error(self, tmp_path):
        from scripts.connectors.snowflake import load_config
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.yaml")

    def test_wrong_type_raises_config_error(self, tmp_path):
        from scripts.connectors.snowflake import load_config
        data = {
            "active": "myconn",
            "connections": [{"name": "myconn", "type": "oracle"}],
        }
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="oracle"):
            load_config(p)

    def test_unknown_auth_mode_raises_config_error(self, tmp_path):
        from scripts.connectors.snowflake import load_config
        data = _minimal_sf_yaml(auth_mode="magic")
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="Unknown Snowflake auth_mode"):
            load_config(p)

    def test_password_mode_missing_user_env_raises_config_error(self, tmp_path):
        from scripts.connectors.snowflake import load_config
        data = _minimal_sf_yaml(auth_mode="password")
        del data["connections"][0]["snowflake"]["user_env"]
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="user_env"):
            load_config(p)

    def test_keypair_mode_missing_private_key_path_env_raises_config_error(self, tmp_path):
        from scripts.connectors.snowflake import load_config
        data = _minimal_sf_yaml(auth_mode="keypair", user_env="SNOWFLAKE_USER")
        # private_key_path_env not set
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="private_key_path_env"):
            load_config(p)

    def test_oauth_mode_missing_token_env_raises_config_error(self, tmp_path):
        from scripts.connectors.snowflake import load_config
        data = _minimal_sf_yaml(auth_mode="oauth", user_env="SNOWFLAKE_USER")
        # token_env not set
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="token_env"):
            load_config(p)

    def test_valid_password_config(self, tmp_path):
        from scripts.connectors.snowflake import load_config
        p = _write_yaml(tmp_path, _minimal_sf_yaml())
        cfg = load_config(p)
        assert cfg.account == "myorg-myaccount"
        assert cfg.warehouse == "ANALYST_WH"
        assert cfg.database == "ANALYTICS"
        assert cfg.schema == "PUBLIC"
        assert cfg.auth_mode == "password"
        assert cfg.user_env == "SNOWFLAKE_USER"
        assert cfg.password_env == "SNOWFLAKE_PASSWORD"
        assert cfg.fetch_size == 1000

    def test_valid_keypair_config(self, tmp_path):
        from scripts.connectors.snowflake import load_config
        data = _minimal_sf_yaml(
            auth_mode="keypair",
            user_env="SNOWFLAKE_USER",
            private_key_path_env="SF_KEY_PATH",
            private_key_passphrase_env="SF_KEY_PASS",
        )
        p = _write_yaml(tmp_path, data)
        cfg = load_config(p)
        assert cfg.auth_mode == "keypair"
        assert cfg.private_key_path_env == "SF_KEY_PATH"
        assert cfg.private_key_passphrase_env == "SF_KEY_PASS"

    def test_valid_sso_config(self, tmp_path):
        from scripts.connectors.snowflake import load_config
        data = _minimal_sf_yaml(auth_mode="sso", user_env="SNOWFLAKE_USER", login_timeout=120)
        p = _write_yaml(tmp_path, data)
        cfg = load_config(p)
        assert cfg.auth_mode == "sso"
        assert cfg.login_timeout == 120

    def test_valid_oauth_config(self, tmp_path):
        from scripts.connectors.snowflake import load_config
        data = _minimal_sf_yaml(
            auth_mode="oauth",
            user_env="SNOWFLAKE_USER",
            token_env="SF_OAUTH_TOKEN",
        )
        p = _write_yaml(tmp_path, data)
        cfg = load_config(p)
        assert cfg.auth_mode == "oauth"
        assert cfg.token_env == "SF_OAUTH_TOKEN"

    def test_role_and_custom_fetch_size(self, tmp_path):
        from scripts.connectors.snowflake import load_config
        data = _minimal_sf_yaml(role="DATA_ANALYST", fetch_size=500)
        p = _write_yaml(tmp_path, data)
        cfg = load_config(p)
        assert cfg.role == "DATA_ANALYST"
        assert cfg.fetch_size == 500


# ---------------------------------------------------------------------------
# build_connection tests
# ---------------------------------------------------------------------------


class TestBuildConnection:
    def test_password_mode_calls_connect_with_credentials(self, mock_snowflake_connector, monkeypatch):
        from scripts.connectors.snowflake import build_connection, SnowflakeConfig
        import scripts.connectors.snowflake as sf_mod
        monkeypatch.setenv("SNOWFLAKE_USER", "testuser")
        monkeypatch.setenv("SNOWFLAKE_PASSWORD", "testpass")
        cfg = SnowflakeConfig(
            account="myorg-myaccount",
            warehouse="ANALYST_WH",
            database="ANALYTICS",
            schema="PUBLIC",
            auth_mode="password",
            user_env="SNOWFLAKE_USER",
            password_env="SNOWFLAKE_PASSWORD",
        )
        mock_snowflake_connector.connect.return_value = MagicMock()
        sf_mod.snowflake = MagicMock()
        sf_mod.snowflake.connector = mock_snowflake_connector
        build_connection(cfg)
        mock_snowflake_connector.connect.assert_called_once()
        call_kwargs = mock_snowflake_connector.connect.call_args.kwargs
        assert call_kwargs["user"] == "testuser"
        assert call_kwargs["password"] == "testpass"
        assert call_kwargs["account"] == "myorg-myaccount"

    def test_password_mode_missing_user_env_raises_auth_error(self, mock_snowflake_connector, monkeypatch):
        from scripts.connectors.snowflake import build_connection, SnowflakeConfig
        import scripts.connectors.snowflake as sf_mod
        monkeypatch.delenv("SNOWFLAKE_USER", raising=False)
        sf_mod.snowflake = MagicMock()
        sf_mod.snowflake.connector = mock_snowflake_connector
        cfg = SnowflakeConfig(
            account="myorg-myaccount",
            warehouse="WH",
            database="DB",
            auth_mode="password",
            user_env="SNOWFLAKE_USER",
            password_env="SNOWFLAKE_PASSWORD",
        )
        with pytest.raises(AuthError, match="SNOWFLAKE_USER"):
            build_connection(cfg)

    def test_sso_mode_uses_externalbrowser(self, mock_snowflake_connector, monkeypatch):
        from scripts.connectors.snowflake import build_connection, SnowflakeConfig
        import scripts.connectors.snowflake as sf_mod
        monkeypatch.setenv("SNOWFLAKE_USER", "ssouser")
        sf_mod.snowflake = MagicMock()
        sf_mod.snowflake.connector = mock_snowflake_connector
        mock_snowflake_connector.connect.return_value = MagicMock()
        cfg = SnowflakeConfig(
            account="myorg-myaccount",
            warehouse="WH",
            database="DB",
            auth_mode="sso",
            user_env="SNOWFLAKE_USER",
            login_timeout=30,
        )
        build_connection(cfg)
        call_kwargs = mock_snowflake_connector.connect.call_args.kwargs
        assert call_kwargs.get("authenticator") == "externalbrowser"
        assert call_kwargs.get("login_timeout") == 30

    def test_oauth_mode_passes_token(self, mock_snowflake_connector, monkeypatch):
        from scripts.connectors.snowflake import build_connection, SnowflakeConfig
        import scripts.connectors.snowflake as sf_mod
        monkeypatch.setenv("SNOWFLAKE_USER", "oauthuser")
        monkeypatch.setenv("SF_OAUTH_TOKEN", "mytoken123")
        sf_mod.snowflake = MagicMock()
        sf_mod.snowflake.connector = mock_snowflake_connector
        mock_snowflake_connector.connect.return_value = MagicMock()
        cfg = SnowflakeConfig(
            account="myorg-myaccount",
            warehouse="WH",
            database="DB",
            auth_mode="oauth",
            user_env="SNOWFLAKE_USER",
            token_env="SF_OAUTH_TOKEN",
        )
        build_connection(cfg)
        call_kwargs = mock_snowflake_connector.connect.call_args.kwargs
        assert call_kwargs.get("authenticator") == "oauth"
        assert call_kwargs.get("token") == "mytoken123"

    def test_role_included_when_set(self, mock_snowflake_connector, monkeypatch):
        from scripts.connectors.snowflake import build_connection, SnowflakeConfig
        import scripts.connectors.snowflake as sf_mod
        monkeypatch.setenv("SNOWFLAKE_USER", "user")
        monkeypatch.setenv("SNOWFLAKE_PASSWORD", "pass")
        sf_mod.snowflake = MagicMock()
        sf_mod.snowflake.connector = mock_snowflake_connector
        mock_snowflake_connector.connect.return_value = MagicMock()
        cfg = SnowflakeConfig(
            account="myorg-myaccount",
            warehouse="WH",
            database="DB",
            auth_mode="password",
            user_env="SNOWFLAKE_USER",
            password_env="SNOWFLAKE_PASSWORD",
            role="ANALYST",
        )
        build_connection(cfg)
        call_kwargs = mock_snowflake_connector.connect.call_args.kwargs
        assert call_kwargs.get("role") == "ANALYST"

    def test_auth_failed_raises_auth_error(self, mock_snowflake_connector, monkeypatch):
        from scripts.connectors.snowflake import build_connection, SnowflakeConfig
        import scripts.connectors.snowflake as sf_mod
        monkeypatch.setenv("SNOWFLAKE_USER", "baduser")
        monkeypatch.setenv("SNOWFLAKE_PASSWORD", "badpass")
        sf_mod.snowflake = MagicMock()
        sf_mod.snowflake.connector = mock_snowflake_connector
        sf_mod.DatabaseError = mock_snowflake_connector.DatabaseError
        sf_mod.OperationalError = mock_snowflake_connector.OperationalError
        mock_snowflake_connector.connect.side_effect = mock_snowflake_connector.DatabaseError(
            "Incorrect username or password was specified"
        )
        cfg = SnowflakeConfig(
            account="myorg-myaccount",
            warehouse="WH",
            database="DB",
            auth_mode="password",
            user_env="SNOWFLAKE_USER",
            password_env="SNOWFLAKE_PASSWORD",
        )
        with pytest.raises(AuthError, match="authentication failed"):
            build_connection(cfg)

    def test_network_error_raises_network_error(self, mock_snowflake_connector, monkeypatch):
        from scripts.connectors.snowflake import build_connection, SnowflakeConfig
        import scripts.connectors.snowflake as sf_mod
        monkeypatch.setenv("SNOWFLAKE_USER", "user")
        monkeypatch.setenv("SNOWFLAKE_PASSWORD", "pass")
        sf_mod.snowflake = MagicMock()
        sf_mod.snowflake.connector = mock_snowflake_connector
        sf_mod.DatabaseError = mock_snowflake_connector.DatabaseError
        sf_mod.OperationalError = mock_snowflake_connector.OperationalError
        mock_snowflake_connector.connect.side_effect = mock_snowflake_connector.OperationalError(
            "Failed to connect to host: myorg-myaccount"
        )
        cfg = SnowflakeConfig(
            account="myorg-myaccount",
            warehouse="WH",
            database="DB",
            auth_mode="password",
            user_env="SNOWFLAKE_USER",
            password_env="SNOWFLAKE_PASSWORD",
        )
        with pytest.raises(NetworkError):
            build_connection(cfg)

    def test_connector_not_installed_raises_config_error(self, monkeypatch):
        import scripts.connectors.snowflake as sf_mod
        original = sf_mod.snowflake
        sf_mod.snowflake = None
        try:
            from scripts.connectors.snowflake import build_connection, SnowflakeConfig
            cfg = SnowflakeConfig(auth_mode="password")
            with pytest.raises(ConfigError, match="snowflake-connector-python"):
                build_connection(cfg)
        finally:
            sf_mod.snowflake = original


# ---------------------------------------------------------------------------
# SnowflakeRunner.execute_query tests
# ---------------------------------------------------------------------------


class TestExecuteQuery:
    def _make_runner(self, mock_snowflake_connector):
        from scripts.connectors.snowflake import SnowflakeRunner, SnowflakeConfig
        import scripts.connectors.snowflake as sf_mod
        sf_mod.DatabaseError = mock_snowflake_connector.DatabaseError
        sf_mod.ProgrammingError = mock_snowflake_connector.ProgrammingError
        cfg = SnowflakeConfig(fetch_size=1000)
        conn, cursor = _mock_connection()
        runner = SnowflakeRunner(cfg, conn)
        return runner, conn, cursor

    def test_returns_columns_and_rows(self, mock_snowflake_connector):
        runner, conn, cursor = self._make_runner(mock_snowflake_connector)
        cursor.description = [
            ("ID", None, None, None, None, None, None),
            ("NAME", None, None, None, None, None, None),
        ]
        cursor.fetchmany.side_effect = [[(1, "Alice"), (2, "Bob")], []]
        cols, rows = runner.execute_query("SELECT id, name FROM users", row_limit=0)
        assert cols == ["id", "name"]
        assert rows == [["1", "Alice"], ["2", "Bob"]]

    def test_row_limit_stops_early(self, mock_snowflake_connector):
        runner, conn, cursor = self._make_runner(mock_snowflake_connector)
        cursor.description = [("N", None, None, None, None, None, None)]
        cursor.fetchmany.side_effect = [
            [(i,) for i in range(10)],
            [],
        ]
        _, rows = runner.execute_query("SELECT n FROM t", row_limit=3)
        assert len(rows) == 3

    def test_none_values_become_empty_string(self, mock_snowflake_connector):
        runner, conn, cursor = self._make_runner(mock_snowflake_connector)
        cursor.description = [("VAL", None, None, None, None, None, None)]
        cursor.fetchmany.side_effect = [[(None,)], []]
        _, rows = runner.execute_query("SELECT val FROM t", row_limit=0)
        assert rows == [[""]]

    def test_column_names_lowercased(self, mock_snowflake_connector):
        runner, conn, cursor = self._make_runner(mock_snowflake_connector)
        cursor.description = [("ACCOUNT_ID", None, None, None, None, None, None)]
        cursor.fetchmany.side_effect = [[("42",)], []]
        cols, _ = runner.execute_query("SELECT account_id FROM accounts", row_limit=0)
        assert cols == ["account_id"]


# ---------------------------------------------------------------------------
# SnowflakeRunner.run_explain tests
# ---------------------------------------------------------------------------


class TestRunExplain:
    def _make_runner(self, mock_snowflake_connector):
        from scripts.connectors.snowflake import SnowflakeRunner, SnowflakeConfig
        import scripts.connectors.snowflake as sf_mod
        sf_mod.DatabaseError = mock_snowflake_connector.DatabaseError
        sf_mod.ProgrammingError = mock_snowflake_connector.ProgrammingError
        cfg = SnowflakeConfig(fetch_size=1000)
        conn, cursor = _mock_connection()
        runner = SnowflakeRunner(cfg, conn)
        return runner, conn, cursor

    def test_returns_plan_and_bytes_and_partitions(self, mock_snowflake_connector):
        runner, conn, cursor = self._make_runner(mock_snowflake_connector)
        # EXPLAIN USING TABULAR returns columnar rows
        # columns: step, id, operation, objects, partitionsTotal, bytesAssigned
        cursor.description = [
            ("step", None, None, None, None, None, None),
            ("id", None, None, None, None, None, None),
            ("operation", None, None, None, None, None, None),
            ("objects", None, None, None, None, None, None),
            ("partitionsTotal", None, None, None, None, None, None),
            ("bytesAssigned", None, None, None, None, None, None),
        ]
        cursor.fetchall.return_value = [
            (1, 0, "Result", None, 50, 536870912),
            (1, 1, "TableScan", "ORDERS", 50, 536870912),
        ]
        plan_text, estimated_bytes, partitions_total = runner.run_explain("SELECT * FROM orders")
        assert estimated_bytes == 536870912 + 536870912  # sum
        assert partitions_total == 50  # max
        assert isinstance(plan_text, str)

    def test_none_bytes_when_column_absent(self, mock_snowflake_connector):
        runner, conn, cursor = self._make_runner(mock_snowflake_connector)
        cursor.description = [
            ("step", None, None, None, None, None, None),
            ("operation", None, None, None, None, None, None),
        ]
        cursor.fetchall.return_value = [(1, "TableScan")]
        plan_text, estimated_bytes, partitions_total = runner.run_explain("SELECT 1")
        assert estimated_bytes is None
        assert partitions_total is None

    def test_handles_null_bytes_in_rows(self, mock_snowflake_connector):
        runner, conn, cursor = self._make_runner(mock_snowflake_connector)
        cursor.description = [
            ("partitionsTotal", None, None, None, None, None, None),
            ("bytesAssigned", None, None, None, None, None, None),
        ]
        cursor.fetchall.return_value = [
            (None, None),
            (10, 1073741824),
        ]
        _, estimated_bytes, partitions_total = runner.run_explain("SELECT 1")
        assert estimated_bytes == 1073741824  # None rows skipped
        assert partitions_total == 10

    def test_plan_text_is_csv_formatted(self, mock_snowflake_connector):
        runner, conn, cursor = self._make_runner(mock_snowflake_connector)
        cursor.description = [
            ("operation", None, None, None, None, None, None),
            ("bytesAssigned", None, None, None, None, None, None),
        ]
        cursor.fetchall.return_value = [("TableScan", 1000000)]
        plan_text, _, _ = runner.run_explain("SELECT 1")
        # CSV format: header row present (column names are lowercased)
        assert "operation" in plan_text
        assert "bytesassigned" in plan_text


# ---------------------------------------------------------------------------
# format_results (from common) — Snowflake-specific value types
# ---------------------------------------------------------------------------


class TestFormatResultsWithSnowflakeValues:
    def test_csv_with_semi_structured_string(self):
        cols = ["variant_col"]
        rows = [['{"key": "value"}']]
        output = format_results(cols, rows, fmt="csv")
        assert "variant_col" in output

    def test_json_output(self):
        import json
        cols = ["account", "region"]
        rows = [["myorg-myaccount", "us-east-1"]]
        output = format_results(cols, rows, fmt="json")
        data = json.loads(output)
        assert data[0] == {"account": "myorg-myaccount", "region": "us-east-1"}

    def test_empty_string_for_null(self):
        cols = ["comment"]
        rows = [[""], ["some comment"]]
        output = format_results(cols, rows, fmt="csv")
        lines = output.strip().splitlines()
        assert lines[1] in ("", '""')
