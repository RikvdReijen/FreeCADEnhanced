# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD XR workbench contributors                   *
# *                                                                          *
# *   This file is part of FreeCAD.                                          *
# *                                                                          *
# *   FreeCAD is free software: you can redistribute it and/or modify it     *
# *   under the terms of the GNU Lesser General Public License as            *
# *   published by the Free Software Foundation, either version 2.1 of the   *
# *   License, or (at your option) any later version.                        *
# *                                                                          *
# *   FreeCAD is distributed in the hope that it will be useful, but         *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of             *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU      *
# *   Lesser General Public License for more details.                        *
# ***************************************************************************
"""Google Drive v3 access with no third party dependencies.

Only ``urllib.request`` and ``json`` are used, so the workbench keeps working
on a stock FreeCAD install (§6).

Registering your own OAuth client
---------------------------------

FreeCAD ships **no** Google credentials: an OAuth client identifies *you*, and
a shared secret baked into an open source package is neither secret nor
allowed by Google's terms.  Every user registers their own client once:

1. Open https://console.cloud.google.com/ and create (or pick) a project.
2. *APIs & Services -> Library* -> enable **Google Drive API**.
3. *APIs & Services -> OAuth consent screen*: choose *External*, fill in the
   app name and your e-mail, and add your own account under *Test users*.
   Add the scope ``https://www.googleapis.com/auth/drive``.
4. *APIs & Services -> Credentials -> Create credentials -> OAuth client ID*:

   * for the desktop workbench pick **Desktop app** (that client also works
     for the loopback flow used here);
   * for the Quest headset pick **TVs and Limited Input devices**, which is
     the client type the OAuth *device* flow requires.

5. Put the client id (and, for a desktop client, the secret) where the
   workbench looks for them — either

   * ``~/.FreeCAD/xr/gdrive_client.json``::

         {"client_id": "....apps.googleusercontent.com",
          "client_secret": "...."}

     (write it with :func:`save_client_config`, which sets 0600), or
   * the environment variables ``FREECAD_XR_GDRIVE_CLIENT_ID`` and
     ``FREECAD_XR_GDRIVE_CLIENT_SECRET``.

Tokens are cached in ``~/.FreeCAD/xr/gdrive_token.json`` with 0600 permissions
and refreshed automatically.  **Tokens are never logged** — this module only
ever logs whether a token is present, never its value.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .paths import (
    DRIVE_CACHE_DIR,
    GDRIVE_CLIENT_FILE,
    GDRIVE_TOKEN_FILE,
    ensure_dir,
    read_json,
    write_json,
    xr_path,
)

__all__ = [
    "GDriveError",
    "GDriveOfflineError",
    "NotConfiguredError",
    "NotAuthenticatedError",
    "AuthorisationPending",
    "ConflictError",
    "DriveEntry",
    "DeviceCodeFlow",
    "DeviceCode",
    "LoopbackFlow",
    "GoogleDriveClient",
    "GoogleDriveSync",
    "AccountStatus",
    "account_status",
    "load_client_config",
    "save_client_config",
    "sign_out",
    "FOLDER_MIME",
    "FCXR_MIME",
    "FCSTD_MIME",
    "escape_query_value",
    "extension_filter",
    "TokenStore",
]

logger = logging.getLogger("xrsync.gdrive")

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_URL = "https://www.googleapis.com/drive/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"

DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/drive "
    "https://www.googleapis.com/auth/userinfo.email"
)

FOLDER_MIME = "application/vnd.google-apps.folder"
FCSTD_MIME = "application/x-extension-fcstd"
FCXR_MIME = "application/x-fcxr"

#: the file extensions the workbench cares about
SYNC_EXTENSIONS = (".FCStd", ".fcxr")

FILE_FIELDS = "id,name,mimeType,size,modifiedTime,md5Checksum,parents,trashed"
LIST_FIELDS = "nextPageToken,files(%s)" % FILE_FIELDS

ENV_CLIENT_ID = "FREECAD_XR_GDRIVE_CLIENT_ID"
ENV_CLIENT_SECRET = "FREECAD_XR_GDRIVE_CLIENT_SECRET"

#: seconds before the recorded expiry at which a token is considered stale
TOKEN_SKEW = 60.0
DEFAULT_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class GDriveError(Exception):
    """Any Google Drive failure."""

    def __init__(self, message: str, status: int = 0, reason: str = "") -> None:
        super().__init__(message)
        self.status = int(status)
        self.reason = reason


class GDriveOfflineError(GDriveError):
    """Google could not be reached at all."""


class NotConfiguredError(GDriveError):
    """No OAuth client is configured (see the module docstring)."""


class NotAuthenticatedError(GDriveError):
    """A client is configured but nobody is signed in."""


class AuthorisationPending(GDriveError):
    """The user has not finished the device flow yet."""


class ConflictError(GDriveError):
    """The remote file changed since it was pulled."""

    def __init__(self, message: str, entry: Optional["DriveEntry"] = None) -> None:
        super().__init__(message, status=409, reason="conflict")
        self.entry = entry


# ---------------------------------------------------------------------------
# client configuration
# ---------------------------------------------------------------------------


def client_config_path() -> str:
    return xr_path(GDRIVE_CLIENT_FILE)


def token_path() -> str:
    return xr_path(GDRIVE_TOKEN_FILE)


def cache_dir() -> str:
    return xr_path(DRIVE_CACHE_DIR)


def load_client_config() -> Optional[Dict[str, Any]]:
    """Return ``{"client_id", "client_secret", "scopes"}`` or ``None``.

    The environment wins over the config file so a headless run can override
    it without touching the user's home directory.
    """
    client_id = os.environ.get(ENV_CLIENT_ID)
    client_secret = os.environ.get(ENV_CLIENT_SECRET)
    config: Dict[str, Any] = {}
    stored = read_json(client_config_path(), default=None)
    if isinstance(stored, dict):
        config.update(stored)
        # Google's own downloaded JSON nests everything under "installed"
        for key in ("installed", "web"):
            nested = stored.get(key)
            if isinstance(nested, dict):
                config.update(nested)
    if client_id:
        config["client_id"] = client_id
    if client_secret:
        config["client_secret"] = client_secret
    if not config.get("client_id"):
        return None
    return {
        "client_id": str(config["client_id"]),
        "client_secret": str(config.get("client_secret") or ""),
        "scopes": str(config.get("scopes") or DEFAULT_SCOPES),
    }


def save_client_config(client_id: str, client_secret: str = "", scopes: str = "") -> str:
    """Write ``gdrive_client.json`` with 0600 permissions."""
    if not client_id:
        raise NotConfiguredError("a client id is required")
    payload = {"client_id": str(client_id), "client_secret": str(client_secret or "")}
    if scopes:
        payload["scopes"] = scopes
    return write_json(client_config_path(), payload, private=True)


def require_client_config() -> Dict[str, Any]:
    config = load_client_config()
    if config is None:
        raise NotConfiguredError(
            "no Google OAuth client configured — see the xrsync.gdrive docstring "
            "for how to register one, then use save_client_config()"
        )
    return config


# ---------------------------------------------------------------------------
# token storage
# ---------------------------------------------------------------------------


class TokenStore:
    """The persisted OAuth tokens.  Values are never logged."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or token_path()
        self._data: Dict[str, Any] = {}
        self.load()

    def load(self) -> Dict[str, Any]:
        data = read_json(self.path, default={})
        self._data = data if isinstance(data, dict) else {}
        return self._data

    def save(self) -> None:
        write_json(self.path, self._data, private=True)

    def clear(self) -> None:
        self._data = {}
        try:
            os.unlink(self.path)
        except OSError:
            pass

    # -- accessors ---------------------------------------------------------

    @property
    def access_token(self) -> Optional[str]:
        return self._data.get("access_token")

    @property
    def refresh_token(self) -> Optional[str]:
        return self._data.get("refresh_token")

    @property
    def expires_at(self) -> float:
        try:
            return float(self._data.get("expires_at") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def account(self) -> Optional[str]:
        return self._data.get("account")

    @property
    def scopes(self) -> str:
        return str(self._data.get("scope") or "")

    def is_expired(self, skew: float = TOKEN_SKEW) -> bool:
        return time.time() >= (self.expires_at - skew)

    def has_tokens(self) -> bool:
        return bool(self.access_token or self.refresh_token)

    def update_from_response(self, payload: Dict[str, Any]) -> None:
        """Merge a token endpoint response, keeping the old refresh token."""
        data = dict(self._data)
        for key in ("access_token", "refresh_token", "scope", "token_type", "id_token"):
            value = payload.get(key)
            if value:
                data[key] = value
        expires_in = payload.get("expires_in")
        if expires_in:
            try:
                data["expires_at"] = time.time() + float(expires_in)
            except (TypeError, ValueError):
                pass
        email = _email_from_id_token(payload.get("id_token"))
        if email:
            data["account"] = email
        self._data = data
        self.save()

    def set_account(self, email: Optional[str]) -> None:
        if email and email != self._data.get("account"):
            self._data["account"] = email
            self.save()


def _email_from_id_token(id_token: Optional[str]) -> Optional[str]:
    """Best effort e-mail extraction from an id token (never trusted for auth)."""
    if not id_token or not isinstance(id_token, str):
        return None
    parts = id_token.split(".")
    if len(parts) != 3:
        return None
    import base64

    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception:
        return None
    email = claims.get("email")
    return str(email) if email else None


def sign_out() -> None:
    """Forget the stored tokens (does not touch the client configuration)."""
    TokenStore().clear()


@dataclass
class AccountStatus:
    """What the GUI needs to render the Drive section of the panel."""

    configured: bool = False
    signed_in: bool = False
    account: Optional[str] = None
    cache_dir: str = ""
    scopes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "configured": self.configured,
            "signed_in": self.signed_in,
            "account": self.account,
            "cache_dir": self.cache_dir,
            "scopes": self.scopes,
        }


def account_status() -> AccountStatus:
    """Offline description of the current Drive configuration."""
    config = load_client_config()
    store = TokenStore()
    return AccountStatus(
        configured=config is not None,
        signed_in=store.has_tokens(),
        account=store.account,
        cache_dir=cache_dir(),
        scopes=store.scopes or (config or {}).get("scopes", ""),
    )


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def _urlopen(request: "urllib.request.Request", timeout: float):
    """Single choke point so tests can monkeypatch ``urllib.request.urlopen``."""
    return urllib.request.urlopen(request, timeout=timeout)


def _describe_http_error(exc: "urllib.error.HTTPError") -> Tuple[str, str]:
    try:
        raw = exc.read()
    except Exception:
        raw = b""
    message = raw[:500].decode("utf-8", "replace")
    reason = ""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or message
            reason = str(error.get("status") or error.get("code") or "")
            errors = error.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                reason = str(errors[0].get("reason") or reason)
        elif isinstance(error, str):
            reason = error
            message = payload.get("error_description") or error
    return message or str(exc), reason


def http_request(
    method: str,
    url: str,
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    raw: bool = False,
) -> Any:
    """Perform one HTTP request and return parsed JSON (or raw bytes).

    Returns ``(payload, response_headers)`` when ``raw`` is true, otherwise the
    decoded JSON payload (``{}`` for an empty body).
    """
    request = urllib.request.Request(url, data=data, method=method.upper())
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        response = _urlopen(request, timeout)
    except urllib.error.HTTPError as exc:
        message, reason = _describe_http_error(exc)
        status = int(getattr(exc, "code", 0) or 0)
        logger.debug("Drive %s %s -> HTTP %s (%s)", method, _redact(url), status, reason)
        if reason in ("authorization_pending", "slow_down"):
            raise AuthorisationPending(message, status, reason)
        if status in (401, 403) and reason in ("invalid_grant", "invalid_token", ""):
            raise GDriveError(message, status, reason)
        raise GDriveError(message, status, reason)
    except urllib.error.URLError as exc:
        raise GDriveOfflineError("cannot reach Google: %s" % (exc.reason,)) from None
    except (socket.timeout, TimeoutError) as exc:
        raise GDriveOfflineError("Google Drive timed out: %s" % (exc,)) from None

    with _closing(response):
        body = response.read()
        response_headers = _headers_of(response)
    if raw:
        return body, response_headers
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise GDriveError("Drive returned invalid JSON: %s" % (exc,)) from None


class _closing:
    """``contextlib.closing`` without importing contextlib for one use."""

    def __init__(self, thing: Any) -> None:
        self.thing = thing

    def __enter__(self) -> Any:
        return self.thing

    def __exit__(self, *exc: Any) -> None:
        close = getattr(self.thing, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                pass


def _headers_of(response: Any) -> Dict[str, str]:
    headers = getattr(response, "headers", None)
    if headers is None:
        getter = getattr(response, "getheaders", None)
        if getter is None:
            return {}
        return {str(k).lower(): str(v) for k, v in getter()}
    try:
        return {str(k).lower(): str(v) for k, v in headers.items()}
    except Exception:
        return {}


_SECRET_RE = re.compile(
    r"(access_token|refresh_token|client_secret|device_code|code)=[^&\s]+"
)


def _redact(text: str) -> str:
    """Strip anything token shaped before it can reach a log."""
    return _SECRET_RE.sub(r"\1=<redacted>", str(text))


def _form(data: Dict[str, Any]) -> bytes:
    return urllib.parse.urlencode(data).encode("utf-8")


# ---------------------------------------------------------------------------
# OAuth flows
# ---------------------------------------------------------------------------


@dataclass
class DeviceCode:
    """What the headset shows the user during the device flow."""

    device_code: str = ""
    user_code: str = ""
    verification_url: str = ""
    interval: int = 5
    expires_in: int = 1800

    def __repr__(self) -> str:  # never print the device code
        return (
            "DeviceCode(user_code=%r, verification_url=%r, interval=%d, expires_in=%d)"
            % (self.user_code, self.verification_url, self.interval, self.expires_in)
        )


class DeviceCodeFlow:
    """OAuth 2.0 device authorisation flow (RFC 8628) — used by the headset."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        store: Optional[TokenStore] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.config = config or require_client_config()
        self.store = store or TokenStore()
        self.timeout = timeout
        self.code: Optional[DeviceCode] = None
        self._deadline = 0.0
        self._next_poll = 0.0

    def start(self) -> DeviceCode:
        """Request a device code; show ``verification_url`` and ``user_code``."""
        payload = http_request(
            "POST",
            DEVICE_CODE_URL,
            data=_form(
                {
                    "client_id": self.config["client_id"],
                    "scope": self.config.get("scopes", DEFAULT_SCOPES),
                }
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        if not isinstance(payload, dict) or "device_code" not in payload:
            raise GDriveError("Google did not return a device code")
        self.code = DeviceCode(
            device_code=str(payload["device_code"]),
            user_code=str(payload.get("user_code", "")),
            verification_url=str(
                payload.get("verification_url")
                or payload.get("verification_uri")
                or "https://www.google.com/device"
            ),
            interval=int(payload.get("interval", 5) or 5),
            expires_in=int(payload.get("expires_in", 1800) or 1800),
        )
        self._deadline = time.time() + self.code.expires_in
        self._next_poll = 0.0
        logger.info(
            "Drive device flow started; enter %s at %s",
            self.code.user_code,
            self.code.verification_url,
        )
        return self.code

    def poll_once(self) -> bool:
        """Poll the token endpoint once.

        Returns ``True`` when tokens were obtained and stored, ``False`` while
        the user has not finished yet.  Raises on hard errors (denied, expired).
        """
        if self.code is None:
            raise GDriveError("call start() before poll_once()")
        if time.time() > self._deadline:
            raise GDriveError("the device code expired", reason="expired_token")
        now = time.monotonic()
        if now < self._next_poll:
            return False
        self._next_poll = now + max(1, self.code.interval)

        body = {
            "client_id": self.config["client_id"],
            "device_code": self.code.device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        if self.config.get("client_secret"):
            body["client_secret"] = self.config["client_secret"]
        try:
            payload = http_request(
                "POST",
                TOKEN_URL,
                data=_form(body),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
        except AuthorisationPending as exc:
            if exc.reason == "slow_down":
                self.code.interval += 5
            return False
        except GDriveError as exc:
            if exc.reason in ("access_denied", "expired_token"):
                raise
            if exc.reason == "authorization_pending":
                return False
            raise
        if not isinstance(payload, dict) or "access_token" not in payload:
            return False
        self.store.update_from_response(payload)
        logger.info("Drive device flow complete (token stored)")
        return True

    def wait(
        self, on_poll: Optional[Callable[[], None]] = None, timeout: float = 600.0
    ) -> bool:
        """Block until the user finishes, or ``timeout`` elapses."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.poll_once():
                return True
            if on_poll is not None:
                on_poll()
            time.sleep(1.0)
        return False


class LoopbackFlow:
    """OAuth 2.0 loopback (installed application) flow with PKCE — desktop."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        store: Optional[TokenStore] = None,
        host: str = "127.0.0.1",
        port: int = 0,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.config = config or require_client_config()
        self.store = store or TokenStore()
        self.host = host
        self.port = port
        self.timeout = timeout
        self.state = ""
        self.verifier = ""
        self._server = None

    # -- PKCE --------------------------------------------------------------

    @staticmethod
    def _challenge(verifier: str) -> str:
        import base64

        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def authorisation_url(self, redirect_uri: str) -> str:
        self.state = secrets.token_urlsafe(24)
        self.verifier = secrets.token_urlsafe(64)
        params = {
            "client_id": self.config["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.config.get("scopes", DEFAULT_SCOPES),
            "state": self.state,
            "code_challenge": self._challenge(self.verifier),
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
        return AUTH_URL + "?" + urllib.parse.urlencode(params)

    # -- the flow ----------------------------------------------------------

    def run(self, open_browser: bool = True, wait: float = 300.0) -> bool:
        """Run the whole flow; returns ``True`` when tokens were stored."""
        from http.server import BaseHTTPRequestHandler, HTTPServer

        holder: Dict[str, Any] = {}

        class _Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # noqa: A003
                pass

            def do_GET(self) -> None:  # noqa: N802
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                holder["code"] = (query.get("code") or [None])[0]
                holder["state"] = (query.get("state") or [None])[0]
                holder["error"] = (query.get("error") or [None])[0]
                body = (
                    b"<html><body><h2>FreeCAD XR</h2><p>You can close this "
                    b"tab and go back to FreeCAD.</p></body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer((self.host, self.port), _Redirect)
        server.timeout = 1.0
        redirect_uri = "http://%s:%d/" % (self.host, server.server_address[1])
        url = self.authorisation_url(redirect_uri)
        try:
            if open_browser:
                try:
                    import webbrowser

                    webbrowser.open(url)
                except Exception:
                    logger.info("open this URL to authorise: %s", url)
            deadline = time.time() + wait
            while "code" not in holder and "error" not in holder:
                if time.time() > deadline:
                    raise GDriveError("timed out waiting for the browser redirect")
                server.handle_request()
        finally:
            server.server_close()

        if holder.get("error"):
            raise GDriveError("authorisation refused: %s" % holder["error"])
        if holder.get("state") != self.state:
            raise GDriveError("OAuth state mismatch — aborting")
        return self.exchange(str(holder["code"]), redirect_uri)

    def exchange(self, code: str, redirect_uri: str) -> bool:
        body = {
            "client_id": self.config["client_id"],
            "code": code,
            "code_verifier": self.verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        if self.config.get("client_secret"):
            body["client_secret"] = self.config["client_secret"]
        payload = http_request(
            "POST",
            TOKEN_URL,
            data=_form(body),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        if not isinstance(payload, dict) or "access_token" not in payload:
            raise GDriveError("the token exchange returned no access token")
        self.store.update_from_response(payload)
        return True


# ---------------------------------------------------------------------------
# Drive entries
# ---------------------------------------------------------------------------


@dataclass
class DriveEntry:
    """One Drive file or folder."""

    file_id: str = ""
    name: str = ""
    is_folder: bool = False
    size: int = 0
    modified_time: Optional[str] = None
    mime_type: str = ""
    md5_checksum: Optional[str] = None
    parents: List[str] = field(default_factory=list)
    trashed: bool = False

    @classmethod
    def from_api(cls, payload: Dict[str, Any]) -> "DriveEntry":
        mime = str(payload.get("mimeType") or "")
        try:
            size = int(payload.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        return cls(
            file_id=str(payload.get("id") or ""),
            name=str(payload.get("name") or ""),
            is_folder=mime == FOLDER_MIME,
            size=size,
            modified_time=payload.get("modifiedTime"),
            mime_type=mime,
            md5_checksum=payload.get("md5Checksum"),
            parents=list(payload.get("parents") or []),
            trashed=bool(payload.get("trashed", False)),
        )

    @property
    def extension(self) -> str:
        _, ext = os.path.splitext(self.name)
        return ext

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_id": self.file_id,
            "name": self.name,
            "is_folder": self.is_folder,
            "size": self.size,
            "modified_time": self.modified_time,
            "mime_type": self.mime_type,
            "md5_checksum": self.md5_checksum,
            "parents": list(self.parents),
        }


def escape_query_value(value: str) -> str:
    """Escape a literal for a Drive ``q`` expression."""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


#: internal alias kept for readability at the call sites
_escape_query_value = escape_query_value


def extension_filter(include_folders: bool = True) -> str:
    """``q`` fragment limiting results to ``.FCStd``/``.fcxr`` (and folders)."""
    clauses = ["name contains '%s'" % _escape_query_value(ext) for ext in SYNC_EXTENSIONS]
    if include_folders:
        clauses.append("mimeType = '%s'" % FOLDER_MIME)
    return "(" + " or ".join(clauses) + ")"


def guess_mime(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".fcstd"):
        return FCSTD_MIME
    if lowered.endswith(".fcxr"):
        return FCXR_MIME
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


# ---------------------------------------------------------------------------
# the API client
# ---------------------------------------------------------------------------


class GoogleDriveClient:
    """Minimal Google Drive v3 client (files the workbench cares about)."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        store: Optional[TokenStore] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.config = config or require_client_config()
        self.store = store or TokenStore()
        self.timeout = timeout

    # -- construction ------------------------------------------------------

    @classmethod
    def from_stored_credentials(
        cls, timeout: float = DEFAULT_TIMEOUT
    ) -> "GoogleDriveClient":
        """Build a client from the stored client config and tokens."""
        config = require_client_config()
        store = TokenStore()
        if not store.has_tokens():
            raise NotAuthenticatedError(
                "not signed in to Google Drive — run the device or loopback flow"
            )
        return cls(config=config, store=store, timeout=timeout)

    # -- authentication ----------------------------------------------------

    def access_token(self) -> str:
        """A fresh access token, refreshing it when needed."""
        if not self.store.has_tokens():
            raise NotAuthenticatedError("not signed in to Google Drive")
        if self.store.access_token and not self.store.is_expired():
            return str(self.store.access_token)
        if not self.store.refresh_token:
            raise NotAuthenticatedError("the access token expired and there is no "
                                        "refresh token — sign in again")
        self.refresh()
        token = self.store.access_token
        if not token:
            raise NotAuthenticatedError("could not refresh the Google Drive token")
        return str(token)

    def refresh(self) -> None:
        """Exchange the refresh token for a new access token."""
        body = {
            "client_id": self.config["client_id"],
            "refresh_token": self.store.refresh_token,
            "grant_type": "refresh_token",
        }
        if self.config.get("client_secret"):
            body["client_secret"] = self.config["client_secret"]
        try:
            payload = http_request(
                "POST",
                TOKEN_URL,
                data=_form(body),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
        except GDriveError as exc:
            if exc.reason == "invalid_grant":
                raise NotAuthenticatedError(
                    "Google rejected the stored refresh token — sign in again"
                ) from None
            raise
        if not isinstance(payload, dict) or "access_token" not in payload:
            raise NotAuthenticatedError("the refresh response carried no access token")
        self.store.update_from_response(payload)
        logger.debug("Drive access token refreshed (expires in %ss)",
                     payload.get("expires_in"))

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {"Authorization": "Bearer %s" % self.access_token()}
        headers.update(extra or {})
        return headers

    def _api(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        raw: bool = False,
        base: Optional[str] = None,
    ) -> Any:
        url = (base or API_URL) + path
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )
        return http_request(
            method,
            url,
            data=data,
            headers=self._headers(headers),
            timeout=self.timeout,
            raw=raw,
        )

    # -- account -----------------------------------------------------------

    def about(self) -> Dict[str, Any]:
        """``GET /about`` — also caches the signed-in e-mail address."""
        payload = self._api("GET", "/about", {"fields": "user(emailAddress,displayName),storageQuota"})
        user = payload.get("user") if isinstance(payload, dict) else None
        if isinstance(user, dict):
            self.store.set_account(user.get("emailAddress"))
        return payload if isinstance(payload, dict) else {}

    # -- listing -----------------------------------------------------------

    def list_files(
        self,
        query: Optional[str] = None,
        page_size: int = 100,
        max_results: int = 1000,
        order_by: str = "folder,name",
        include_folders: bool = True,
        filter_extensions: bool = True,
    ) -> List[DriveEntry]:
        """List files, restricted to ``.FCStd``/``.fcxr`` (and folders).

        ``query`` is an extra Drive ``q`` expression, ANDed with the extension
        filter and ``trashed = false``.
        """
        clauses = ["trashed = false"]
        if query:
            clauses.append("(%s)" % query)
        if filter_extensions:
            clauses.append(extension_filter(include_folders))
        combined = " and ".join(clauses)

        entries: List[DriveEntry] = []
        page_token: Optional[str] = None
        while True:
            payload = self._api(
                "GET",
                "/files",
                {
                    "q": combined,
                    "fields": LIST_FIELDS,
                    "pageSize": max(1, min(1000, int(page_size))),
                    "orderBy": order_by,
                    "pageToken": page_token,
                    "spaces": "drive",
                },
            )
            for item in payload.get("files", []) or []:
                entries.append(DriveEntry.from_api(item))
                if len(entries) >= max_results:
                    return entries
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return entries

    def list_children(self, parent_id: str = "root", **kwargs: Any) -> List[DriveEntry]:
        """Folder navigation: everything directly inside ``parent_id``."""
        return self.list_files(
            query="'%s' in parents" % _escape_query_value(parent_id or "root"),
            **kwargs,
        )

    def search(self, name_fragment: str, **kwargs: Any) -> List[DriveEntry]:
        """Search by (partial) name, still restricted to our extensions."""
        return self.list_files(
            query="name contains '%s'" % _escape_query_value(name_fragment), **kwargs
        )

    def metadata(self, file_id: str) -> DriveEntry:
        """Metadata of one file."""
        payload = self._api(
            "GET", "/files/%s" % urllib.parse.quote(file_id), {"fields": FILE_FIELDS}
        )
        return DriveEntry.from_api(payload)

    # -- content -----------------------------------------------------------

    def download(self, file_id: str) -> bytes:
        """Download a file's content (``alt=media``)."""
        body, _ = self._api(
            "GET",
            "/files/%s" % urllib.parse.quote(file_id),
            {"alt": "media"},
            raw=True,
        )
        return body

    def download_to(self, file_id: str, path: str) -> str:
        data = self.download(file_id)
        ensure_dir(os.path.dirname(os.path.abspath(path)), private=False)
        tmp = path + ".part"
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
        return path

    def _resumable(
        self,
        method: str,
        url: str,
        metadata: Dict[str, Any],
        data: bytes,
        mime: str,
    ) -> DriveEntry:
        """Start a resumable session and upload ``data`` in one PUT."""
        body = json.dumps(metadata).encode("utf-8")
        _, headers = http_request(
            method,
            url,
            data=body,
            headers=self._headers(
                {
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": mime,
                    "X-Upload-Content-Length": str(len(data)),
                }
            ),
            timeout=self.timeout,
            raw=True,
        )
        location = headers.get("location")
        if not location:
            raise GDriveError("Drive did not return a resumable upload URL")
        payload = http_request(
            "PUT",
            location,
            data=data,
            headers={"Content-Type": mime, "Content-Length": str(len(data))},
            timeout=max(self.timeout, 120.0),
        )
        if not isinstance(payload, dict) or not payload.get("id"):
            raise GDriveError("the resumable upload did not return a file id")
        return DriveEntry.from_api(payload)

    def upload(
        self,
        name: str,
        data: bytes,
        parent: Optional[str] = None,
        mime: Optional[str] = None,
    ) -> DriveEntry:
        """Create a new file with a resumable upload."""
        metadata: Dict[str, Any] = {"name": name}
        if parent:
            metadata["parents"] = [parent]
        mime = mime or guess_mime(name)
        url = UPLOAD_URL + "?" + urllib.parse.urlencode(
            {"uploadType": "resumable", "fields": FILE_FIELDS}
        )
        return self._resumable("POST", url, metadata, bytes(data), mime)

    def update(
        self, file_id: str, data: bytes, name: Optional[str] = None,
        mime: Optional[str] = None,
    ) -> DriveEntry:
        """Replace the content of an existing file."""
        metadata: Dict[str, Any] = {}
        if name:
            metadata["name"] = name
        url = (
            UPLOAD_URL
            + "/"
            + urllib.parse.quote(file_id)
            + "?"
            + urllib.parse.urlencode({"uploadType": "resumable", "fields": FILE_FIELDS})
        )
        return self._resumable(
            "PATCH", url, metadata, bytes(data), mime or guess_mime(name or "")
        )

    def upload_file(
        self, path: str, parent: Optional[str] = None, name: Optional[str] = None
    ) -> DriveEntry:
        with open(path, "rb") as handle:
            data = handle.read()
        return self.upload(name or os.path.basename(path), data, parent)

    # -- folders -----------------------------------------------------------

    def ensure_folder(self, name: str, parent: Optional[str] = None) -> DriveEntry:
        """Return the folder ``name`` under ``parent``, creating it if needed."""
        query = "mimeType = '%s' and name = '%s' and '%s' in parents" % (
            FOLDER_MIME,
            _escape_query_value(name),
            _escape_query_value(parent or "root"),
        )
        existing = self.list_files(
            query=query, filter_extensions=False, max_results=1, order_by="name"
        )
        if existing:
            return existing[0]
        metadata: Dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
        if parent:
            metadata["parents"] = [parent]
        payload = self._api(
            "POST",
            "/files",
            {"fields": FILE_FIELDS},
            data=json.dumps(metadata).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )
        return DriveEntry.from_api(payload)

    def delete(self, file_id: str) -> None:
        """Move a file to the trash (never a hard delete)."""
        self._api(
            "PATCH",
            "/files/%s" % urllib.parse.quote(file_id),
            {"fields": "id"},
            data=json.dumps({"trashed": True}).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )


# ---------------------------------------------------------------------------
# the sync façade
# ---------------------------------------------------------------------------


def md5_of(path: str) -> str:
    digest = hashlib.md5()  # Drive's own checksum algorithm, not security
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class SyncRecord:
    """What we knew about a file the last time it was pulled or pushed."""

    file_id: str = ""
    name: str = ""
    local_path: str = ""
    modified_time: Optional[str] = None
    md5_checksum: Optional[str] = None
    synced_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_id": self.file_id,
            "name": self.name,
            "local_path": self.local_path,
            "modified_time": self.modified_time,
            "md5_checksum": self.md5_checksum,
            "synced_at": self.synced_at,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SyncRecord":
        return cls(
            file_id=str(payload.get("file_id", "")),
            name=str(payload.get("name", "")),
            local_path=str(payload.get("local_path", "")),
            modified_time=payload.get("modified_time"),
            md5_checksum=payload.get("md5_checksum"),
            synced_at=float(payload.get("synced_at", 0.0) or 0.0),
        )


class GoogleDriveSync:
    """Local cache of Drive files with conflict detection.

    ``~/.FreeCAD/xr/drive-cache/`` holds the downloaded files and a
    ``state.json`` recording the ``modifiedTime``/``md5Checksum`` each file had
    when it was last pulled or pushed.
    """

    STATE_FILE = "state.json"

    def __init__(
        self,
        client: Optional[GoogleDriveClient] = None,
        directory: Optional[str] = None,
    ) -> None:
        self._client = client
        self.directory = directory or cache_dir()
        ensure_dir(self.directory, private=False)
        self._records: Dict[str, SyncRecord] = {}
        self.load()

    # -- state -------------------------------------------------------------

    @property
    def state_path(self) -> str:
        return os.path.join(self.directory, self.STATE_FILE)

    def load(self) -> None:
        data = read_json(self.state_path, default={})
        records = data.get("files", {}) if isinstance(data, dict) else {}
        self._records = {
            str(key): SyncRecord.from_dict(value)
            for key, value in (records or {}).items()
            if isinstance(value, dict)
        }

    def save(self) -> None:
        write_json(
            self.state_path,
            {"version": 1, "files": {k: v.to_dict() for k, v in self._records.items()}},
            private=False,
        )

    def record(self, file_id: str) -> Optional[SyncRecord]:
        return self._records.get(file_id)

    @property
    def client(self) -> GoogleDriveClient:
        if self._client is None:
            self._client = GoogleDriveClient.from_stored_credentials()
        return self._client

    # -- paths -------------------------------------------------------------

    def local_path_for(self, entry: DriveEntry) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", entry.name or entry.file_id)
        return os.path.join(self.directory, "%s-%s" % (entry.file_id[:12], safe))

    # -- operations --------------------------------------------------------

    def pull(self, file_id: str, force: bool = False) -> str:
        """Download ``file_id`` into the cache and return the local path."""
        entry = self.client.metadata(file_id)
        path = self.local_path_for(entry)
        known = self._records.get(file_id)
        if (
            not force
            and known is not None
            and os.path.isfile(path)
            and entry.md5_checksum
            and known.md5_checksum == entry.md5_checksum
        ):
            return path
        self.client.download_to(file_id, path)
        self._records[file_id] = SyncRecord(
            file_id=file_id,
            name=entry.name,
            local_path=path,
            modified_time=entry.modified_time,
            md5_checksum=entry.md5_checksum or md5_of(path),
            synced_at=time.time(),
        )
        self.save()
        return path

    def push(
        self,
        local_path: str,
        file_id: Optional[str] = None,
        parent: Optional[str] = None,
        name: Optional[str] = None,
        force: bool = False,
    ) -> DriveEntry:
        """Upload a local file, refusing to clobber a changed remote."""
        if not os.path.isfile(local_path):
            raise GDriveError("no such file: %s" % local_path)
        with open(local_path, "rb") as handle:
            data = handle.read()
        if file_id is None:
            entry = self.client.upload(
                name or os.path.basename(local_path), data, parent=parent
            )
        else:
            if not force:
                conflict = self.check_conflict(file_id)
                if conflict is not None:
                    raise ConflictError(
                        "the Drive copy of %r changed since it was pulled — "
                        "pull it again or push with force=True" % (conflict.name,),
                        conflict,
                    )
            entry = self.client.update(
                file_id, data, name=name or os.path.basename(local_path)
            )
        self._records[entry.file_id] = SyncRecord(
            file_id=entry.file_id,
            name=entry.name,
            local_path=local_path,
            modified_time=entry.modified_time,
            md5_checksum=entry.md5_checksum or hashlib.md5(data).hexdigest(),
            synced_at=time.time(),
        )
        self.save()
        return entry

    def check_conflict(self, file_id: str) -> Optional[DriveEntry]:
        """Return the remote entry when it drifted from our record."""
        known = self._records.get(file_id)
        if known is None:
            return None
        entry = self.client.metadata(file_id)
        if entry.md5_checksum and known.md5_checksum:
            changed = entry.md5_checksum != known.md5_checksum
        else:
            changed = bool(
                entry.modified_time
                and known.modified_time
                and entry.modified_time != known.modified_time
            )
        return entry if changed else None

    def status(self, file_id: str, local_path: Optional[str] = None) -> str:
        """``unknown`` | ``in_sync`` | ``local_changes`` | ``remote_changes`` |
        ``conflict`` | ``offline``."""
        known = self._records.get(file_id)
        if known is None:
            return "unknown"
        path = local_path or known.local_path
        local_changed = False
        if path and os.path.isfile(path):
            local_changed = md5_of(path) != (known.md5_checksum or "")
        try:
            remote_changed = self.check_conflict(file_id) is not None
        except GDriveOfflineError:
            return "offline"
        if local_changed and remote_changed:
            return "conflict"
        if local_changed:
            return "local_changes"
        if remote_changed:
            return "remote_changes"
        return "in_sync"

    def forget(self, file_id: str) -> None:
        if self._records.pop(file_id, None) is not None:
            self.save()
