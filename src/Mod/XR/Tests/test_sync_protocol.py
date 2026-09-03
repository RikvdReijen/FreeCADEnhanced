# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the XR sync protocol, server and client (ARCHITECTURE.md §3).

Everything runs on loopback with no FreeCAD: the server is driven through a
stub :class:`DocumentBridge` that serves a synthetic FCXR package.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xrsync import protocol as P  # noqa: E402
from xrsync.client import AuthError, HttpError, SyncClient, TransportError  # noqa: E402
from xrsync.fcxr import FcxrWriter, content_hash, read  # noqa: E402
from xrsync.server import (  # noqa: E402
    BridgeError,
    DeviceRegistry,
    DocumentBridge,
    EventLog,
    MarshalledBridge,
    SceneCache,
    SyncServer,
)


# ---------------------------------------------------------------------------
# pure protocol
# ---------------------------------------------------------------------------


class ConstantsTest(unittest.TestCase):
    def test_ports_and_paths_match_the_architecture(self):
        self.assertEqual(P.PROTOCOL_VERSION, 1)
        self.assertEqual(P.DEFAULT_PORT, 47810)
        self.assertEqual(P.DISCOVERY_PORT, 47811)
        self.assertEqual(P.EP_HELLO, "/api/v1/hello")
        self.assertEqual(P.EP_PAIR, "/api/v1/pair")
        self.assertEqual(P.EP_DOCUMENTS, "/api/v1/documents")
        self.assertEqual(P.EP_SCENE, "/api/v1/scene")
        self.assertEqual(P.EP_SCENE_HASH, "/api/v1/scene/hash")
        self.assertEqual(P.EP_EVENTS, "/api/v1/events")
        self.assertEqual(P.EP_ENVIRONMENTS, "/api/v1/environments")
        self.assertEqual(P.EP_ENVIRONMENT, "/api/v1/environment")
        self.assertEqual(P.EP_PAINT, "/api/v1/paint")
        self.assertEqual(P.EP_VECTOR, "/api/v1/vector")
        self.assertEqual(P.EP_THUMBNAIL, "/api/v1/thumbnail")
        self.assertEqual(P.CONTENT_TYPE_FCXR, "application/x-fcxr")

    def test_only_hello_and_pair_are_public(self):
        self.assertEqual(P.PUBLIC_ENDPOINTS, frozenset({P.EP_HELLO, P.EP_PAIR}))

    def test_path_builders(self):
        self.assertEqual(P.scene_path("Doc", 2), "/api/v1/scene?doc=Doc&lod=2")
        self.assertEqual(P.scene_hash_path("Doc"), "/api/v1/scene/hash?doc=Doc")
        self.assertEqual(P.events_path(7), "/api/v1/events?since=7")
        self.assertEqual(P.environment_path("bambu_x1c"),
                         "/api/v1/environment?id=bambu_x1c")

    def test_lod_clamping(self):
        self.assertEqual(P.clamp_lod(-3), 0)
        self.assertEqual(P.clamp_lod(9), 3)
        self.assertEqual(P.clamp_lod("2"), 2)
        self.assertEqual(P.clamp_lod(None), P.DEFAULT_LOD)
        self.assertEqual(P.clamp_lod("nonsense"), P.DEFAULT_LOD)


class MessageCodecTest(unittest.TestCase):
    def test_hello_round_trip(self):
        hello = P.HelloResponse(name="desk", id="abc", auth_required=True,
                                features=["fcxr", "paint"], port=47810)
        self.assertEqual(P.HelloResponse.from_json(hello.to_json()), hello)

    def test_nested_lists_round_trip(self):
        response = P.DocumentsResponse(
            documents=[
                P.DocumentInfo(name="A", label="Part A", hash="1234", object_count=3),
                P.DocumentInfo(name="B", label="Part B", hash="5678"),
            ],
            active="A",
        )
        decoded = P.DocumentsResponse.from_json(response.to_json())
        self.assertEqual(decoded, response)
        self.assertIsInstance(decoded.documents[0], P.DocumentInfo)

    def test_events_round_trip(self):
        response = P.EventsResponse(
            events=[P.Event(seq=4, type=P.EVENT_DOC_CHANGED, doc="A",
                            data={"source": "paint"})],
            last_seq=4,
        )
        decoded = P.EventsResponse.from_json(response.to_json())
        self.assertEqual(decoded.events[0].data, {"source": "paint"})
        self.assertEqual(decoded.last_seq, 4)

    def test_unknown_fields_are_ignored(self):
        decoded = P.DocumentInfo.from_dict(
            {"name": "A", "label": "A", "hash": "x", "future_field": 42}
        )
        self.assertEqual(decoded.name, "A")

    def test_encoding_is_deterministic(self):
        first = P.HelloResponse(name="a", features=["x", "y"]).to_json()
        second = P.HelloResponse(name="a", features=["x", "y"]).to_json()
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first)["name"], "a")

    def test_bad_payloads_raise_protocol_errors(self):
        with self.assertRaises(P.ProtocolError):
            P.HelloResponse.from_json("{not json}")
        with self.assertRaises(P.ProtocolError):
            P.HelloResponse.from_dict([1, 2, 3])
        with self.assertRaises(P.ProtocolError):
            P.decode_json(b"\xff\xfe")
        with self.assertRaises(P.ProtocolError):
            P.encode_json({"x": float("nan")})


class TokenAndPairingTest(unittest.TestCase):
    def test_tokens_are_unique_and_well_shaped(self):
        tokens = {P.generate_token() for _ in range(50)}
        self.assertEqual(len(tokens), 50)
        for token in tokens:
            self.assertTrue(P.is_valid_token(token))
        with self.assertRaises(P.ProtocolError):
            P.generate_token(4)

    def test_pairing_codes_are_six_digits(self):
        for _ in range(100):
            code = P.generate_pairing_code()
            self.assertEqual(len(code), 6)
            self.assertTrue(code.isdigit())
            self.assertTrue(P.is_valid_pairing_code(code))

    def test_pairing_codes_keep_leading_zeros(self):
        self.assertTrue(P.is_valid_pairing_code("000123"))
        self.assertFalse(P.is_valid_pairing_code("123"))
        self.assertFalse(P.is_valid_pairing_code("12345a"))
        self.assertFalse(P.is_valid_pairing_code(123456))
        self.assertFalse(P.is_valid_pairing_code(None))

    def test_pairing_code_comparison(self):
        self.assertTrue(P.check_pairing_code("012345", "012345"))
        self.assertFalse(P.check_pairing_code("012345", "012346"))
        self.assertFalse(P.check_pairing_code(None, "012345"))
        self.assertFalse(P.check_pairing_code("012345", None))
        self.assertFalse(P.check_pairing_code("12345", "12345"))

    def test_pair_request_validation(self):
        P.PairRequest(code="123456", device="Quest 3").validate()
        with self.assertRaises(P.ProtocolError):
            P.PairRequest(code="12", device="Quest 3").validate()
        with self.assertRaises(P.ProtocolError):
            P.PairRequest(code="123456", device="").validate()

    def test_bearer_header_parsing(self):
        self.assertEqual(P.parse_bearer("Bearer abc"), "abc")
        self.assertEqual(P.parse_bearer("bearer   abc  "), "abc")
        self.assertIsNone(P.parse_bearer("Basic abc"))
        self.assertIsNone(P.parse_bearer("Bearer"))
        self.assertIsNone(P.parse_bearer(None))
        self.assertEqual(P.auth_header("t"), {"Authorization": "Bearer t"})


class BeaconTest(unittest.TestCase):
    def test_request_round_trip(self):
        self.assertEqual(P.encode_discovery_request(), b"FCXR-DISCOVER?v=1")
        self.assertEqual(P.parse_discovery_request(b"FCXR-DISCOVER?v=1"), 1)
        self.assertEqual(P.parse_discovery_request("FCXR-DISCOVER?v=2\n"), 2)

    def test_offer_round_trip(self):
        offer = P.encode_discovery_offer("desktop", 47810, "abcd-1234")
        self.assertEqual(offer, b"FCXR-OFFER v=1 name=desktop port=47810 id=abcd-1234")
        parsed = P.parse_discovery_offer(offer, address="192.168.1.7")
        self.assertEqual(parsed.name, "desktop")
        self.assertEqual(parsed.port, 47810)
        self.assertEqual(parsed.id, "abcd-1234")
        self.assertEqual(parsed.version, 1)
        self.assertEqual(parsed.base_url, "http://192.168.1.7:47810")

    def test_names_with_spaces_are_sanitised(self):
        offer = P.encode_discovery_offer("my laptop", 1234, "id 1")
        parsed = P.parse_discovery_offer(offer)
        self.assertEqual(parsed.name, "my-laptop")
        self.assertEqual(parsed.id, "id-1")

    def test_malformed_beacons_are_rejected(self):
        for junk in (b"", b"hello", b"FCXR-OFFER", b"FCXR-DISCOVER",
                     b"FCXR-OFFER v=1 name=x", b"FCXR-OFFER v=1 port=abc",
                     b"FCXR-OFFER v=1 port=99999", b"\xff\xfe\xfd"):
            with self.assertRaises(P.ProtocolError):
                if junk.startswith(b"FCXR-OFFER"):
                    P.parse_discovery_offer(junk)
                else:
                    P.parse_discovery_request(junk)
        with self.assertRaises(P.ProtocolError):
            P.parse_discovery_offer(b"x" * (P.MAX_BEACON_SIZE + 1))

    def test_unknown_extra_fields_are_tolerated(self):
        parsed = P.parse_discovery_offer(
            b"FCXR-OFFER v=1 name=a port=1 id=b future=42"
        )
        self.assertEqual(parsed.port, 1)


# ---------------------------------------------------------------------------
# server building blocks
# ---------------------------------------------------------------------------


class EventLogTest(unittest.TestCase):
    def test_sequence_numbers_increase(self):
        log = EventLog()
        first = log.publish(P.EVENT_DOC_CHANGED, doc="A")
        second = log.publish(P.EVENT_DOC_CHANGED, doc="B")
        self.assertEqual((first.seq, second.seq), (1, 2))
        self.assertEqual([e.seq for e in log.since(1)], [2])

    def test_wait_returns_immediately_when_events_are_pending(self):
        log = EventLog()
        log.publish(P.EVENT_PING)
        started = time.monotonic()
        events, last = log.wait(0, timeout=5.0)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(events), 1)
        self.assertEqual(last, 1)

    def test_wait_times_out(self):
        log = EventLog()
        events, last = log.wait(0, timeout=0.05)
        self.assertEqual(events, [])
        self.assertEqual(last, 0)

    def test_wait_wakes_on_publish(self):
        log = EventLog()
        threading.Timer(0.1, lambda: log.publish(P.EVENT_DOC_CHANGED, "A")).start()
        events, _ = log.wait(0, timeout=5.0)
        self.assertEqual(len(events), 1)


class SceneCacheTest(unittest.TestCase):
    def test_lru_eviction(self):
        cache = SceneCache(maxsize=2)
        cache.put(("a", 1, "h"), b"A")
        cache.put(("b", 1, "h"), b"B")
        self.assertEqual(cache.get(("a", 1, "h")), b"A")  # refresh a
        cache.put(("c", 1, "h"), b"C")
        self.assertIsNone(cache.get(("b", 1, "h")))
        self.assertEqual(cache.get(("a", 1, "h")), b"A")
        self.assertEqual(len(cache), 2)

    def test_hash_is_part_of_the_key(self):
        cache = SceneCache()
        cache.put(("a", 1, "h1"), b"A")
        self.assertIsNone(cache.get(("a", 1, "h2")))


class DeviceRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "paired_devices.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_verify_revoke_and_persist(self):
        registry = DeviceRegistry(self.path)
        token = registry.add("Quest 3")
        self.assertTrue(registry.verify(token))
        self.assertFalse(registry.verify("nope"))
        self.assertFalse(registry.verify(None))
        self.assertEqual(len(registry), 1)

        reloaded = DeviceRegistry(self.path)
        self.assertTrue(reloaded.verify(token))
        self.assertEqual(reloaded.devices()[0]["device"], "Quest 3")
        self.assertNotIn("token", reloaded.devices()[0])

        self.assertTrue(reloaded.revoke(token))
        self.assertFalse(reloaded.revoke(token))
        self.assertFalse(DeviceRegistry(self.path).verify(token))

    def test_file_is_private(self):
        registry = DeviceRegistry(self.path)
        registry.add("Quest 3")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_broken_file_is_ignored(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{{{ not json")
        self.assertEqual(len(DeviceRegistry(self.path)), 0)


class MarshalledBridgeTest(unittest.TestCase):
    def test_every_call_goes_through_the_dispatcher(self):
        calls = []

        class Inner(DocumentBridge):
            def list_documents(self):
                return [{"name": "A", "label": "A", "hash": "h"}]

            def scene_hash(self, doc):
                return "h"

            def export_scene(self, doc, lod):
                return b"FCXR"

        bridge = MarshalledBridge(Inner(), lambda fn: (calls.append(1), fn())[1])
        self.assertEqual(bridge.scene_hash("A"), "h")
        self.assertEqual(bridge.export_scene("A", 1), b"FCXR")
        self.assertEqual(len(bridge.list_documents()), 1)
        self.assertEqual(len(calls), 3)


# ---------------------------------------------------------------------------
# end to end
# ---------------------------------------------------------------------------


def synthetic_scene(name="Body", size=1.0):
    writer = FcxrWriter(source_document="%s.FCStd" % name)
    material = writer.add_material("Steel", [0.5, 0.5, 0.55, 1.0], 0.9, 0.3)
    mesh = writer.add_mesh(
        name,
        positions=[(0, 0, 0), (size, 0, 0), (0, size, 0)],
        normals=[(0, 0, 1)] * 3,
        indices=[(0, 1, 2)],
        material=material,
    )
    root = writer.add_node(name, mesh=mesh, fc_name=name)
    writer.set_scene(root=root, environment="bambu_x1c", user_scale=12.0)
    return writer.to_bytes()


class StubBridge(DocumentBridge):
    """A DocumentBridge that serves a synthetic package, no FreeCAD involved."""

    def __init__(self):
        self.scenes = {"Part": synthetic_scene("Part"), "Other": synthetic_scene("Other")}
        self.hashes = {name: content_hash(data) for name, data in self.scenes.items()}
        self.export_calls = 0
        self.paint_calls = []
        self.vector_calls = []
        self.environments = [
            {"id": "bambu_x1c", "name": "Bambu Lab X1C", "description": "chamber",
             "user_scale": 12.0}
        ]
        self.specs = {"bambu_x1c": {"id": "bambu_x1c", "version": 1,
                                    "user_scale": 12.0, "nodes": []}}
        self.png = b"\x89PNG\r\n\x1a\n-thumbnail-"

    def list_documents(self):
        return [
            {"name": name, "label": name, "hash": self.hashes[name],
             "path": "/tmp/%s.FCStd" % name, "touched": False, "object_count": 1}
            for name in sorted(self.scenes)
        ]

    def default_document(self):
        return "Part"

    def scene_hash(self, doc):
        name = doc or self.default_document()
        if name not in self.scenes:
            raise BridgeError("no such document: %s" % name, 404)
        return self.hashes[name]

    def export_scene(self, doc, lod):
        name = doc or self.default_document()
        if name not in self.scenes:
            raise BridgeError("no such document: %s" % name, 404)
        self.export_calls += 1
        return self.scenes[name]

    def thumbnail(self, doc):
        return self.png if (doc or "Part") == "Part" else None

    def apply_paint(self, data, doc=None):
        package = read(data)
        self.paint_calls.append((doc, package))
        return {"ok": True, "doc": doc or "Part",
                "applied": len(package.paint.get("targets", [])),
                "message": "applied"}

    def apply_vector(self, vector, doc=None):
        self.vector_calls.append((doc, vector))
        return {"ok": True, "doc": doc or "Part",
                "applied": len(vector.get("paths", [])), "message": "applied"}

    def list_environments(self):
        return list(self.environments)

    def get_environment(self, env_id):
        return self.specs.get(env_id)

    def state(self):
        return {"environment": "bambu_x1c", "scale": 12.0}

    # test helper
    def touch(self, doc):
        self.scenes[doc] = synthetic_scene(doc, size=2.0)
        self.hashes[doc] = content_hash(self.scenes[doc])


class ServerClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.bridge = StubBridge()
        self.devices_path = os.path.join(
            self.tmp.name, "devices-%d.json" % id(self)
        )
        self.server = SyncServer(
            port=0,
            bridge=self.bridge,
            host="127.0.0.1",
            name="test-host",
            devices_path=self.devices_path,
            discovery=False,
        )
        self.server.start()
        self.addCleanup(self.server.stop)
        self.client = SyncClient("127.0.0.1", self.server.port, timeout=10.0)
        self.addCleanup(self.client.close)

    def pair(self):
        code, expires_in = self.server.begin_pairing()
        self.assertGreater(expires_in, 0)
        response = self.client.pair(code, "Quest 3")
        return response

    # -- lifecycle ---------------------------------------------------------

    def test_server_reports_its_bound_port(self):
        self.assertTrue(self.server.is_running())
        self.assertNotEqual(self.server.port, 0)
        self.assertIn(":%d" % self.server.port, self.server.url)
        self.assertTrue(all(u.startswith("http://") for u in self.server.urls()))

    def test_stop_is_idempotent_and_releases_the_port(self):
        port = self.server.port
        self.server.stop()
        self.server.stop()
        self.assertFalse(self.server.is_running())
        with self.assertRaises(TransportError):
            SyncClient("127.0.0.1", port, timeout=1.0).hello()

    # -- auth --------------------------------------------------------------

    def test_hello_needs_no_token(self):
        hello = self.client.hello()
        self.assertEqual(hello.protocol, P.PROTOCOL_VERSION)
        self.assertEqual(hello.name, "test-host")
        self.assertTrue(hello.auth_required)
        self.assertFalse(hello.paired)
        self.assertEqual(hello.port, self.server.port)
        self.assertIn("fcxr", hello.features)

    def test_protected_endpoints_reject_missing_and_bad_tokens(self):
        for call in (
            lambda: self.client.documents(),
            lambda: self.client.scene("Part"),
            lambda: self.client.scene_hash("Part"),
            lambda: self.client.events(0, 0.1),
            lambda: self.client.environments(),
            lambda: self.client.push_vector({"version": 1, "paths": []}),
        ):
            with self.assertRaises(AuthError) as caught:
                call()
            self.assertEqual(caught.exception.status, 401)
            self.client.close()  # the server drops unauthenticated connections
        self.client.token = "not-a-real-token"
        with self.assertRaises(AuthError):
            self.client.documents()

    def test_pairing_flow(self):
        self.assertFalse(self.server.pairing_active())
        code, _ = self.server.begin_pairing()
        self.assertTrue(self.server.pairing_active())
        self.assertFalse(self.server.pairing_completed())

        wrong = "000000" if code != "000000" else "111111"
        with self.assertRaises(HttpError) as caught:
            self.client.pair(wrong, "Impostor")
        self.assertEqual(caught.exception.status, 403)
        self.assertFalse(self.server.pairing_completed())

        response = self.client.pair(code, "Quest 3")
        self.assertTrue(response.token)
        self.assertEqual(response.device, "Quest 3")
        self.assertEqual(response.server_id, self.server.server_id)
        self.assertTrue(self.server.pairing_completed())
        self.assertEqual(self.server.paired_device, "Quest 3")
        self.assertTrue(self.client.hello().paired)

        # the code is single use
        second = SyncClient("127.0.0.1", self.server.port)
        self.addCleanup(second.close)
        with self.assertRaises(HttpError):
            second.pair(code, "Second")

    def test_pairing_can_be_cancelled(self):
        code, _ = self.server.begin_pairing()
        self.server.cancel_pairing()
        self.assertFalse(self.server.pairing_active())
        with self.assertRaises(HttpError):
            self.client.pair(code, "Quest 3")

    def test_expired_pairing_code_is_refused(self):
        code, _ = self.server.begin_pairing(timeout=0.0)
        with self.assertRaises(HttpError):
            self.client.pair(code, "Quest 3")

    def test_malformed_pair_body(self):
        status, _, _ = self.client.request(
            "POST", P.EP_PAIR, body=b"{{{", content_type=P.CONTENT_TYPE_JSON,
            authenticate=False,
        )
        self.assertEqual(status, 400)

    def test_token_survives_a_server_restart(self):
        response = self.pair()
        self.server.stop()
        restarted = SyncServer(
            port=0, bridge=self.bridge, host="127.0.0.1",
            devices_path=self.devices_path, discovery=False,
        )
        restarted.start()
        self.addCleanup(restarted.stop)
        client = SyncClient("127.0.0.1", restarted.port, token=response.token)
        self.addCleanup(client.close)
        self.assertEqual(len(client.documents()), 2)

    # -- documents and scenes ---------------------------------------------

    def test_documents(self):
        self.pair()
        response = self.client.documents_response()
        self.assertEqual([d.name for d in response.documents], ["Other", "Part"])
        self.assertEqual(response.active, "Part")
        self.assertEqual(response.documents[1].hash, self.bridge.hashes["Part"])

    def test_scene_fetch_and_parse(self):
        self.pair()
        data = self.client.scene("Part", lod=2)
        self.assertEqual(data, self.bridge.scenes["Part"])
        document = read(data)
        self.assertEqual(document.scene["environment"], "bambu_x1c")
        self.assertEqual(document.nodes[0]["fc_name"], "Part")

    def test_scene_defaults_to_the_active_document(self):
        self.pair()
        self.assertEqual(self.client.scene(), self.bridge.scenes["Part"])

    def test_scene_is_cached_until_the_hash_changes(self):
        self.pair()
        self.client.scene("Part", lod=1)
        self.client.scene("Part", lod=1)
        self.assertEqual(self.bridge.export_calls, 1)
        self.assertEqual(self.server.cache.hits, 1)

        self.client.scene("Part", lod=3)  # a different lod is a different entry
        self.assertEqual(self.bridge.export_calls, 2)

        self.bridge.touch("Part")
        self.client.scene("Part", lod=1)
        self.assertEqual(self.bridge.export_calls, 3)

    def test_scene_hash_endpoint(self):
        self.pair()
        self.assertEqual(self.client.scene_hash("Part"), self.bridge.hashes["Part"])
        self.bridge.touch("Part")
        self.assertEqual(self.client.scene_hash("Part"), self.bridge.hashes["Part"])

    def test_unknown_document_is_a_404(self):
        self.pair()
        with self.assertRaises(HttpError) as caught:
            self.client.scene("Nope")
        self.assertEqual(caught.exception.status, 404)

    def test_unknown_endpoint_is_a_404(self):
        self.pair()
        status, _, _ = self.client.request("GET", "/api/v1/nonsense")
        self.assertEqual(status, 404)

    def test_thumbnail(self):
        self.pair()
        self.assertEqual(self.client.thumbnail("Part"), self.bridge.png)
        with self.assertRaises(HttpError) as caught:
            self.client.thumbnail("Other")
        self.assertEqual(caught.exception.status, 404)

    # -- environments and state -------------------------------------------

    def test_environments(self):
        self.pair()
        environments = self.client.environments()
        self.assertEqual([e.id for e in environments], ["bambu_x1c"])
        self.assertEqual(environments[0].user_scale, 12.0)
        self.assertEqual(self.client.environment("bambu_x1c")["id"], "bambu_x1c")
        with self.assertRaises(HttpError) as caught:
            self.client.environment("nope")
        self.assertEqual(caught.exception.status, 404)

    def test_state(self):
        self.pair()
        self.assertEqual(
            self.client.state(), {"environment": "bambu_x1c", "scale": 12.0}
        )

    # -- events ------------------------------------------------------------

    def test_long_poll_returns_when_an_event_is_published(self):
        self.pair()
        baseline = self.server.events.last_seq
        started = time.monotonic()
        threading.Timer(
            0.15, lambda: self.server.notify_document_changed("Part", source="test")
        ).start()
        response = self.client.events(since=baseline, timeout=10.0)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 9.0)
        types = [event.type for event in response.events]
        self.assertIn(P.EVENT_DOC_CHANGED, types)
        changed = [e for e in response.events if e.type == P.EVENT_DOC_CHANGED][0]
        self.assertEqual(changed.doc, "Part")
        self.assertEqual(changed.data["source"], "test")
        self.assertGreaterEqual(response.last_seq, changed.seq)

    def test_long_poll_times_out_empty(self):
        self.pair()
        started = time.monotonic()
        response = self.client.events(since=self.server.events.last_seq, timeout=0.3)
        self.assertEqual(response.events, [])
        self.assertGreaterEqual(time.monotonic() - started, 0.25)

    def test_events_are_replayed_from_a_sequence_number(self):
        self.pair()
        baseline = self.server.events.last_seq
        self.server.notify_document_changed("Part")
        self.server.notify_document_changed("Other")
        response = self.client.events(since=baseline, timeout=0.1)
        self.assertEqual(len(response.events), 2)
        again = self.client.events(since=response.events[0].seq, timeout=0.1)
        self.assertEqual(len(again.events), 1)
        self.assertEqual(again.events[0].doc, "Other")

    def test_events_reject_a_bad_since(self):
        self.pair()
        status, _, _ = self.client.request("GET", "/api/v1/events?since=abc")
        self.assertEqual(status, 400)

    def test_wait_for_change_helper(self):
        self.pair()
        baseline = self.server.events.last_seq
        self.server.notify_document_changed("Part")
        changed, last_seq = self.client.wait_for_change(
            "Part", since=baseline, timeout=0.1
        )
        self.assertTrue(changed)
        self.assertGreater(last_seq, 0)
        changed, _ = self.client.wait_for_change("Ghost", since=last_seq, timeout=0.1)
        self.assertFalse(changed)

    # -- inbound edits -----------------------------------------------------

    def test_paint_post(self):
        self.pair()
        writer = FcxrWriter()
        writer.add_image("layer0", PNG_BYTES)
        writer.set_scene(root=0)
        writer.set_paint(
            {
                "version": 1,
                "targets": [
                    {"fc_name": "Body",
                     "layers": [{"name": "Base", "image": 0, "blend": "normal",
                                 "opacity": 1.0, "visible": True,
                                 "resolution": [512, 512]}]}
                ],
            }
        )
        response = self.client.push_paint(writer.to_bytes(), doc="Part")
        self.assertTrue(response.ok)
        self.assertEqual(response.applied, 1)
        self.assertEqual(response.doc, "Part")
        self.assertEqual(len(self.bridge.paint_calls), 1)
        doc_name, package = self.bridge.paint_calls[0]
        self.assertEqual(doc_name, "Part")
        self.assertEqual(package.paint["targets"][0]["fc_name"], "Body")
        self.assertEqual(package.images[0], PNG_BYTES)

    def test_paint_post_publishes_an_event(self):
        self.pair()
        before = self.server.events.last_seq
        writer = FcxrWriter()
        writer.set_scene(root=0)
        writer.set_paint({"version": 1, "targets": []})
        self.client.push_paint(writer.to_bytes(), doc="Part")
        events = self.server.events.since(before)
        self.assertEqual([e.type for e in events], [P.EVENT_DOC_CHANGED])
        self.assertEqual(events[0].data["source"], "paint")

    def test_empty_paint_body_is_rejected(self):
        self.pair()
        status, _, _ = self.client.request(
            "POST", P.EP_PAINT, body=b"", content_type=P.CONTENT_TYPE_FCXR
        )
        self.assertEqual(status, 400)

    def test_corrupt_paint_body_is_rejected(self):
        self.pair()
        status, _, _ = self.client.request(
            "POST", P.EP_PAINT, body=b"not an fcxr package",
            content_type=P.CONTENT_TYPE_FCXR,
        )
        self.assertEqual(status, 400)

    def test_vector_post(self):
        self.pair()
        vector = {
            "version": 1,
            "plane": {"origin": [0, 0, 0], "rotation": [0, 0, 0, 1]},
            "unit_scale": 0.001,
            "paths": [
                {"id": "p1", "closed": False,
                 "nodes": [{"point": [0, 0], "in": None, "out": None,
                            "type": "corner"}],
                 "stroke": {"color": [0, 0, 0, 1], "width": 0.5},
                 "fill": None, "target": "draft"}
            ],
        }
        response = self.client.push_vector(vector, doc="Part")
        self.assertTrue(response.ok)
        self.assertEqual(response.applied, 1)
        self.assertEqual(self.bridge.vector_calls[0][1], vector)

    def test_invalid_vector_is_rejected(self):
        self.pair()
        with self.assertRaises(HttpError) as caught:
            self.client.push_vector({"version": 1,
                                     "paths": [{"target": "spaceship",
                                                "nodes": []}]})
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(self.bridge.vector_calls, [])

    # -- misc --------------------------------------------------------------

    def test_auth_can_be_disabled_for_a_trusted_network(self):
        server = SyncServer(
            port=0, bridge=self.bridge, host="127.0.0.1",
            devices_path=os.path.join(self.tmp.name, "open.json"),
            discovery=False, auth_required=False,
        )
        server.start()
        self.addCleanup(server.stop)
        client = SyncClient("127.0.0.1", server.port)
        self.addCleanup(client.close)
        self.assertFalse(client.hello().auth_required)
        self.assertEqual(len(client.documents()), 2)

    def test_context_manager(self):
        with SyncServer(
            port=0, bridge=self.bridge, host="127.0.0.1", discovery=False,
            auth_required=False,
            devices_path=os.path.join(self.tmp.name, "ctx.json"),
        ) as server:
            self.assertTrue(server.is_running())
            with SyncClient("127.0.0.1", server.port) as client:
                self.assertEqual(client.hello().protocol, 1)
        self.assertFalse(server.is_running())


class DiscoveryResponderTest(unittest.TestCase):
    def test_responder_answers_a_discovery_request(self):
        import socket

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        server = SyncServer(
            port=0, bridge=StubBridge(), host="127.0.0.1", name="beacon-host",
            devices_path=os.path.join(tmp.name, "d.json"),
            discovery=True, discovery_port=0,
        )
        server.start()
        self.addCleanup(server.stop)
        if not server.discovery_port:
            self.skipTest("the discovery socket could not be bound here")

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        sock.settimeout(3.0)
        sock.sendto(P.encode_discovery_request(), ("127.0.0.1", server.discovery_port))
        data, _ = sock.recvfrom(P.MAX_BEACON_SIZE)
        offer = P.parse_discovery_offer(data, address="127.0.0.1")
        self.assertEqual(offer.name, "beacon-host")
        self.assertEqual(offer.port, server.port)
        self.assertEqual(offer.id, server.server_id)

    def test_junk_does_not_upset_the_responder(self):
        import socket

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        server = SyncServer(
            port=0, bridge=StubBridge(), host="127.0.0.1",
            devices_path=os.path.join(tmp.name, "d.json"),
            discovery=True, discovery_port=0,
        )
        server.start()
        self.addCleanup(server.stop)
        if not server.discovery_port:
            self.skipTest("the discovery socket could not be bound here")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        sock.settimeout(3.0)
        sock.sendto(b"garbage", ("127.0.0.1", server.discovery_port))
        sock.sendto(P.encode_discovery_request(), ("127.0.0.1", server.discovery_port))
        data, _ = sock.recvfrom(P.MAX_BEACON_SIZE)
        self.assertTrue(data.startswith(b"FCXR-OFFER"))


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


if __name__ == "__main__":
    unittest.main()
