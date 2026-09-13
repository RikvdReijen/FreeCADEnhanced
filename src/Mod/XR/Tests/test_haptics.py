# SPDX-License-Identifier: LGPL-2.1-or-later
"""Haptic patterns, the engine's scheduling, and the subsystem hooks."""

import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

from xrhaptics import (FIT_EVENTS, ASSEMBLY_EVENTS, HapticEngine, Pattern, Pulse, RecordingBackend,  # noqa: E402
                       feed, pattern_for, set_pattern, snap_feedback)


class PatternTest(unittest.TestCase):
    def test_defaults_exist(self):
        for kind in ("snap", "contact", "blocked", "constraint", "ui_click", "error", "seated", "aligned"):
            self.assertIsNotNone(pattern_for(kind), kind)
        self.assertEqual(len(pattern_for("constraint").pulses), 2, "double tap")
        self.assertAlmostEqual(pattern_for("constraint").length, 0.03 + 0.05 + 0.03)

    def test_amplitude_scaling(self):
        contact = pattern_for("contact")
        self.assertAlmostEqual(contact.amplitude_factor(None), 1.0)
        self.assertAlmostEqual(contact.amplitude_factor(0.0), 0.35)
        self.assertAlmostEqual(contact.amplitude_factor(0.005), 1.0)
        self.assertAlmostEqual(contact.amplitude_factor(1.0), 1.0)

    def test_pulse_clamps(self):
        p = Pulse(1.7, -1, 100)
        self.assertEqual((p.amplitude, p.duration), (1.0, 0.0))
        self.assertEqual(p.scaled(0.5).amplitude, 0.5)

    def test_override(self):
        set_pattern(Pattern("custom", [Pulse(0.1, 0.01)], priority=9))
        self.assertEqual(pattern_for("custom").priority, 9)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.backend = RecordingBackend()
        self.engine = HapticEngine(self.backend, clock=lambda: 0.0)

    def test_pulses_are_sent_when_due(self):
        e = self.engine
        self.assertTrue(e.trigger("constraint", hand=1, now=0.0))
        self.assertEqual(e.pending, 2)
        self.assertEqual(e.update(now=0.0), 1)
        self.assertEqual(self.backend.pulses, [(1, 0.8, 0.03, 200)])
        self.assertEqual(e.update(now=0.05), 0, "second tap not due yet")
        self.assertEqual(e.update(now=0.08), 1)
        self.assertEqual(e.pending, 0)

    def test_cooldown_and_priority(self):
        e = self.engine
        self.assertTrue(e.trigger("contact", 1, now=0.0))
        self.assertFalse(e.trigger("contact", 1, now=0.02), "within cooldown")
        self.assertEqual(e.dropped, 1)
        self.assertTrue(e.trigger("contact", 1, now=0.1))
        # a lower-priority click cannot interrupt the contact bump
        self.assertFalse(e.trigger("ui_click", 1, now=0.105))
        # a blocked buzz can
        self.assertTrue(e.trigger("blocked", 1, now=0.105))
        e.update(now=1.0)
        kinds = [k for _, _, k in e.fired]
        self.assertEqual(kinds, ["contact", "contact", "blocked"])

    def test_magnitude_and_intensity(self):
        e = HapticEngine(self.backend, intensity=0.5)
        e.trigger("contact", 0, magnitude=0.0, now=0.0)
        e.update(now=0.0)
        hand, amp, dur, freq = self.backend.pulses[0]
        self.assertEqual(hand, 0)
        self.assertAlmostEqual(amp, 0.45 * 0.35 * 0.5, places=4)

    def test_both_hands_and_disabled(self):
        e = self.engine
        self.assertTrue(e.trigger("snap", hand=None, now=0.0))
        e.update(now=0.0)
        self.assertEqual(sorted(p[0] for p in self.backend.pulses), [0, 1])
        e.enabled = False
        self.assertFalse(e.trigger("snap", 1, now=5.0))
        self.assertIn("off", e.describe())

    def test_unknown_kind_and_flush(self):
        e = self.engine
        self.assertFalse(e.trigger("teleport", 1, now=0.0))
        e.trigger("aligned", 1, now=0.0)
        self.assertEqual(e.flush(), 3)
        e.trigger("aligned", 1, now=10.0)
        e.stop()
        self.assertEqual(e.pending, 0)


class HooksTest(unittest.TestCase):
    def test_feed_fit_events(self):
        from xrfit.session import FitEvent

        backend = RecordingBackend()
        engine = HapticEngine(backend)
        events = [FitEvent("grab", "peg"), FitEvent("contact", "peg", "hole", depth=0.004), FitEvent("blocked", "peg", depth=0.01)]
        self.assertEqual(feed(engine, events, FIT_EVENTS, hand=1, now=0.0), 3)
        engine.update(now=1.0)
        # Three events in one frame: each higher priority interrupts the last, so only the buzz plays.
        self.assertEqual([p[3] for p in backend.pulses], [90])
        self.assertEqual(feed(engine, [FitEvent("nothing", "x")], FIT_EVENTS, now=2.0), 0)

    def test_feed_assembly_events(self):
        from xrassembly.session import AssemblyEvent

        engine = HapticEngine(RecordingBackend())
        self.assertEqual(feed(engine, [AssemblyEvent("snap", "peg"), AssemblyEvent("constraint", "peg")], ASSEMBLY_EVENTS, now=0.0), 2)

    def test_snap_feedback(self):
        from xrsketch.snapping import SnapResult

        engine = HapticEngine(RecordingBackend())
        none = SnapResult((0, 0, 0))
        grid = SnapResult((0, 0, 0), kind="grid", target=None, index=None)
        vertex = SnapResult((0, 0, 0), kind="vertex", target="obj", index=3)
        self.assertTrue(snap_feedback(engine, none, grid, now=0.0))
        self.assertFalse(snap_feedback(engine, grid, grid, now=1.0), "holding a snap is silent")
        self.assertTrue(snap_feedback(engine, grid, vertex, now=2.0))
        self.assertTrue(snap_feedback(engine, vertex, none, now=3.0))
        self.assertEqual([k for _, _, k in engine.fired], ["snap", "ui_click", "unsnap"])


if __name__ == "__main__":
    unittest.main()
