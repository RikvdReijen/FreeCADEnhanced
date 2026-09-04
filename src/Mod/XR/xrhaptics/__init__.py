# SPDX-License-Identifier: LGPL-2.1-or-later
"""Haptics on snap, contact and constraint satisfaction.

Cheap, and snapping without feedback feels unreliable even when it is not.
``patterns`` says what each event feels like, ``engine`` schedules pulses
with cooldowns and priorities, ``hooks`` maps the other subsystems' events
onto the engine. The OpenXR output action lives in ``xrcore.haptics_bridge``;
the Quest app has its own pulse in ``input.cpp``.
"""

from .patterns import PATTERNS, Pattern, Pulse, pattern_for, set_pattern
from .engine import HapticEngine, NullBackend, RecordingBackend
from .hooks import ASSEMBLY_EVENTS, CAM_EVENTS, FIT_EVENTS, SCAN_EVENTS, VOICE_EVENTS, feed, snap_feedback

__all__ = ["PATTERNS", "Pattern", "Pulse", "pattern_for", "set_pattern", "HapticEngine", "NullBackend",
           "RecordingBackend", "ASSEMBLY_EVENTS", "CAM_EVENTS", "FIT_EVENTS", "SCAN_EVENTS", "VOICE_EVENTS",
           "feed", "snap_feedback"]
