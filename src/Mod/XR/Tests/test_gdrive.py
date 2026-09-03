# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the dependency-free Google Drive integration.

``urllib.request.urlopen`` is monkeypatched throughout: nothing here touches
the network, and ``FREECAD_XR_HOME`` is redirected to a temporary directory so
the real ``~/.FreeCAD`` is never read or written.
"""

import io
import json
import logging
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xrsync import gdrive  # noqa: E402
from xrsync.gdrive import (  # noqa: E402
    ConflictError,
    DeviceCodeFlow,
    DriveEntry,
    GDriveError,
    GDriveOfflineError,
    GoogleDriveClient,
    GoogleDriveSync,
    NotAuthenticatedError,
    NotConfiguredError,
    TokenStore,
    account_status,
    load_client_config,
    save_client_config,
    sign_out,
)


# ---------------------------------------------------------------------------
# fake transport
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, body=b"", headers=None, status=200):
        self._body = body
        self.headers = headers or {}
        self.status = status
        self.closed = False

    def read(self):
        return self._body

    def close(self):
        self.closed = True


def json_response(payload, headers=None):
    return FakeResponse(json.dumps(payload).encode("utf-8"), headers)


def http_error(status, payload, url="https://example.invalid"):
    body = json.dumps(payload).encode("utf-8")
    return urllib.error.HTTPError(url, status, "error", HTTPMessage(), io.BytesIO(body))


class FakeTransport:
    """Records requests and replays queued responses."""

    def __init__(self, testcase):
        self.test = testcase
        self.requests = []
        self.responses = []

    def queue(self, *responses):
        self.responses.extend(responses)
        return self

    def install(self):
        original = urllib.request.urlopen
        urllib.request.urlopen = self
        self.test.addCleanup(setattr, urllib.request, "urlopen", original)
        return self

    def __call__(self, request, timeout=None):
        body = request.data
        self.requests.append(
            {
                "method": request.get_method(),
                "url": request.full_url,
                "headers": {k.lower(): v for k, v in request.header_items()},
                "body": body,
                "timeout": timeout,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected request: %s %s"
                                 % (request.get_method(), request.full_url))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    # -- helpers -----------------------------------------------------------

    def last(self):
        return self.requests[-1]

    def form(self, index=-1):
        body = self.requests[index]["body"] or b""
        return dict(urllib.parse.parse_qsl(body.decode("utf-8")))

    def query(self, index=-1):
        parsed = urllib.parse.urlparse(self.requests[index]["url"])
        return dict(urllib.parse.parse_qsl(parsed.query))

    def path(self, index=-1):
        return urllib.parse.urlparse(self.requests[index]["url"]).path


class GDriveTestCase(unittest.TestCase):
    """Redirects the XR home and installs a fake transport."""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        previous = os.environ.get("FREECAD_XR_HOME")
        os.environ["FREECAD_XR_HOME"] = self.home.name
        self.addCleanup(self._restore_home, previous)
        for name in ("FREECAD_XR_GDRIVE_CLIENT_ID", "FREECAD_XR_GDRIVE_CLIENT_SECRET"):
            if name in os.environ:
                value = os.environ.pop(name)
                self.addCleanup(os.environ.__setitem__, name, value)
        self.http = FakeTransport(self).install()

    def _restore_home(self, previous):
        if previous is None:
            os.environ.pop("FREECAD_XR_HOME", None)
        else:
            os.environ["FREECAD_XR_HOME"] = previous

    def configure(self, client_id="cid.apps.googleusercontent.com", secret="sec"):
        save_client_config(client_id, secret)
        return load_client_config()

    def sign_in(self, expires_in=3600, refresh="refresh-token"):
        store = TokenStore()
        store.update_from_response(
            {
                "access_token": "access-token",
                "refresh_token": refresh,
                "expires_in": expires_in,
                "scope": gdrive.DEFAULT_SCOPES,
                "token_type": "Bearer",
            }
        )
        return store


# ---------------------------------------------------------------------------
# configuration and token storage
# ---------------------------------------------------------------------------


class ClientConfigTest(GDriveTestCase):
    def test_nothing_configured(self):
        self.assertIsNone(load_client_config())
        status = account_status()
        self.assertFalse(status.configured)
        self.assertFalse(status.signed_in)
        self.assertIn("drive-cache", status.cache_dir)

    def test_no_credentials_are_hardcoded(self):
        import re

        with open(gdrive.__file__, "r", encoding="utf-8") as handle:
            source = handle.read()
        # a real client id looks like 1234567890-abcdef....apps.googleusercontent.com
        self.assertIsNone(
            re.search(r"\d{6,}-[a-z0-9]{12,}\.apps\.googleusercontent\.com", source)
        )
        self.assertNotIn("GOCSPX-", source)  # a real client secret prefix
        self.assertIn("FREECAD_XR_GDRIVE_CLIENT_ID", source)

    def test_save_and_load(self):
        save_client_config("cid", "secret")
        config = load_client_config()
        self.assertEqual(config["client_id"], "cid")
        self.assertEqual(config["client_secret"], "secret")
        self.assertEqual(config["scopes"], gdrive.DEFAULT_SCOPES)
        mode = os.stat(gdrive.client_config_path()).st_mode
        self.assertEqual(stat.S_IMODE(mode), 0o600)

    def test_environment_overrides_the_file(self):
        save_client_config("from-file", "file-secret")
        os.environ["FREECAD_XR_GDRIVE_CLIENT_ID"] = "from-env"
        self.addCleanup(os.environ.pop, "FREECAD_XR_GDRIVE_CLIENT_ID", None)
        self.assertEqual(load_client_config()["client_id"], "from-env")

    def test_google_console_json_layout_is_accepted(self):
        from xrsync.paths import write_json

        write_json(
            gdrive.client_config_path(),
            {"installed": {"client_id": "nested", "client_secret": "s"}},
        )
        self.assertEqual(load_client_config()["client_id"], "nested")

    def test_empty_client_id_is_refused(self):
        with self.assertRaises(NotConfiguredError):
            save_client_config("")
        with self.assertRaises(NotConfiguredError):
            gdrive.require_client_config()


class TokenStoreTest(GDriveTestCase):
    def test_stored_with_private_permissions(self):
        self.sign_in()
        mode = os.stat(gdrive.token_path()).st_mode
        self.assertEqual(stat.S_IMODE(mode), 0o600)

    def test_expiry_and_refresh_token_are_kept(self):
        store = self.sign_in(expires_in=3600)
        self.assertFalse(store.is_expired())
        store.update_from_response({"access_token": "new", "expires_in": 10})
        self.assertEqual(TokenStore().refresh_token, "refresh-token")
        self.assertEqual(TokenStore().access_token, "new")
        self.assertTrue(TokenStore().is_expired())

    def test_sign_out_forgets_everything(self):
        self.sign_in()
        self.assertTrue(account_status().signed_in)
        sign_out()
        self.assertFalse(account_status().signed_in)
        self.assertFalse(os.path.exists(gdrive.token_path()))

    def test_account_is_taken_from_the_id_token(self):
        import base64

        claims = base64.urlsafe_b64encode(
            json.dumps({"email": "user@example.com"}).encode("utf-8")
        ).decode("ascii").rstrip("=")
        store = TokenStore()
        store.update_from_response(
            {"access_token": "a", "expires_in": 60, "id_token": "h.%s.s" % claims}
        )
        self.assertEqual(TokenStore().account, "user@example.com")
        self.assertEqual(account_status().account, "user@example.com")

    def test_broken_id_token_is_ignored(self):
        store = TokenStore()
        store.update_from_response({"access_token": "a", "id_token": "rubbish"})
        self.assertIsNone(store.account)


# ---------------------------------------------------------------------------
# device flow
# ---------------------------------------------------------------------------


class DeviceFlowTest(GDriveTestCase):
    def setUp(self):
        super().setUp()
        self.config = self.configure()

    def test_start_shapes_the_request(self):
        self.http.queue(
            json_response(
                {
                    "device_code": "dev-code",
                    "user_code": "ABCD-EFGH",
                    "verification_url": "https://www.google.com/device",
                    "interval": 5,
                    "expires_in": 1800,
                }
            )
        )
        flow = DeviceCodeFlow()
        code = flow.start()
        request = self.http.last()
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["url"], gdrive.DEVICE_CODE_URL)
        form = self.http.form()
        self.assertEqual(form["client_id"], self.config["client_id"])
        self.assertIn("drive", form["scope"])
        self.assertEqual(code.user_code, "ABCD-EFGH")
        self.assertEqual(code.verification_url, "https://www.google.com/device")
        self.assertEqual(code.interval, 5)

    def test_start_accepts_verification_uri(self):
        self.http.queue(
            json_response({"device_code": "d", "user_code": "U",
                           "verification_uri": "https://g.co/x"})
        )
        self.assertEqual(DeviceCodeFlow().start().verification_url, "https://g.co/x")

    def test_start_rejects_a_reply_without_a_device_code(self):
        self.http.queue(json_response({"nothing": "here"}))
        with self.assertRaises(GDriveError):
            DeviceCodeFlow().start()

    def _started_flow(self, interval=5):
        self.http.queue(
            json_response({"device_code": "dev-code", "user_code": "U",
                           "verification_url": "https://g.co", "interval": interval,
                           "expires_in": 1800})
        )
        flow = DeviceCodeFlow()
        flow.start()
        return flow

    def test_poll_once_while_pending(self):
        flow = self._started_flow()
        self.http.queue(http_error(428, {"error": "authorization_pending"}))
        self.assertFalse(flow.poll_once())
        form = self.http.form()
        self.assertEqual(
            form["grant_type"], "urn:ietf:params:oauth:grant-type:device_code"
        )
        self.assertEqual(form["device_code"], "dev-code")
        self.assertEqual(form["client_id"], self.config["client_id"])
        self.assertFalse(TokenStore().has_tokens())

    def test_poll_once_backs_off_on_slow_down(self):
        flow = self._started_flow(interval=5)
        self.http.queue(http_error(403, {"error": "slow_down"}))
        self.assertFalse(flow.poll_once())
        self.assertEqual(flow.code.interval, 10)

    def test_poll_once_respects_the_interval(self):
        flow = self._started_flow()
        self.http.queue(http_error(428, {"error": "authorization_pending"}))
        self.assertFalse(flow.poll_once())
        # a second immediate poll must not hit the network at all
        self.assertFalse(flow.poll_once())
        self.assertEqual(len(self.http.requests), 2)

    def test_poll_once_stores_the_tokens(self):
        flow = self._started_flow()
        self.http.queue(
            json_response(
                {"access_token": "at", "refresh_token": "rt", "expires_in": 3599,
                 "scope": gdrive.DEFAULT_SCOPES, "token_type": "Bearer"}
            )
        )
        self.assertTrue(flow.poll_once())
        store = TokenStore()
        self.assertEqual(store.access_token, "at")
        self.assertEqual(store.refresh_token, "rt")
        self.assertFalse(store.is_expired())
        self.assertTrue(account_status().signed_in)

    def test_access_denied_is_raised(self):
        flow = self._started_flow()
        self.http.queue(http_error(403, {"error": "access_denied"}))
        with self.assertRaises(GDriveError) as caught:
            flow.poll_once()
        self.assertEqual(caught.exception.reason, "access_denied")

    def test_expired_device_code_is_raised(self):
        flow = self._started_flow()
        self.http.queue(http_error(400, {"error": "expired_token"}))
        with self.assertRaises(GDriveError):
            flow.poll_once()

    def test_poll_before_start_is_an_error(self):
        with self.assertRaises(GDriveError):
            DeviceCodeFlow().poll_once()

    def test_device_code_is_not_in_the_repr(self):
        flow = self._started_flow()
        self.assertNotIn("dev-code", repr(flow.code))
        self.assertIn("ABCD" if False else "U", repr(flow.code))

    def test_flow_needs_a_configured_client(self):
        os.unlink(gdrive.client_config_path())
        with self.assertRaises(NotConfiguredError):
            DeviceCodeFlow()


# ---------------------------------------------------------------------------
# authenticated client
# ---------------------------------------------------------------------------


class ClientAuthTest(GDriveTestCase):
    def setUp(self):
        super().setUp()
        self.configure()

    def test_from_stored_credentials_requires_a_client(self):
        os.unlink(gdrive.client_config_path())
        with self.assertRaises(NotConfiguredError):
            GoogleDriveClient.from_stored_credentials()

    def test_from_stored_credentials_requires_a_sign_in(self):
        with self.assertRaises(NotAuthenticatedError):
            GoogleDriveClient.from_stored_credentials()

    def test_valid_token_is_used_directly(self):
        self.sign_in()
        client = GoogleDriveClient.from_stored_credentials()
        self.http.queue(json_response({"files": []}))
        client.list_files()
        self.assertEqual(
            self.http.last()["headers"]["authorization"], "Bearer access-token"
        )

    def test_expired_token_is_refreshed_first(self):
        self.sign_in(expires_in=-10)
        client = GoogleDriveClient.from_stored_credentials()
        self.http.queue(
            json_response({"access_token": "fresh", "expires_in": 3600}),
            json_response({"files": []}),
        )
        client.list_files()
        self.assertEqual(len(self.http.requests), 2)
        self.assertEqual(self.http.requests[0]["url"], gdrive.TOKEN_URL)
        form = dict(
            urllib.parse.parse_qsl(self.http.requests[0]["body"].decode("utf-8"))
        )
        self.assertEqual(form["grant_type"], "refresh_token")
        self.assertEqual(form["refresh_token"], "refresh-token")
        self.assertEqual(
            self.http.requests[1]["headers"]["authorization"], "Bearer fresh"
        )
        self.assertEqual(TokenStore().access_token, "fresh")
        # the old refresh token survives a response that omits it
        self.assertEqual(TokenStore().refresh_token, "refresh-token")

    def test_revoked_refresh_token_reports_not_authenticated(self):
        self.sign_in(expires_in=-10)
        client = GoogleDriveClient.from_stored_credentials()
        self.http.queue(http_error(400, {"error": "invalid_grant"}))
        with self.assertRaises(NotAuthenticatedError):
            client.list_files()

    def test_expired_without_a_refresh_token(self):
        self.sign_in(expires_in=-10, refresh="")
        client = GoogleDriveClient(store=TokenStore())
        with self.assertRaises(NotAuthenticatedError):
            client.list_files()

    def test_offline_is_reported_clearly(self):
        self.sign_in()
        client = GoogleDriveClient.from_stored_credentials()
        self.http.queue(urllib.error.URLError("no route to host"))
        with self.assertRaises(GDriveOfflineError):
            client.list_files()

    def test_api_errors_carry_the_status(self):
        self.sign_in()
        client = GoogleDriveClient.from_stored_credentials()
        self.http.queue(
            http_error(404, {"error": {"code": 404, "message": "File not found",
                                       "errors": [{"reason": "notFound"}]}})
        )
        with self.assertRaises(GDriveError) as caught:
            client.metadata("nope")
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(caught.exception.reason, "notFound")
        self.assertIn("File not found", str(caught.exception))

    def test_invalid_json_is_reported(self):
        self.sign_in()
        client = GoogleDriveClient.from_stored_credentials()
        self.http.queue(FakeResponse(b"<html>nope</html>"))
        with self.assertRaises(GDriveError):
            client.list_files()

    def test_tokens_are_never_logged(self):
        self.sign_in(expires_in=-10)
        client = GoogleDriveClient.from_stored_credentials()
        self.http.queue(
            json_response({"access_token": "sup3rs3cret", "expires_in": 3600}),
            http_error(500, {"error": {"message": "boom"}}),
        )
        with self.assertLogs("xrsync.gdrive", level="DEBUG") as logs:
            with self.assertRaises(GDriveError):
                client.list_files()
        blob = "\n".join(logs.output)
        self.assertNotIn("sup3rs3cret", blob)
        self.assertNotIn("refresh-token", blob)


# ---------------------------------------------------------------------------
# file operations
# ---------------------------------------------------------------------------


FILE_PAYLOAD = {
    "id": "file-1",
    "name": "Bracket.FCStd",
    "mimeType": "application/octet-stream",
    "size": "2048",
    "modifiedTime": "2026-09-03T10:00:00.000Z",
    "md5Checksum": "abc123",
    "parents": ["folder-1"],
}
FOLDER_PAYLOAD = {
    "id": "folder-1",
    "name": "CAD",
    "mimeType": gdrive.FOLDER_MIME,
    "modifiedTime": "2026-09-01T10:00:00.000Z",
}


class FileOperationTest(GDriveTestCase):
    def setUp(self):
        super().setUp()
        self.configure()
        self.sign_in()
        self.client = GoogleDriveClient.from_stored_credentials()

    # -- listing -----------------------------------------------------------

    def test_list_files_filters_to_our_extensions(self):
        self.http.queue(json_response({"files": [FILE_PAYLOAD, FOLDER_PAYLOAD]}))
        entries = self.client.list_files()
        query = self.http.query()
        self.assertEqual(self.http.path(), "/drive/v3/files")
        self.assertIn("trashed = false", query["q"])
        self.assertIn("name contains '.FCStd'", query["q"])
        self.assertIn("name contains '.fcxr'", query["q"])
        self.assertIn(gdrive.FOLDER_MIME, query["q"])
        self.assertIn("md5Checksum", query["fields"])
        self.assertEqual([e.name for e in entries], ["Bracket.FCStd", "CAD"])
        self.assertEqual(entries[0].file_id, "file-1")
        self.assertEqual(entries[0].size, 2048)
        self.assertEqual(entries[0].modified_time, "2026-09-03T10:00:00.000Z")
        self.assertEqual(entries[0].md5_checksum, "abc123")
        self.assertFalse(entries[0].is_folder)
        self.assertTrue(entries[1].is_folder)

    def test_list_children_scopes_to_the_parent(self):
        self.http.queue(json_response({"files": [FILE_PAYLOAD]}))
        self.client.list_children("folder-1")
        self.assertIn("'folder-1' in parents", self.http.query()["q"])

    def test_list_files_paginates(self):
        self.http.queue(
            json_response({"files": [FILE_PAYLOAD], "nextPageToken": "page-2"}),
            json_response({"files": [FOLDER_PAYLOAD]}),
        )
        entries = self.client.list_files()
        self.assertEqual(len(entries), 2)
        self.assertEqual(self.http.query(1)["pageToken"], "page-2")

    def test_max_results_stops_early(self):
        self.http.queue(
            json_response({"files": [FILE_PAYLOAD, FOLDER_PAYLOAD],
                           "nextPageToken": "page-2"})
        )
        self.assertEqual(len(self.client.list_files(max_results=1)), 1)

    def test_quotes_in_names_are_escaped(self):
        self.http.queue(json_response({"files": []}))
        self.client.search("Bob's part")
        self.assertIn("Bob\\'s part", self.http.query()["q"])

    def test_metadata(self):
        self.http.queue(json_response(FILE_PAYLOAD))
        entry = self.client.metadata("file-1")
        self.assertEqual(self.http.path(), "/drive/v3/files/file-1")
        self.assertEqual(entry.name, "Bracket.FCStd")
        self.assertEqual(entry.extension, ".FCStd")

    # -- download ----------------------------------------------------------

    def test_download_uses_alt_media(self):
        self.http.queue(FakeResponse(b"FCXR-binary-body"))
        data = self.client.download("file-1")
        self.assertEqual(data, b"FCXR-binary-body")
        self.assertEqual(self.http.query()["alt"], "media")
        self.assertEqual(self.http.path(), "/drive/v3/files/file-1")
        self.assertEqual(self.http.last()["method"], "GET")

    def test_download_to_writes_the_file(self):
        self.http.queue(FakeResponse(b"payload"))
        target = os.path.join(self.home.name, "sub", "out.bin")
        self.client.download_to("file-1", target)
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), b"payload")
        self.assertFalse(os.path.exists(target + ".part"))

    # -- upload ------------------------------------------------------------

    def test_upload_is_resumable(self):
        self.http.queue(
            FakeResponse(b"", {"Location": "https://upload.example/session-1"}),
            json_response(FILE_PAYLOAD),
        )
        entry = self.client.upload("Bracket.FCStd", b"binary", parent="folder-1")

        start = self.http.requests[0]
        self.assertEqual(start["method"], "POST")
        self.assertEqual(self.http.query(0)["uploadType"], "resumable")
        self.assertEqual(json.loads(start["body"].decode("utf-8")),
                         {"name": "Bracket.FCStd", "parents": ["folder-1"]})
        self.assertEqual(start["headers"]["x-upload-content-length"], "6")
        self.assertEqual(start["headers"]["x-upload-content-type"], gdrive.FCSTD_MIME)

        put = self.http.requests[1]
        self.assertEqual(put["method"], "PUT")
        self.assertEqual(put["url"], "https://upload.example/session-1")
        self.assertEqual(put["body"], b"binary")
        self.assertEqual(entry.file_id, "file-1")

    def test_upload_guesses_the_fcxr_mime_type(self):
        self.http.queue(
            FakeResponse(b"", {"Location": "https://upload.example/s"}),
            json_response(dict(FILE_PAYLOAD, name="Scene.fcxr")),
        )
        self.client.upload("Scene.fcxr", b"x")
        self.assertEqual(
            self.http.requests[0]["headers"]["x-upload-content-type"], gdrive.FCXR_MIME
        )

    def test_upload_without_a_session_url_fails(self):
        self.http.queue(FakeResponse(b"", {}))
        with self.assertRaises(GDriveError):
            self.client.upload("x.fcxr", b"x")

    def test_update_patches_the_existing_file(self):
        self.http.queue(
            FakeResponse(b"", {"Location": "https://upload.example/session-2"}),
            json_response(FILE_PAYLOAD),
        )
        entry = self.client.update("file-1", b"new bytes", name="Bracket.FCStd")
        self.assertEqual(self.http.requests[0]["method"], "PATCH")
        self.assertIn("/upload/drive/v3/files/file-1", self.http.requests[0]["url"])
        self.assertEqual(self.http.requests[1]["body"], b"new bytes")
        self.assertEqual(entry.md5_checksum, "abc123")

    # -- folders -----------------------------------------------------------

    def test_ensure_folder_returns_an_existing_folder(self):
        self.http.queue(json_response({"files": [FOLDER_PAYLOAD]}))
        entry = self.client.ensure_folder("CAD")
        self.assertEqual(len(self.http.requests), 1)
        self.assertEqual(entry.file_id, "folder-1")
        self.assertTrue(entry.is_folder)
        query = self.http.query()["q"]
        self.assertIn("name = 'CAD'", query)
        self.assertIn("'root' in parents", query)

    def test_ensure_folder_creates_a_missing_folder(self):
        self.http.queue(
            json_response({"files": []}),
            json_response(dict(FOLDER_PAYLOAD, id="folder-9", name="XR")),
        )
        entry = self.client.ensure_folder("XR", parent="folder-1")
        create = self.http.requests[1]
        self.assertEqual(create["method"], "POST")
        self.assertEqual(
            json.loads(create["body"].decode("utf-8")),
            {"name": "XR", "mimeType": gdrive.FOLDER_MIME, "parents": ["folder-1"]},
        )
        self.assertEqual(entry.file_id, "folder-9")

    def test_delete_only_trashes(self):
        self.http.queue(json_response({"id": "file-1"}))
        self.client.delete("file-1")
        self.assertEqual(self.http.last()["method"], "PATCH")
        self.assertEqual(json.loads(self.http.last()["body"].decode("utf-8")),
                         {"trashed": True})

    def test_about_caches_the_account(self):
        self.http.queue(
            json_response({"user": {"emailAddress": "cad@example.com"}})
        )
        self.client.about()
        self.assertEqual(TokenStore().account, "cad@example.com")


# ---------------------------------------------------------------------------
# the sync façade
# ---------------------------------------------------------------------------


class FakeDriveClient:
    """A GoogleDriveClient stand-in for the GoogleDriveSync tests."""

    def __init__(self, entry, content=b"remote-bytes"):
        self.entry = entry
        self.content = content
        self.uploads = []
        self.updates = []

    def metadata(self, file_id):
        return self.entry

    def download_to(self, file_id, path):
        with open(path, "wb") as handle:
            handle.write(self.content)
        return path

    def upload(self, name, data, parent=None, mime=None):
        self.uploads.append((name, data, parent))
        return DriveEntry(file_id="new-file", name=name, size=len(data),
                          md5_checksum="new-md5", modified_time="2026-09-04T00:00:00Z")

    def update(self, file_id, data, name=None, mime=None):
        self.updates.append((file_id, data, name))
        self.entry = DriveEntry(
            file_id=file_id, name=name or self.entry.name, size=len(data),
            md5_checksum="pushed-md5", modified_time="2026-09-05T00:00:00Z",
        )
        return self.entry


def remote_entry(md5="abc123", modified="2026-09-03T10:00:00Z"):
    return DriveEntry(file_id="file-1", name="Bracket.FCStd", size=7,
                      md5_checksum=md5, modified_time=modified)


class DriveSyncTest(GDriveTestCase):
    def setUp(self):
        super().setUp()
        self.configure()
        self.sign_in()
        self.client = FakeDriveClient(remote_entry())
        self.sync = GoogleDriveSync(client=self.client)

    def test_pull_caches_the_file_and_records_its_state(self):
        path = self.sync.pull("file-1")
        self.assertTrue(path.startswith(self.sync.directory))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"remote-bytes")
        record = self.sync.record("file-1")
        self.assertEqual(record.md5_checksum, "abc123")
        self.assertEqual(record.modified_time, "2026-09-03T10:00:00Z")
        self.assertTrue(os.path.exists(self.sync.state_path))
        self.assertEqual(GoogleDriveSync(client=self.client).record("file-1").name,
                         "Bracket.FCStd")

    def test_pull_skips_an_unchanged_file(self):
        path = self.sync.pull("file-1")
        with open(path, "wb") as handle:
            handle.write(b"sentinel")
        self.sync.pull("file-1")
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"sentinel")
        self.sync.pull("file-1", force=True)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"remote-bytes")

    def test_pull_downloads_again_when_the_remote_changed(self):
        path = self.sync.pull("file-1")
        with open(path, "wb") as handle:
            handle.write(b"stale")
        self.client.entry = remote_entry(md5="different")
        self.client.content = b"newer-bytes"
        self.sync.pull("file-1")
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"newer-bytes")

    def _local_file(self, content=b"local-bytes"):
        path = os.path.join(self.home.name, "Bracket.FCStd")
        with open(path, "wb") as handle:
            handle.write(content)
        return path

    def test_push_creates_a_new_file(self):
        entry = self.sync.push(self._local_file())
        self.assertEqual(entry.file_id, "new-file")
        self.assertEqual(self.client.uploads[0][1], b"local-bytes")
        self.assertEqual(self.sync.record("new-file").md5_checksum, "new-md5")

    def test_push_updates_a_known_file(self):
        path = self.sync.pull("file-1")
        entry = self.sync.push(path, file_id="file-1")
        self.assertEqual(self.client.updates[0][0], "file-1")
        self.assertEqual(entry.md5_checksum, "pushed-md5")
        self.assertEqual(self.sync.record("file-1").md5_checksum, "pushed-md5")

    def test_push_refuses_to_clobber_a_changed_remote(self):
        path = self.sync.pull("file-1")
        self.client.entry = remote_entry(md5="changed-elsewhere")
        with self.assertRaises(ConflictError) as caught:
            self.sync.push(path, file_id="file-1")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.client.updates, [])
        self.assertIsNotNone(caught.exception.entry)

    def test_push_with_force_overrides_the_conflict(self):
        path = self.sync.pull("file-1")
        self.client.entry = remote_entry(md5="changed-elsewhere")
        entry = self.sync.push(path, file_id="file-1", force=True)
        self.assertEqual(entry.md5_checksum, "pushed-md5")

    def test_push_needs_an_existing_file(self):
        with self.assertRaises(GDriveError):
            self.sync.push(os.path.join(self.home.name, "missing.FCStd"))

    def test_status_transitions(self):
        self.assertEqual(self.sync.status("unknown-id"), "unknown")
        path = self.sync.pull("file-1")
        self.assertEqual(self.sync.status("file-1"), "in_sync")

        with open(path, "ab") as handle:
            handle.write(b"local edit")
        self.assertEqual(self.sync.status("file-1"), "local_changes")

        self.sync.pull("file-1", force=True)
        self.client.entry = remote_entry(md5="remote-edit")
        self.assertEqual(self.sync.status("file-1"), "remote_changes")

        with open(path, "ab") as handle:
            handle.write(b"local edit too")
        self.assertEqual(self.sync.status("file-1"), "conflict")

    def test_status_when_offline(self):
        self.sync.pull("file-1")

        def boom(file_id):
            raise GDriveOfflineError("no network")

        self.client.metadata = boom
        self.assertEqual(self.sync.status("file-1"), "offline")

    def test_forget(self):
        self.sync.pull("file-1")
        self.sync.forget("file-1")
        self.assertIsNone(self.sync.record("file-1"))


class HelperTest(unittest.TestCase):
    def test_extension_filter(self):
        clause = gdrive.extension_filter(include_folders=False)
        self.assertEqual(clause,
                         "(name contains '.FCStd' or name contains '.fcxr')")

    def test_guess_mime(self):
        self.assertEqual(gdrive.guess_mime("a.FCStd"), gdrive.FCSTD_MIME)
        self.assertEqual(gdrive.guess_mime("a.fcxr"), gdrive.FCXR_MIME)
        self.assertEqual(gdrive.guess_mime("a.weird"), "application/octet-stream")

    def test_redaction(self):
        redacted = gdrive._redact(
            "https://x/token?access_token=abc&refresh_token=def&other=1"
        )
        self.assertNotIn("abc", redacted)
        self.assertNotIn("def", redacted)
        self.assertIn("other=1", redacted)

    def test_drive_entry_from_api_is_lenient(self):
        entry = DriveEntry.from_api({"id": "x", "name": "n", "size": "not a number"})
        self.assertEqual(entry.size, 0)
        self.assertFalse(entry.is_folder)
        self.assertEqual(entry.to_dict()["file_id"], "x")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
