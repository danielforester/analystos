"""
Salesforce connectivity core for AnalystOS.

Uses simple-salesforce for SOQL execution and Salesforce REST API calls.
Supports five auth modes:
  - sf_cli       — Reuse an existing Salesforce CLI session (recommended, no setup required).
                   Run `sf org login web` once; this mode reads the stored token from
                   ~/.sfdx/{username}.json and auto-refreshes when expired.
  - playwright   — Spawn a headed browser for interactive login (SSO-safe, no Connected App).
                   Navigates to instance_url, waits for the user to complete login (including
                   SSO), captures the session token from cookies, and caches it for ~2 hours.
                   Requires: pip install playwright && playwright install chromium
  - oauth_web    — Browser-based OAuth 2.0 Authorization Code flow.
                   Requires a Connected App with http://localhost:{port}/callback registered.
                   Spawns browser, traps callback, caches token + refresh token.
  - password     — Username + password + security token. No Connected App required, but
                   does not work for orgs that enforce SSO for all users.
  - access_token — Pre-obtained Bearer token. Works with any auth method; grab the token
                   from Salesforce Inspector (browser extension), the `sid` cookie in
                   DevTools, or Workbench → Info → Session Information.
                   Supports direct value, env var, or interactive prompt (prompt: true).
                   Typical lifetime: 2 hours (org-configurable).

Token cache (playwright and oauth_web) is stored next to active.yaml as .sf_token_cache.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

try:
    from simple_salesforce import Salesforce
    from simple_salesforce.exceptions import SalesforceAuthenticationFailed, SalesforceError
except ImportError:
    Salesforce = None  # type: ignore[assignment,misc]
    SalesforceAuthenticationFailed = Exception  # type: ignore[assignment,misc]
    SalesforceError = Exception  # type: ignore[assignment,misc]

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
    "SalesforceConfig",
    "SalesforceRunner",
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

DEFAULT_API_VERSION = "62.0"
DEFAULT_REDIRECT_PORT = 51778
OAUTH_TIMEOUT_SECONDS = 120        # max seconds to wait for browser auth callback
TOKEN_LIFETIME_SECONDS = 7200      # conservative access-token lifetime (2 hours)
TOKEN_EXPIRY_BUFFER_SECONDS = 300  # refresh 5 min before expiry

LOGIN_URL_PROD = "https://login.salesforce.com"
LOGIN_URL_SANDBOX = "https://test.salesforce.com"


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class SalesforceConfig:
    # Common
    api_version: str = DEFAULT_API_VERSION
    auth_mode: str = "sf_cli"  # sf_cli | oauth_web | password | access_token
    sandbox: bool = False

    # sf_cli mode — reads from ~/.sfdx/{username}.json
    sf_cli_username: str = ""   # leave blank to auto-detect the default org

    # playwright mode
    playwright_timeout: int = 120   # seconds to wait for login to complete

    # oauth_web mode — direct values (preferred) or env var names
    client_id: str = ""
    client_id_env: str = ""
    client_secret: str = ""
    client_secret_env: str = ""
    redirect_port: int = DEFAULT_REDIRECT_PORT

    # password mode — direct values (preferred) or env var names
    username: str = ""
    username_env: str = ""
    password: str = ""          # include security token appended, e.g. "mypassABC123TOKEN"
    password_env: str = ""

    # access_token mode — direct value, env var, or interactive prompt
    # Set prompt: true to be asked to paste the token at session start (good for short-lived tokens)
    access_token: str = ""
    access_token_env: str = ""
    instance_url: str = ""
    instance_url_env: str = ""
    prompt: bool = False        # if true and access_token is unset, prompt interactively

    # Set by load_config; used to locate the token cache file
    config_dir: Optional[Path] = None

    @property
    def login_url(self) -> str:
        return LOGIN_URL_SANDBOX if self.sandbox else LOGIN_URL_PROD

    @property
    def api_version_clean(self) -> str:
        """API version string without a leading 'v', e.g. '62.0' not 'v62.0'."""
        return self.api_version.lstrip("v")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_config(config_path: Path) -> SalesforceConfig:
    """Load and validate the active Salesforce connection from active.yaml."""
    raw = load_yaml_config(config_path)
    conn = find_active_connection(raw, config_path)

    conn_name = conn.get("name", "")
    conn_type = conn.get("type", "")
    if conn_type != "salesforce":
        raise ConfigError(
            f"Active connection '{conn_name}' has type '{conn_type}', expected 'salesforce'.\n"
            "Switch to a Salesforce connection with /db-use or update active.yaml."
        )

    sf = conn.get("salesforce") or {}
    auth_mode = sf.get("auth_mode", "sf_cli")

    if auth_mode not in ("sf_cli", "playwright", "oauth_web", "password", "access_token"):
        raise ConfigError(
            f"Unknown Salesforce auth_mode '{auth_mode}'. "
            "Must be: sf_cli, playwright, oauth_web, password, or access_token."
        )

    # sf_cli: no required fields — username is optional (auto-detects default org)
    if auth_mode == "playwright":
        _require_any(sf, [("instance_url", "instance_url_env")], conn_name, config_path)
    elif auth_mode == "oauth_web":
        _require_any(sf, [("client_id", "client_id_env"), ("client_secret", "client_secret_env")],
                     conn_name, config_path)
    elif auth_mode == "password":
        _require_any(sf, [("username", "username_env"), ("password", "password_env")],
                     conn_name, config_path)
    elif auth_mode == "access_token":
        _require_any(sf, [("instance_url", "instance_url_env")], conn_name, config_path)
        # access_token itself may be absent if prompt: true

    return SalesforceConfig(
        api_version=str(sf.get("api_version", DEFAULT_API_VERSION)),
        auth_mode=auth_mode,
        sandbox=bool(sf.get("sandbox", False)),
        sf_cli_username=sf.get("sf_cli_username", ""),
        playwright_timeout=int(sf.get("playwright_timeout", 120)),
        client_id=sf.get("client_id", ""),
        client_id_env=sf.get("client_id_env", ""),
        client_secret=sf.get("client_secret", ""),
        client_secret_env=sf.get("client_secret_env", ""),
        redirect_port=int(sf.get("redirect_port", DEFAULT_REDIRECT_PORT)),
        username=sf.get("username", ""),
        username_env=sf.get("username_env", ""),
        password=sf.get("password", ""),
        password_env=sf.get("password_env", ""),
        access_token=sf.get("access_token", ""),
        access_token_env=sf.get("access_token_env", ""),
        instance_url=sf.get("instance_url", ""),
        instance_url_env=sf.get("instance_url_env", ""),
        prompt=bool(sf.get("prompt", False)),
        config_dir=config_path.parent,
    )


def _require_any(
    block: dict,
    pairs: list[tuple[str, str]],
    conn_name: str,
    config_path: Path,
) -> None:
    """
    Validate that for each (direct_key, env_key) pair, at least one is set.

    E.g. _require_any(sf, [("username", "username_env"), ("password", "password_env")], ...)
    raises ConfigError if both username and username_env are absent.
    """
    missing_pairs = [
        f"'{direct}' or '{env}'"
        for direct, env in pairs
        if not block.get(direct) and not block.get(env)
    ]
    if missing_pairs:
        raise ConfigError(
            f"Salesforce connection '{conn_name}' requires {', '.join(missing_pairs)}.\n"
            f"Set the value directly in the 'salesforce:' block in {config_path}, "
            "or point to an environment variable via the _env variant."
        )


# ---------------------------------------------------------------------------
# Connection construction
# ---------------------------------------------------------------------------


def _ensure_simple_salesforce() -> None:
    if Salesforce is None:
        raise ConfigError(
            "simple-salesforce is not installed.\n"
            "Install it with: pip install simple-salesforce"
        )


def build_connection(cfg: SalesforceConfig) -> "Salesforce":
    """
    Build a Salesforce connection from SalesforceConfig.

    Returns a simple_salesforce.Salesforce instance ready for SOQL queries and
    REST API calls.

    Auth modes:
      sf_cli       — Reuse Salesforce CLI session from ~/.sfdx/; auto-refreshes
      oauth_web    — Browser OAuth flow with token caching and refresh
      password     — Username + password + security token
      access_token — Pre-obtained Bearer token from env var
    """
    _ensure_simple_salesforce()

    try:
        if cfg.auth_mode == "sf_cli":
            return _connect_sf_cli(cfg)
        elif cfg.auth_mode == "playwright":
            return _connect_playwright(cfg)
        elif cfg.auth_mode == "oauth_web":
            return _connect_oauth_web(cfg)
        elif cfg.auth_mode == "password":
            return _connect_password(cfg)
        else:
            return _connect_access_token(cfg)
    except (AuthError, ConfigError, NetworkError):
        raise
    except SalesforceAuthenticationFailed as exc:
        raise AuthError(f"Salesforce authentication failed: {exc}") from exc
    except Exception as exc:
        msg = str(exc)
        if any(kw in msg.lower() for kw in ("connect", "timeout", "network", "socket", "dns")):
            raise NetworkError(f"Could not reach Salesforce: {msg}") from exc
        raise AuthError(f"Salesforce connection error: {msg}") from exc


# ---------------------------------------------------------------------------
# Auth mode: sf_cli
# ---------------------------------------------------------------------------


def _connect_sf_cli(cfg: SalesforceConfig) -> "Salesforce":
    """
    Reuse an existing Salesforce CLI (sf / sfdx) session.

    Reads the credential file at ~/.sfdx/{username}.json.  If sf_cli_username
    is blank, auto-detects the default org (isDefaultUsername: true) or the
    most-recently-modified credential file.

    Tries the stored accessToken first.  If it has aged past TOKEN_LIFETIME_SECONDS
    and a refreshToken + clientId are available, silently refreshes before use.
    """
    cred = _find_sf_cli_credential(cfg.sf_cli_username)
    if cred is None:
        hint = (
            f"for username '{cfg.sf_cli_username}' " if cfg.sf_cli_username else ""
        )
        raise AuthError(
            f"No Salesforce CLI credential found {hint}in ~/.sfdx/.\n"
            "Run: sf org login web\n"
            "Then retry, or set sf_cli_username in active.yaml if you have multiple orgs."
        )

    access_token = cred.get("accessToken", "")
    refresh_token = cred.get("refreshToken", "")
    instance_url = cred.get("instanceUrl", "")
    client_id = cred.get("clientId", "")
    cred_file = cred.get("_source_path")  # injected by _find_sf_cli_credential

    if not access_token or not instance_url:
        raise AuthError(
            f"Salesforce CLI credential file is missing accessToken or instanceUrl.\n"
            "Try re-authenticating: sf org login web"
        )

    # Attempt a silent refresh if the token looks stale and we have what we need
    if _sf_cli_token_needs_refresh(cred) and refresh_token and client_id:
        print("[salesforce_connect] Refreshing Salesforce CLI token…", file=sys.stderr)
        try:
            login_url = cred.get("loginUrl", LOGIN_URL_PROD)
            new_data = _refresh_token(refresh_token, client_id, "", login_url)
            access_token = new_data["access_token"]
            instance_url = new_data.get("instance_url", instance_url)
            # Write the new token back so the next call is also fast
            if cred_file:
                _update_sf_cli_credential(Path(cred_file), access_token, instance_url)
            print("[salesforce_connect] Token refreshed.", file=sys.stderr)
        except (AuthError, NetworkError):
            # Refresh failed — try the existing token anyway; Salesforce will 401 if it's dead
            pass

    username = cred.get("username", "")
    if username:
        print(f"[salesforce_connect] Using Salesforce CLI session for {username}.", file=sys.stderr)

    return Salesforce(
        instance_url=instance_url,
        session_id=access_token,
        version=cfg.api_version_clean,
    )


def _sf_cli_paths() -> list[Path]:
    """Return candidate directories for Salesforce CLI credential files, most-preferred first."""
    home = Path.home()
    candidates = [
        # sfdx v1 and sf v2 both write here by default
        home / ".sfdx",
        # sf v2 may also use SFDX_HOME or SF_HOME overrides
        Path(os.environ["SFDX_HOME"]) if "SFDX_HOME" in os.environ else None,
        Path(os.environ["SF_HOME"]) if "SF_HOME" in os.environ else None,
    ]
    return [p for p in candidates if p and p.exists()]


def _find_sf_cli_credential(username: str) -> Optional[dict]:
    """
    Find and return a parsed Salesforce CLI credential dict.

    Injects a '_source_path' key with the file path so callers can write
    back a refreshed token.

    Search order:
      1. If username is given: look for {username}.json in each candidate dir.
      2. Otherwise: prefer the file marked isDefaultUsername=true, then fall
         back to the most-recently-modified .json file that looks like a cred.
    """
    for directory in _sf_cli_paths():
        if username:
            candidate = directory / f"{username}.json"
            if candidate.exists():
                try:
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                    data["_source_path"] = str(candidate)
                    return data
                except (json.JSONDecodeError, OSError):
                    continue
        else:
            # Collect all credential files (skip alias files and config files)
            files = sorted(
                [f for f in directory.glob("*.json") if "@" in f.stem or _looks_like_cred(f)],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            # Prefer the default org
            for f in files:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if data.get("isDefaultUsername") and "accessToken" in data:
                        data["_source_path"] = str(f)
                        return data
                except (json.JSONDecodeError, OSError):
                    continue
            # Fall back to most-recently-modified valid cred
            for f in files:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if "accessToken" in data and "instanceUrl" in data:
                        data["_source_path"] = str(f)
                        return data
                except (json.JSONDecodeError, OSError):
                    continue

    return None


def _looks_like_cred(path: Path) -> bool:
    """Quick heuristic: does the file look like a Salesforce credential (not a config file)?"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return "accessToken" in data
    except Exception:
        return False


def _sf_cli_token_needs_refresh(cred: dict) -> bool:
    """
    Estimate whether the stored access token is likely expired.

    The SF CLI stores 'created' as a Unix timestamp (ms) when the token was issued.
    We treat tokens older than TOKEN_LIFETIME_SECONDS as needing a refresh.
    """
    created_raw = cred.get("created")
    if created_raw is None:
        return False  # no timestamp — optimistically try the existing token
    try:
        created_s = float(created_raw)
        # Salesforce stores this in milliseconds
        if created_s > 1e10:
            created_s /= 1000
        age = time.time() - created_s
        return age > (TOKEN_LIFETIME_SECONDS - TOKEN_EXPIRY_BUFFER_SECONDS)
    except (ValueError, TypeError):
        return False


def _update_sf_cli_credential(cred_file: Path, new_access_token: str, new_instance_url: str) -> None:
    """Write a refreshed access token back to the SF CLI credential file."""
    try:
        data = json.loads(cred_file.read_text(encoding="utf-8"))
        data["accessToken"] = new_access_token
        data["instanceUrl"] = new_instance_url
        data["created"] = int(time.time() * 1000)  # milliseconds, matching SF CLI format
        cred_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"[salesforce_connect] Warning: could not update CLI credential file: {exc}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Auth mode: playwright
# ---------------------------------------------------------------------------


def _ensure_playwright() -> None:
    """Raise a helpful ConfigError if playwright is not installed."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        raise ConfigError(
            "playwright is not installed.\n"
            "Install it with:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )


def _connect_playwright(cfg: SalesforceConfig) -> "Salesforce":
    """
    Spawn a headed browser for interactive Salesforce login, then capture the
    session token from the browser's cookie jar.

    Flow:
      1. Check the token cache — reuse if not expired.
      2. Open a Chromium window at instance_url.
      3. User logs in (SSO, MFA, password — anything the browser supports).
      4. Poll for the 'sid' cookie once the user reaches a Salesforce page.
      5. Close the browser, cache the token, return a Salesforce instance.

    The cache key is derived from instance_url + sandbox flag so different orgs
    don't collide.
    """
    _ensure_playwright()

    instance_url = _resolve_field(cfg.instance_url, cfg.instance_url_env, "instance_url")
    cache_path = _token_cache_path(cfg)

    # Use instance_url as the cache key (no client_id for this mode)
    cached = _load_cached_token(cache_path, instance_url, cfg.sandbox)
    if cached and not _is_token_expired(cached):
        print("[salesforce_connect] Using cached Playwright session token.", file=sys.stderr)
        return Salesforce(
            instance_url=cached["instance_url"],
            session_id=cached["access_token"],
            version=cfg.api_version_clean,
        )

    print(
        f"[salesforce_connect] Opening browser — log in to {instance_url} to continue.\n"
        "[salesforce_connect] The browser will close automatically once login is detected.",
        file=sys.stderr,
    )
    token = _playwright_login_flow(instance_url, cfg.playwright_timeout)
    _save_cached_token(
        cache_path,
        {"access_token": token, "refresh_token": "", "instance_url": instance_url},
        instance_url,
        cfg.sandbox,
    )
    print("[salesforce_connect] Login detected. Token cached (~2 hours).", file=sys.stderr)
    return Salesforce(
        instance_url=instance_url,
        session_id=token,
        version=cfg.api_version_clean,
    )


def _playwright_login_flow(instance_url: str, timeout_seconds: int) -> str:
    """
    Open a headed Chromium browser at instance_url and poll for the Salesforce
    session cookie ('sid') until it appears or timeout_seconds elapses.

    Returns the sid value (a valid Bearer token, ~2 hours lifetime).
    Raises AuthError if the login is not completed within timeout_seconds.
    """
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlparse

    instance_domain = urlparse(instance_url).netloc

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto(instance_url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            try:
                browser.close()
            except Exception:
                pass
            raise NetworkError(f"Could not reach {instance_url}: {exc}") from exc

        sid: str = ""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                for cookie in context.cookies():
                    if (
                        cookie["name"] == "sid"
                        and instance_domain in cookie["domain"].lstrip(".")
                        and _looks_like_sf_token(cookie["value"])
                    ):
                        sid = cookie["value"]
                        break
            except Exception:
                break

            if sid:
                break

            try:
                page.wait_for_timeout(1000)
            except Exception:
                break

        try:
            browser.close()
        except Exception:
            pass

    if not sid:
        raise AuthError(
            f"Salesforce session token not found after {timeout_seconds}s.\n"
            "Make sure you completed the login and the browser reached the Salesforce home page.\n"
            "If login takes longer, increase playwright_timeout in active.yaml."
        )
    return sid


def _looks_like_sf_token(value: str) -> bool:
    """Heuristic: Salesforce session tokens are long strings containing a '!' separator."""
    return len(value) > 20 and "!" in value


# ---------------------------------------------------------------------------
# Auth mode: oauth_web
# ---------------------------------------------------------------------------


def _connect_oauth_web(cfg: SalesforceConfig) -> "Salesforce":
    """OAuth 2.0 Authorization Code flow with token caching and refresh."""
    client_id = _resolve_field(cfg.client_id, cfg.client_id_env, "client_id")
    client_secret = _resolve_field(cfg.client_secret, cfg.client_secret_env, "client_secret")
    cache_path = _token_cache_path(cfg)

    # Check for a valid cached token
    cached = _load_cached_token(cache_path, client_id, cfg.sandbox)
    if cached:
        if _is_token_expired(cached):
            refresh_tok = cached.get("refresh_token", "")
            if refresh_tok:
                print(
                    "[salesforce_connect] Refreshing cached Salesforce token…",
                    file=sys.stderr,
                )
                try:
                    refreshed = _refresh_token(
                        refresh_tok, client_id, client_secret, cfg.login_url
                    )
                    _save_cached_token(cache_path, refreshed, client_id, cfg.sandbox)
                    print("[salesforce_connect] Token refreshed.", file=sys.stderr)
                    return _sf_from_token(refreshed, cfg.api_version_clean)
                except AuthError:
                    # Refresh failed — fall through to browser flow
                    pass
        else:
            print("[salesforce_connect] Using cached Salesforce token.", file=sys.stderr)
            return _sf_from_token(cached, cfg.api_version_clean)

    # Full browser flow
    token_data = _oauth_web_flow(
        client_id=client_id,
        client_secret=client_secret,
        login_url=cfg.login_url,
        redirect_port=cfg.redirect_port,
    )
    _save_cached_token(cache_path, token_data, client_id, cfg.sandbox)
    print("[salesforce_connect] Authentication successful. Token cached.", file=sys.stderr)
    return _sf_from_token(token_data, cfg.api_version_clean)


def _sf_from_token(token_data: dict, api_version: str) -> "Salesforce":
    """Build a Salesforce instance from a token response dict."""
    return Salesforce(
        instance_url=token_data["instance_url"],
        session_id=token_data["access_token"],
        version=api_version,
    )


def _oauth_web_flow(
    client_id: str,
    client_secret: str,
    login_url: str,
    redirect_port: int,
) -> dict:
    """
    Run the OAuth 2.0 Authorization Code flow.

    Opens the Salesforce login page in the user's browser, starts a local HTTP
    server on redirect_port to capture the authorization code, then exchanges it
    for an access token + refresh token.

    Returns the parsed token response dict (access_token, refresh_token, instance_url, …).
    """
    redirect_uri = f"http://localhost:{redirect_port}/callback"
    auth_url = (
        f"{login_url}/services/oauth2/authorize?"
        + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        })
    )
    token_url = f"{login_url}/services/oauth2/token"

    # Shared state between handler and main thread
    code_holder: dict = {}
    done_event = threading.Event()

    handler_cls = _make_callback_handler(code_holder, done_event)

    try:
        server = HTTPServer(("localhost", redirect_port), handler_cls)
    except OSError as exc:
        raise AuthError(
            f"Cannot start OAuth callback server on port {redirect_port}: {exc}\n"
            f"Check that port {redirect_port} is not in use, "
            f"or set a different redirect_port in active.yaml."
        ) from exc

    server.timeout = 1.0  # handle_request polls at 1-second intervals

    print(
        "[salesforce_connect] Opening browser for Salesforce OAuth login…\n"
        f"[salesforce_connect] Waiting for callback on {redirect_uri}",
        file=sys.stderr,
    )
    print(f"[salesforce_connect] If the browser does not open, visit:\n  {auth_url}", file=sys.stderr)

    webbrowser.open(auth_url)

    start = time.monotonic()
    while not done_event.is_set():
        if time.monotonic() - start > OAUTH_TIMEOUT_SECONDS:
            server.server_close()
            raise AuthError(
                f"OAuth login timed out after {OAUTH_TIMEOUT_SECONDS}s. "
                "Please complete the browser login and try again."
            )
        server.handle_request()

    server.server_close()

    if "error" in code_holder:
        raise AuthError(f"Salesforce returned an OAuth error: {code_holder['error']}")

    auth_code = code_holder.get("code")
    if not auth_code:
        raise AuthError("No authorization code received from Salesforce callback.")

    return _exchange_code_for_token(auth_code, client_id, client_secret, token_url, redirect_uri)


def _make_callback_handler(code_holder: dict, done_event: object) -> type:
    """Return a BaseHTTPRequestHandler subclass that captures the OAuth callback."""

    class _CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)

            if "code" in params:
                code_holder["code"] = params["code"][0]
                self._respond(200, "Authorization successful! You can close this window.")
            else:
                err_parts = params.get(
                    "error_description",
                    params.get("error", ["Unknown OAuth error"]),
                )
                code_holder["error"] = err_parts[0]
                self._respond(400, f"Authorization failed: {code_holder['error']}")

            done_event.set()  # type: ignore[attr-defined]

        def _respond(self, status: int, message: str) -> None:
            body = (
                f"<html><body><h2>AnalystOS — Salesforce Auth</h2>"
                f"<p>{message}</p></body></html>"
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: N802
            pass  # suppress request log noise

    return _CallbackHandler


def _exchange_code_for_token(
    auth_code: str,
    client_id: str,
    client_secret: str,
    token_url: str,
    redirect_uri: str,
) -> dict:
    """POST the authorization code to the token endpoint and return parsed response."""
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": auth_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }).encode("utf-8")

    req = urllib.request.Request(token_url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise AuthError(
            f"Token exchange failed (HTTP {exc.code}): {error_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise NetworkError(f"Could not reach Salesforce token endpoint: {exc}") from exc


def _refresh_token(
    refresh_tok: str,
    client_id: str,
    client_secret: str,  # may be "" for public/CLI apps that don't require it
    login_url: str,
) -> dict:
    """Exchange a refresh token for a new access token."""
    params: dict = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_tok,
        "client_id": client_id,
    }
    if client_secret:
        params["client_secret"] = client_secret
    body = urllib.parse.urlencode(params).encode("utf-8")

    token_url = f"{login_url}/services/oauth2/token"
    req = urllib.request.Request(token_url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # Refresh responses don't always include refresh_token; preserve the old one
        if "refresh_token" not in data:
            data["refresh_token"] = refresh_tok
        return data
    except urllib.error.HTTPError as exc:
        raise AuthError(
            f"Token refresh failed (HTTP {exc.code}) — will re-authenticate."
        ) from exc
    except urllib.error.URLError as exc:
        raise NetworkError(f"Could not reach Salesforce token endpoint: {exc}") from exc


# ---------------------------------------------------------------------------
# Auth mode: password
# ---------------------------------------------------------------------------


def _connect_password(cfg: SalesforceConfig) -> "Salesforce":
    """Username + password (+ security token appended) auth via simple-salesforce."""
    username = _resolve_field(cfg.username, cfg.username_env, "username")
    password = _resolve_field(cfg.password, cfg.password_env, "password")

    domain = "test" if cfg.sandbox else "login"

    try:
        return Salesforce(
            username=username,
            password=password,
            security_token="",   # security token is expected to be appended to password
            domain=domain,
            version=cfg.api_version_clean,
        )
    except SalesforceAuthenticationFailed as exc:
        raise AuthError(
            f"Salesforce authentication failed for user '{username}': {exc}\n"
            "Check the username and password (with security token appended) in active.yaml."
        ) from exc


# ---------------------------------------------------------------------------
# Auth mode: access_token
# ---------------------------------------------------------------------------


def _connect_access_token(cfg: SalesforceConfig) -> "Salesforce":
    """
    Connect using a pre-obtained Bearer token.

    Token resolution order:
      1. access_token: value in active.yaml
      2. access_token_env: environment variable name
      3. Interactive prompt (if prompt: true, or if neither of the above is set)

    The interactive prompt uses getpass so the token is not echoed to the terminal.
    Useful for short-lived session tokens grabbed from the browser.
    """
    instance_url = _resolve_field(cfg.instance_url, cfg.instance_url_env, "instance_url")

    # Resolve token — fall back to interactive prompt
    access_token = cfg.access_token
    if not access_token and cfg.access_token_env:
        access_token = os.environ.get(cfg.access_token_env, "")

    if not access_token:
        if cfg.prompt or not (cfg.access_token or cfg.access_token_env):
            access_token = _prompt_for_token(instance_url)
        else:
            raise AuthError(
                "Salesforce access_token is not configured.\n"
                "Set 'access_token:' in active.yaml, point 'access_token_env:' to an env var, "
                "or set 'prompt: true' to be asked at session start."
            )

    return Salesforce(
        instance_url=instance_url,
        session_id=access_token,
        version=cfg.api_version_clean,
    )


def _prompt_for_token(instance_url: str) -> str:
    """
    Interactively prompt the analyst to paste a Salesforce session token.

    Uses getpass so the token is not echoed to the terminal.
    Instructions reference the three easiest capture methods.
    """
    import getpass
    print(
        "\nSalesforce session token required.\n"
        "Get one from any of these sources (token lasts ~2 hours):\n"
        "  • Salesforce Inspector extension → click the extension icon, copy Session ID\n"
        f"  • Browser DevTools on {instance_url} → Application → Cookies → 'sid' value\n"
        "  • Workbench (workbench.developerforce.com) → Info → Session Information\n",
        file=sys.stderr,
    )
    token = getpass.getpass("Paste Salesforce session token: ")
    if not token.strip():
        raise AuthError("No token entered. Aborting.")
    return token.strip()


# ---------------------------------------------------------------------------
# Token cache helpers
# ---------------------------------------------------------------------------


def _token_cache_path(cfg: SalesforceConfig) -> Path:
    base = cfg.config_dir if cfg.config_dir else Path(".claude/db-connections")
    return base / ".sf_token_cache.json"


def _cache_key(client_id: str, sandbox: bool) -> str:
    """Short but unique key for this client + environment combination."""
    material = f"{client_id}:{sandbox}"
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def _load_cached_token(
    cache_path: Path, client_id: str, sandbox: bool
) -> Optional[dict]:
    if not cache_path.exists():
        return None
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        return cache.get(_cache_key(client_id, sandbox))
    except (json.JSONDecodeError, OSError):
        return None


def _save_cached_token(
    cache_path: Path, token_data: dict, client_id: str, sandbox: bool
) -> None:
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    except (json.JSONDecodeError, OSError):
        cache = {}

    cache[_cache_key(client_id, sandbox)] = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token", ""),
        "instance_url": token_data["instance_url"],
        "cached_at": time.time(),
    }

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[salesforce_connect] Warning: could not write token cache: {exc}", file=sys.stderr)


def _is_token_expired(cached: dict) -> bool:
    """Return True if the cached token is expired or within the expiry buffer."""
    cached_at = float(cached.get("cached_at", 0))
    age = time.time() - cached_at
    return age > (TOKEN_LIFETIME_SECONDS - TOKEN_EXPIRY_BUFFER_SECONDS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_field(direct: str, env_var: str, label: str) -> str:
    """
    Return a credential value from either a direct config value or an env var.

    Resolution order:
      1. direct  — value written literally in active.yaml (preferred; file is gitignored)
      2. env_var — name of an environment variable to read at runtime

    Raises AuthError with a helpful message if neither is set.
    """
    if direct:
        return direct
    if env_var:
        value = os.environ.get(env_var)
        if value:
            return value
        raise AuthError(
            f"Salesforce credential '{label}' not found.\n"
            f"Either set '{label}:' directly in active.yaml, "
            f"or set the '{env_var}' environment variable."
        )
    raise AuthError(
        f"Salesforce credential '{label}' is not configured.\n"
        f"Set '{label}:' in the salesforce: block of active.yaml."
    )


# ---------------------------------------------------------------------------
# Query runner
# ---------------------------------------------------------------------------


class SalesforceRunner:
    """
    Executes SOQL queries and REST API calls against a Salesforce org.

    All execution is synchronous. Pagination is handled automatically by
    execute_query() and query_all().
    """

    def __init__(self, cfg: SalesforceConfig, sf: "Salesforce") -> None:
        self.cfg = cfg
        self._sf = sf

    def execute_query(
        self, soql: str, row_limit: int = 1000, include_deleted: bool = False
    ) -> tuple[list[str], list[list[str]]]:
        """
        Execute a SOQL query and return (column_names, rows).

        Automatically follows pagination (nextRecordsUrl) up to row_limit.
        Pass row_limit=0 for unlimited (up to Salesforce's 50k synchronous cap).

        include_deleted=True uses query_all() to include soft-deleted records.
        """
        try:
            if include_deleted:
                result = self._sf.query_all(soql)
            else:
                result = self._sf.query(soql)
        except SalesforceAuthenticationFailed as exc:
            raise AuthError(f"Salesforce session expired or invalid: {exc}") from exc
        except SalesforceError as exc:
            raise QueryError(str(exc)) from exc
        except Exception as exc:
            self._reraise(exc)

        columns: Optional[list[str]] = None
        rows: list[list[str]] = []

        while True:
            for rec in result.get("records", []):
                if columns is None:
                    columns = [k for k in rec.keys() if k != "attributes"]
                rows.append([_to_str(rec.get(col)) for col in columns])
                if row_limit > 0 and len(rows) >= row_limit:
                    return columns or [], rows

            if result.get("done"):
                break

            next_url = result.get("nextRecordsUrl")
            if not next_url:
                break

            try:
                result = self._sf.query_more(next_url, identifier_is_url=True)
            except Exception as exc:
                raise QueryError(f"Error fetching next page of results: {exc}") from exc

        return columns or [], rows

    def describe(self, object_name: str) -> dict:
        """
        Call Salesforce describeSObject for a given object name.

        Returns the raw describe dict (fields, relationships, picklists, etc.)
        Raises QueryError if the object is not found or inaccessible.
        """
        try:
            return getattr(self._sf, object_name).describe()
        except SalesforceAuthenticationFailed as exc:
            raise AuthError(f"Salesforce session expired or invalid: {exc}") from exc
        except SalesforceError as exc:
            raise QueryError(
                f"Could not describe '{object_name}': {exc}\n"
                "Check the object API name (case-insensitive, e.g. 'Account', 'Contact', 'My_Object__c')."
            ) from exc
        except Exception as exc:
            raise QueryError(f"Error describing '{object_name}': {exc}") from exc

    def count(self, object_name: str, where_clause: str = "") -> int:
        """
        Return the record count for a Salesforce object, optionally with a WHERE clause.

        Used for cost estimation before running large queries. Counts against the
        50,000-row synchronous query limit.
        """
        soql = f"SELECT COUNT() FROM {object_name}"
        if where_clause.strip():
            soql += f" WHERE {where_clause.strip()}"
        try:
            result = self._sf.query(soql)
            return int(result.get("totalSize", 0))
        except SalesforceAuthenticationFailed as exc:
            raise AuthError(f"Salesforce session expired or invalid: {exc}") from exc
        except SalesforceError as exc:
            raise QueryError(f"Count query failed: {exc}") from exc
        except Exception as exc:
            raise QueryError(f"Count query error: {exc}") from exc

    def get_identity(self) -> dict:
        """
        Return basic identity info for the authenticated user.

        Calls the standard OAuth 2.0 /services/oauth2/userinfo endpoint, which works
        for all auth modes. Returns a dict with preferred_username, name, email, etc.
        """
        instance = getattr(self._sf, "sf_instance", "")
        token = getattr(self._sf, "session_id", "")
        if not instance or not token:
            return {"preferred_username": "(unknown)"}
        try:
            url = f"https://{instance}/services/oauth2/userinfo"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return {"preferred_username": "(unknown)"}

    def _reraise(self, exc: Exception) -> None:
        msg = str(exc)
        if any(kw in msg.lower() for kw in ("connect", "timeout", "network", "socket", "dns")):
            raise NetworkError(f"Could not reach Salesforce: {msg}") from exc
        if any(kw in msg.lower() for kw in ("session", "expired", "invalid session")):
            raise AuthError(f"Salesforce session expired: {msg}") from exc
        raise QueryError(f"Salesforce error: {msg}") from exc


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _to_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        # Nested relationship result (e.g. Account.Name) — strip 'attributes' and serialize
        clean = {k: v for k, v in value.items() if k != "attributes"}
        return json.dumps(clean, default=str)
    return str(value)
