# SPDX-License-Identifier: LGPL-2.1-or-later
"""Voice: numbers, the grammar, dispatch, the session."""

import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from xrvoice import (ActionResult, Context, Dispatcher, RemoteBackend, TextBackend, Transcript, VoiceSession,  # noqa: E402
                     VoskBackend, default_handlers, help_text, parse, parse_number, parse_quantity, tokenize,
                     vocabulary_words)


def num(text):
    r = parse_number(tokenize(text))
    return None if r is None else r[0]


def qty(text, family="length"):
    q = parse_quantity(tokenize(text), 0, family)
    return None if q is None else (round(q.value, 6), q.family)


class NumbersTest(unittest.TestCase):
    def test_digits_and_words(self):
        self.assertEqual(num("2"), 2.0)
        self.assertEqual(num("2.5"), 2.5)
        self.assertEqual(num("2,5"), 2.5)
        self.assertEqual(num("two"), 2.0)
        self.assertEqual(num("twenty five"), 25.0)
        self.assertEqual(num("twenty-five"), 25.0)
        self.assertEqual(num("one hundred and twenty"), 120.0)
        self.assertEqual(num("two thousand three hundred"), 2300.0)
        self.assertEqual(num("a hundred"), 100.0)

    def test_decimals_and_fractions(self):
        self.assertEqual(num("two point five"), 2.5)
        self.assertEqual(num("three point one four"), 3.14)
        self.assertEqual(num("half"), 0.5)
        self.assertEqual(num("a half"), 0.5)
        self.assertEqual(num("a quarter"), 0.25)
        self.assertEqual(num("three quarters"), 0.75)
        self.assertEqual(num("two and a half"), 2.5)
        self.assertEqual(num("2 and a half"), 2.5)
        self.assertEqual(num("3/4"), 0.75)
        self.assertIsNone(num("banana"))

    def test_units(self):
        self.assertEqual(qty("2 mm"), (2.0, "length"))
        self.assertEqual(qty("2mm"), (2.0, "length"))
        self.assertEqual(qty("two millimetres"), (2.0, "length"))
        self.assertEqual(qty("one inch"), (25.4, "length"))
        self.assertEqual(qty("half an inch"), (12.7, "length"))
        self.assertEqual(qty("3 cm"), (30.0, "length"))
        self.assertEqual(qty("1.5 m"), (1500.0, "length"))
        self.assertEqual(qty("ninety degrees", "angle"), (90.0, "angle"))
        self.assertEqual(qty("ninety", "angle"), (90.0, "angle"))
        self.assertEqual(qty("5"), (5.0, "length"))
        self.assertEqual(qty("50 percent"), (0.5, "ratio"))
        self.assertEqual(qty('1/2"'), (12.7, "length"))


class GrammarTest(unittest.TestCase):
    def intent(self, text):
        p = parse(text)
        self.assertIsNotNone(p.intent, "%r did not parse (alternatives %s)" % (text, p.alternatives))
        return p.intent

    def test_modelling_commands(self):
        i = self.intent("fillet these edges, 2 mm")
        self.assertEqual((i.name, i.params["qty"].value), ("fillet", 2.0))
        self.assertEqual(self.intent("round off the edges two point five millimetres").params["qty"].value, 2.5)
        self.assertEqual(self.intent("chamfer 1mm").name, "chamfer")
        i = self.intent("pocket five millimetres deep")
        self.assertEqual((i.name, i.params["qty"].value), ("pocket", 5.0))
        self.assertTrue(self.intent("pocket through all").params["through_all"])
        self.assertEqual(self.intent("extrude 10").params["qty"].value, 10.0)
        self.assertEqual(self.intent("hole 6 mm diameter").params["qty"].value, 6.0)
        self.assertEqual(self.intent("hollow out 1.2 mm wall").name, "shell")
        i = self.intent("set wall thickness to 3 mm")
        self.assertEqual((i.params["name"], i.params["qty"].value), ("wall thickness", 3.0))

    def test_transforms(self):
        i = self.intent("move up two and a half millimetres")
        self.assertEqual((i.params["dir"], i.params["vector"], i.params["qty"].value), ("up", (0, 1, 0), 2.5))
        i = self.intent("rotate ninety degrees about z")
        self.assertEqual((i.params["angle"].value, i.params["axis"]), (90.0, "z"))
        i = self.intent("rotate around y forty five")
        self.assertEqual((i.params["angle"].value, i.params["axis"]), (45.0, "y"))
        self.assertEqual(self.intent("make it bigger").params["factor"], 1.25)

    def test_navigation_and_modes(self):
        self.assertEqual(self.intent("shrink me").params["direction"], "shrink")
        self.assertEqual(self.intent("grow me").params["direction"], "grow")
        self.assertEqual(self.intent("life size").name, "scale_reset")
        self.assertEqual(self.intent("switch to the printer").params["name"], "printer")
        self.assertEqual(self.intent("take me to the laser cutter").params["name"], "laser cutter")
        self.assertEqual(self.intent("environment studio").params["name"], "studio")
        self.assertEqual(self.intent("next environment").name, "environment_next")
        self.assertEqual(self.intent("sculpt mode").params["mode"], "sculpt")
        self.assertEqual(self.intent("assembly mode").params["mode"], "assembly")
        self.assertEqual(self.intent("modelling").params["mode"], "model")
        self.assertEqual(self.intent("use the pen tool").params["tool"], "pen")
        self.assertEqual(self.intent("grab tool").params["tool"], "select")
        self.assertFalse(self.intent("snap off").params["enabled"])
        self.assertTrue(self.intent("turn on snapping").params["enabled"])
        self.assertEqual(self.intent("grid 5").params["qty"].value, 5.0)
        self.assertTrue(self.intent("start capture").params["enabled"])
        self.assertFalse(self.intent("stop recording").params["enabled"])

    def test_cam_and_misc(self):
        self.assertEqual(self.intent("play the toolpath").name, "play")
        self.assertEqual(self.intent("layer 12").params["n"], 12)
        self.assertEqual(self.intent("speed 4 times").params["factor"], 4.0)
        self.assertEqual(self.intent("faster").params["factor"], 2.0)
        for text, name in (("undo", "undo"), ("undo that", "undo"), ("redo", "redo"), ("delete it", "delete"), ("hide", "hide"),
                           ("show everything", "show"), ("select all", "select_all"), ("clear selection", "deselect"),
                           ("how long is it", "measure"), ("done", "commit"), ("never mind", "cancel"), ("save", "save"),
                           ("mate it", "mate"), ("let go", "release"), ("what can i say", "voice_help"), ("dimension that", "dimension")):
            self.assertEqual(self.intent(text).name, name, text)

    def test_no_match_reports_alternatives(self):
        p = parse("fillet")
        self.assertIsNone(p.intent)
        self.assertIn("fillet", p.alternatives)
        self.assertIsNone(parse("").intent)
        self.assertIsNone(parse("teleport me to mars").intent)

    def test_help_and_vocabulary(self):
        self.assertIn("fillet", help_text())
        words = vocabulary_words()
        for w in ("fillet", "millimetres", "twenty", "half", "printer", "undo"):
            self.assertIn(w, words)


class DispatchTest(unittest.TestCase):
    def test_needs_and_confidence(self):
        d = Dispatcher(default_handlers())
        r = d.handle(parse("fillet 2 mm").intent, Context())
        self.assertFalse(r.ok)
        self.assertIn("needs a selection", r.message)
        r = d.handle(parse("fillet 2 mm", confidence=0.2).intent, Context(selection=["x"], document=object()))
        self.assertFalse(r.ok)
        self.assertIn("not sure", r.message)
        r = d.handle(None)
        self.assertEqual(r.message, "not understood")

    def test_dry_run_without_freecad(self):
        d = Dispatcher(default_handlers())
        ctx = Context(selection=[("Pad", ["Edge1"])], document=object())
        r = d.handle(parse("fillet 2 mm").intent, ctx)
        self.assertTrue(r.ok, r.message)
        self.assertIn("dry run", r.message)
        r = d.handle(parse("pocket 5").intent, ctx)
        self.assertTrue(r.ok)
        r = d.handle(parse("rotate 90 about x").intent, ctx)
        self.assertIn("90", r.message)

    def test_missing_argument(self):
        d = Dispatcher(default_handlers())
        # 'fillet' alone does not parse; a synthetic intent without qty is refused by the handler
        from xrvoice.grammar import Intent, command
        r = d.handle(Intent("fillet", {}, "fillet", 1.0, command("fillet")), Context(selection=["e"], document=object()))
        self.assertFalse(r.ok)
        self.assertIn("how big", r.message)

    def test_custom_handler_and_exceptions(self):
        d = Dispatcher()
        d.register("undo", lambda intent, ctx: "custom undo")
        self.assertEqual(d.handle(parse("undo").intent).message, "custom undo")

        def boom(intent, ctx):
            raise RuntimeError("no")

        d.register("redo", boom)
        r = d.handle(parse("redo").intent)
        self.assertFalse(r.ok)
        self.assertIn("failed: no", r.message)
        self.assertEqual(len(d.log), 2)

    def test_session_handlers(self):
        class FakeAssembly(object):
            grabbed = "peg"

            def __init__(self):
                self.released = False

            def confirm(self):
                from xrassembly import Mate
                return Mate("concentric", "a", "b", "c", "d")

            def release(self):
                self.released = True

        fake = FakeAssembly()
        d = Dispatcher(default_handlers())
        ctx = Context(sessions={"assembly": fake})
        self.assertEqual(d.handle(parse("mate").intent, ctx).message, "mated concentric")
        self.assertTrue(d.handle(parse("let go").intent, ctx).ok)
        self.assertTrue(fake.released)
        self.assertFalse(d.handle(parse("play").intent, ctx).ok, "no CAM session")


class SessionTest(unittest.TestCase):
    def test_say_and_events(self):
        s = VoiceSession(TextBackend())
        results = s.say("undo")
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok, "undo needs a document handler outcome; dispatcher reports")
        kinds = [e.kind for e in s.drain_events()]
        self.assertIn("misheard", kinds)
        s.say("fly to the moon")
        self.assertIn("didn't understand", s.history[-1][2].message)

    def test_partials_and_remote(self):
        backend = RemoteBackend()
        s = VoiceSession(backend)
        backend.receive({"text": "undo", "confidence": 0.9, "final": False})
        self.assertEqual(s.poll(), [])
        self.assertEqual(s.partial, "undo")
        backend.receive('{"text": "select all", "confidence": 0.9}')
        results = s.poll()
        self.assertEqual(results[0].intent.name, "select_all")
        self.assertEqual(s.partial, "")

    def test_vosk_unavailable_is_honest(self):
        b = VoskBackend(model_path=None)
        self.assertFalse(b.available)
        self.assertTrue(b.unavailable_reason)
        b.start()  # no-op
        self.assertFalse(b.listening)
        t = Transcript.from_dict({"text": "x"})
        self.assertEqual(t.to_dict()["text"], "x")


if __name__ == "__main__":
    unittest.main()
