"""
Unit tests for scripts/connectors/athena.py.

All tests use mocked boto3 — no AWS credentials or live Athena connection needed.
Run with: python -m pytest scripts/connectors/tests/ -v
"""

import io
import os
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import yaml

from scripts.connectors.athena import (
    AthenaConfig,
    AthenaRunner,
    _to_bytes,
    build_boto3_session,
    extract_bytes_from_explain,
    load_config,
)
from scripts.connectors.common import (
    AuthError,
    ConfigError,
    NetworkError,
    QueryError,
    TimeoutError,
    format_results,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "active.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _minimal_athena_yaml(auth_mode: str = "env", **athena_overrides) -> dict:
    athena_block = {
        "region": "us-east-1",
        "database": "test_db",
        "s3_output_location": "s3://test-bucket/results/",
        "workgroup": "primary",
        "aws": {"auth": auth_mode},
        **athena_overrides,
    }
    return {
        "active": "test",
        "connections": [
            {
                "name": "test",
                "type": "athena",
                "display_name": "Test Athena",
                "read_only": True,
                "transport": "direct",
                "athena": athena_block,
            }
        ],
    }


def _mock_runner(cfg: AthenaConfig | None = None) -> tuple[AthenaRunner, MagicMock]:
    """Return (runner, mock_athena_client)."""
    if cfg is None:
        cfg = AthenaConfig(
            region="us-east-1",
            database="test_db",
            s3_output_location="s3://bucket/results/",
        )
    mock_client = MagicMock()
    session = MagicMock()
    session.client.return_value = mock_client
    runner = AthenaRunner(cfg, session)
    return runner, mock_client


def _make_results_page(columns: list[str], rows: list[list[str]], next_token: str | None = None) -> dict:
    """Build a get_query_results response dict."""
    col_info = [{"Label": c, "Name": c} for c in columns]
    # First page includes a header row
    header_row = {"Data": [{"VarCharValue": c} for c in columns]}
    data_rows = [{"Data": [{"VarCharValue": v} for v in row]} for row in rows]
    result = {
        "ResultSet": {
            "ResultSetMetadata": {"ColumnInfo": col_info},
            "Rows": [header_row] + data_rows,
        }
    }
    if next_token:
        result["NextToken"] = next_token
    return result


def _make_results_continuation(rows: list[list[str]], next_token: str | None = None) -> dict:
    """Build a continuation page (no header row, no ColumnInfo needed here)."""
    data_rows = [{"Data": [{"VarCharValue": v} for v in row]} for row in rows]
    result = {
        "ResultSet": {
            "ResultSetMetadata": {"ColumnInfo": []},
            "Rows": data_rows,
        }
    }
    if next_token:
        result["NextToken"] = next_token
    return result


# ---------------------------------------------------------------------------
# load_config tests
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_missing_file_raises_config_error(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.yaml")

    def test_bad_yaml_raises_config_error(self, tmp_path):
        p = tmp_path / "active.yaml"
        p.write_text("active: [unclosed", encoding="utf-8")
        with pytest.raises(ConfigError, match="parse"):
            load_config(p)

    def test_missing_active_field_raises_config_error(self, tmp_path):
        p = _write_yaml(tmp_path, {"connections": []})
        with pytest.raises(ConfigError, match="active"):
            load_config(p)

    def test_active_name_not_found_raises_config_error(self, tmp_path):
        p = _write_yaml(tmp_path, {"active": "missing", "connections": []})
        with pytest.raises(ConfigError, match="not found"):
            load_config(p)

    def test_wrong_type_raises_config_error(self, tmp_path):
        data = {
            "active": "myconn",
            "connections": [{"name": "myconn", "type": "sqlite"}],
        }
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="sqlite"):
            load_config(p)

    def test_missing_region_raises_config_error(self, tmp_path):
        data = _minimal_athena_yaml()
        del data["connections"][0]["athena"]["region"]
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="region"):
            load_config(p)

    def test_missing_database_raises_config_error(self, tmp_path):
        data = _minimal_athena_yaml()
        del data["connections"][0]["athena"]["database"]
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="database"):
            load_config(p)

    def test_missing_s3_output_raises_config_error(self, tmp_path):
        data = _minimal_athena_yaml()
        del data["connections"][0]["athena"]["s3_output_location"]
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="s3_output_location"):
            load_config(p)

    def test_unknown_auth_mode_raises_config_error(self, tmp_path):
        data = _minimal_athena_yaml()
        data["connections"][0]["athena"]["aws"]["auth"] = "magic"
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="Unknown auth mode"):
            load_config(p)

    def test_valid_env_config(self, tmp_path):
        p = _write_yaml(tmp_path, _minimal_athena_yaml(auth_mode="env"))
        cfg = load_config(p)
        assert cfg.region == "us-east-1"
        assert cfg.database == "test_db"
        assert cfg.s3_output_location == "s3://test-bucket/results/"
        assert cfg.workgroup == "primary"
        assert cfg.auth_mode == "env"
        assert cfg.timeout_seconds == 300

    def test_valid_profile_config(self, tmp_path):
        data = _minimal_athena_yaml(auth_mode="profile")
        data["connections"][0]["athena"]["aws"]["profile_name"] = "my-sso"
        p = _write_yaml(tmp_path, data)
        cfg = load_config(p)
        assert cfg.auth_mode == "profile"
        assert cfg.profile_name == "my-sso"

    def test_valid_keys_config(self, tmp_path):
        data = _minimal_athena_yaml(auth_mode="keys")
        data["connections"][0]["athena"]["aws"]["access_key_id_env"] = "MY_KEY_ID"
        data["connections"][0]["athena"]["aws"]["secret_access_key_env"] = "MY_SECRET"
        p = _write_yaml(tmp_path, data)
        cfg = load_config(p)
        assert cfg.auth_mode == "keys"
        assert cfg.access_key_id_env == "MY_KEY_ID"
        assert cfg.secret_access_key_env == "MY_SECRET"

    def test_custom_timeout(self, tmp_path):
        data = _minimal_athena_yaml()
        data["connections"][0]["athena"]["timeout_seconds"] = 600
        p = _write_yaml(tmp_path, data)
        cfg = load_config(p)
        assert cfg.timeout_seconds == 600


# ---------------------------------------------------------------------------
# build_boto3_session tests
# ---------------------------------------------------------------------------


class TestBuildBoto3Session:
    def test_env_mode_creates_session_with_region(self):
        cfg = AthenaConfig(
            region="eu-west-1",
            database="db",
            s3_output_location="s3://b/r/",
            auth_mode="env",
        )
        with patch("scripts.connectors.athena.boto3.Session") as mock_session_cls:
            mock_session_cls.return_value = MagicMock()
            build_boto3_session(cfg)
            mock_session_cls.assert_called_once_with(region_name="eu-west-1")

    def test_profile_mode_calls_with_profile_name(self):
        cfg = AthenaConfig(
            region="us-east-1",
            database="db",
            s3_output_location="s3://b/r/",
            auth_mode="profile",
            profile_name="my-profile",
        )
        mock_session = MagicMock()
        mock_creds = MagicMock()
        mock_session.get_credentials.return_value = mock_creds

        with patch("scripts.connectors.athena.boto3.Session", return_value=mock_session):
            build_boto3_session(cfg)
            import boto3
            boto3.Session  # just ensure import works

        mock_session.get_credentials.assert_called_once()
        mock_creds.get_frozen_credentials.assert_called_once()

    def test_profile_mode_missing_profile_name_raises_config_error(self):
        cfg = AthenaConfig(
            region="us-east-1",
            database="db",
            s3_output_location="s3://b/r/",
            auth_mode="profile",
            profile_name=None,
        )
        with pytest.raises(ConfigError, match="profile_name"):
            build_boto3_session(cfg)

    def test_profile_mode_sso_expired_raises_auth_error_with_login_hint(self):
        cfg = AthenaConfig(
            region="us-east-1",
            database="db",
            s3_output_location="s3://b/r/",
            auth_mode="profile",
            profile_name="sso-profile",
        )

        class FakeSSOTokenLoadError(Exception):
            pass

        FakeSSOTokenLoadError.__name__ = "SSOTokenLoadError"

        mock_session = MagicMock()
        mock_creds = MagicMock()
        mock_creds.get_frozen_credentials.side_effect = FakeSSOTokenLoadError("expired")
        mock_session.get_credentials.return_value = mock_creds

        with patch("scripts.connectors.athena.boto3.Session", return_value=mock_session):
            with pytest.raises(AuthError) as exc_info:
                build_boto3_session(cfg)

        assert "aws sso login --profile sso-profile" in str(exc_info.value)

    def test_profile_mode_profile_not_found_raises_auth_error(self):
        cfg = AthenaConfig(
            region="us-east-1",
            database="db",
            s3_output_location="s3://b/r/",
            auth_mode="profile",
            profile_name="nonexistent",
        )
        import botocore.exceptions

        with patch("scripts.connectors.athena.boto3.Session") as mock_session_cls:
            mock_session_cls.side_effect = botocore.exceptions.ProfileNotFound(profile="nonexistent")
            with pytest.raises(AuthError, match="nonexistent"):
                build_boto3_session(cfg)

    def test_keys_mode_reads_env_vars(self, monkeypatch):
        cfg = AthenaConfig(
            region="us-east-1",
            database="db",
            s3_output_location="s3://b/r/",
            auth_mode="keys",
            access_key_id_env="MY_KEY_ID",
            secret_access_key_env="MY_SECRET",
        )
        monkeypatch.setenv("MY_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
        monkeypatch.setenv("MY_SECRET", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")

        with patch("scripts.connectors.athena.boto3.Session") as mock_session_cls:
            mock_session_cls.return_value = MagicMock()
            build_boto3_session(cfg)
            mock_session_cls.assert_called_once_with(
                aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
                aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                region_name="us-east-1",
            )

    def test_keys_mode_missing_key_id_raises_auth_error(self, monkeypatch):
        cfg = AthenaConfig(
            region="us-east-1",
            database="db",
            s3_output_location="s3://b/r/",
            auth_mode="keys",
            access_key_id_env="MY_KEY_ID",
            secret_access_key_env="MY_SECRET",
        )
        monkeypatch.delenv("MY_KEY_ID", raising=False)
        monkeypatch.delenv("MY_SECRET", raising=False)
        with pytest.raises(AuthError, match="MY_KEY_ID"):
            build_boto3_session(cfg)

    def test_keys_mode_missing_secret_raises_auth_error(self, monkeypatch):
        cfg = AthenaConfig(
            region="us-east-1",
            database="db",
            s3_output_location="s3://b/r/",
            auth_mode="keys",
            access_key_id_env="MY_KEY_ID",
            secret_access_key_env="MY_SECRET",
        )
        monkeypatch.setenv("MY_KEY_ID", "somekey")
        monkeypatch.delenv("MY_SECRET", raising=False)
        with pytest.raises(AuthError, match="MY_SECRET"):
            build_boto3_session(cfg)


# ---------------------------------------------------------------------------
# AthenaRunner.poll_until_terminal tests
# ---------------------------------------------------------------------------


def _execution_response(state: str, reason: str = "") -> dict:
    status = {"State": state}
    if reason:
        status["StateChangeReason"] = reason
    return {"QueryExecution": {"Status": status}}


class TestPollUntilTerminal:
    def test_succeeds_after_two_running_states(self):
        runner, client = _mock_runner()
        client.get_query_execution.side_effect = [
            _execution_response("RUNNING"),
            _execution_response("RUNNING"),
            _execution_response("SUCCEEDED"),
        ]
        with patch("scripts.connectors.athena.time.sleep"):
            result = runner.poll_until_terminal("exec-123")
        assert result["Status"]["State"] == "SUCCEEDED"
        assert client.get_query_execution.call_count == 3

    def test_failed_state_raises_query_error(self):
        runner, client = _mock_runner()
        client.get_query_execution.return_value = _execution_response(
            "FAILED", "Table not found"
        )
        with patch("scripts.connectors.athena.time.sleep"):
            with pytest.raises(QueryError, match="Table not found"):
                runner.poll_until_terminal("exec-456")

    def test_cancelled_state_raises_query_error(self):
        runner, client = _mock_runner()
        client.get_query_execution.return_value = _execution_response("CANCELLED")
        with patch("scripts.connectors.athena.time.sleep"):
            with pytest.raises(QueryError, match="cancelled"):
                runner.poll_until_terminal("exec-789")

    def test_timeout_calls_cancel_and_raises_timeout_error(self):
        cfg = AthenaConfig(
            region="us-east-1",
            database="db",
            s3_output_location="s3://b/r/",
            timeout_seconds=1,
        )
        runner, client = _mock_runner(cfg)
        client.get_query_execution.return_value = _execution_response("RUNNING")

        # Make time.monotonic advance quickly past the deadline
        start = time.monotonic()
        call_count = [0]

        def fast_monotonic():
            call_count[0] += 1
            # First call (deadline set): return real time
            # Subsequent calls: return time past deadline
            if call_count[0] <= 1:
                return start
            return start + 10  # well past the 1s timeout

        with patch("scripts.connectors.athena.time.monotonic", side_effect=fast_monotonic):
            with patch("scripts.connectors.athena.time.sleep"):
                with pytest.raises(TimeoutError):
                    runner.poll_until_terminal("exec-timeout")

        client.stop_query_execution.assert_called_once_with(QueryExecutionId="exec-timeout")


# ---------------------------------------------------------------------------
# AthenaRunner.fetch_results_paginated tests
# ---------------------------------------------------------------------------


class TestFetchResultsPaginated:
    def test_single_page_returns_correct_columns_and_rows(self):
        runner, client = _mock_runner()
        client.get_query_results.return_value = _make_results_page(
            ["id", "name"],
            [["1", "Alice"], ["2", "Bob"]],
        )
        columns, rows = runner.fetch_results_paginated("exec-1", row_limit=0)
        assert columns == ["id", "name"]
        assert rows == [["1", "Alice"], ["2", "Bob"]]

    def test_row_limit_stops_early(self):
        runner, client = _mock_runner()
        client.get_query_results.return_value = _make_results_page(
            ["id"],
            [["1"], ["2"], ["3"], ["4"], ["5"]],
        )
        _, rows = runner.fetch_results_paginated("exec-1", row_limit=3)
        assert len(rows) == 3

    def test_pagination_fetches_multiple_pages(self):
        runner, client = _mock_runner()
        client.get_query_results.side_effect = [
            _make_results_page(["x"], [["a"], ["b"]], next_token="tok1"),
            _make_results_continuation([["c"], ["d"]]),
        ]
        _, rows = runner.fetch_results_paginated("exec-1", row_limit=0)
        assert [r[0] for r in rows] == ["a", "b", "c", "d"]
        assert client.get_query_results.call_count == 2


# ---------------------------------------------------------------------------
# format_results tests
# ---------------------------------------------------------------------------


class TestFormatResults:
    def test_csv_with_header(self):
        output = format_results(["a", "b"], [["1", "2"], ["3", "4"]], fmt="csv")
        lines = output.strip().splitlines()
        assert lines[0] == "a,b"
        assert lines[1] == "1,2"
        assert lines[2] == "3,4"

    def test_csv_without_header(self):
        output = format_results(
            ["a", "b"], [["1", "2"]], fmt="csv", include_header=False
        )
        assert "a,b" not in output
        assert "1,2" in output

    def test_json_output(self):
        import json

        output = format_results(["x", "y"], [["hello", "world"]], fmt="json")
        data = json.loads(output)
        assert data == [{"x": "hello", "y": "world"}]

    def test_truncation_appends_notice_csv(self):
        rows = [[str(i)] for i in range(10)]
        output = format_results(["n"], rows, fmt="csv", row_limit=5)
        assert "truncated at 5 rows" in output
        # Only 5 data rows + header
        data_lines = [l for l in output.splitlines() if l and not l.startswith("#")]
        assert len(data_lines) == 6  # 1 header + 5 data

    def test_truncation_appends_notice_json(self):
        rows = [[str(i)] for i in range(10)]
        output = format_results(["n"], rows, fmt="json", row_limit=5)
        assert "truncated at 5 rows" in output

    def test_no_truncation_when_limit_zero(self):
        rows = [[str(i)] for i in range(20)]
        output = format_results(["n"], rows, fmt="csv", row_limit=0)
        assert "truncated" not in output


# ---------------------------------------------------------------------------
# extract_bytes_from_explain tests
# ---------------------------------------------------------------------------


class TestExtractBytesFromExplain:
    def test_parses_size_gb(self):
        text = "Fragment 1 [...]  rows = 1000, size = 2.5 GB"
        result = extract_bytes_from_explain(text)
        assert result == int(2.5 * 1024**3)

    def test_parses_size_mb(self):
        text = "size = 512 MB of data"
        result = extract_bytes_from_explain(text)
        assert result == 512 * 1024**2

    def test_parses_size_kb(self):
        text = "size = 100 KB"
        result = extract_bytes_from_explain(text)
        assert result == 100 * 1024

    def test_parses_data_size_numeric(self):
        text = "dataSize=1073741824 rows=5000"
        result = extract_bytes_from_explain(text)
        assert result == 1073741824

    def test_returns_none_for_unparseable(self):
        text = "- Output[col1] => [col1:varchar]\n   Cost: ?  Estimate: unknown"
        result = extract_bytes_from_explain(text)
        assert result is None

    def test_returns_none_for_empty_string(self):
        assert extract_bytes_from_explain("") is None

    def test_parses_tb(self):
        text = "size = 1.5 TB"
        result = extract_bytes_from_explain(text)
        assert result == int(1.5 * 1024**4)


# ---------------------------------------------------------------------------
# _to_bytes helper
# ---------------------------------------------------------------------------


class TestToBytes:
    def test_bytes(self):
        assert _to_bytes(500, "B") == 500

    def test_kb(self):
        assert _to_bytes(1, "KB") == 1024

    def test_gb(self):
        assert _to_bytes(1, "GB") == 1024**3

    def test_unknown_unit(self):
        assert _to_bytes(100, "ZB") == 100  # falls back to *1
