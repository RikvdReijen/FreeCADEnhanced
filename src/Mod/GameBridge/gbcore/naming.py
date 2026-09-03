# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************
"""Turning FreeCAD labels into asset names the engines will accept.

FreeCAD labels are free text: ``Pad``, ``M6 bolt (x4)``, ``Gehäuse``, ``2mm``.
Each target has its own idea of what an asset may be called, and the failure
modes are all different - Unreal refuses the import, Unity writes a file the
asset database then cannot find, Blender silently truncates at 63 bytes and
appends ``.001`` to whatever collided.  Sanitising per target, up front, is the
only way to keep a name recognisable in all three.
"""

import re
import unicodedata

__all__ = [
    "NamePolicy",
    "FREECAD_POLICY",
    "UNREAL_POLICY",
    "UNITY_POLICY",
    "BLENDER_POLICY",
    "POLICIES",
    "get_policy",
    "NameAllocator",
]

# Windows forbids these device names whatever the extension, and both Unreal and
# Unity keep assets as files, so an object innocently labelled "CON" would fail
# to import on one platform and work on the others.
_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CLOCK$"]
    + ["COM%d" % i for i in range(1, 10)]
    + ["LPT%d" % i for i in range(1, 10)]
)

# Unreal additionally reserves a handful of names in its package system.
_UNREAL_RESERVED = frozenset(["None", "NULL", "Default", "Engine", "Core", "Script"])


class NamePolicy:
    """How one target wants its asset names spelled."""

    def __init__(
        self,
        name,
        allowed=r"A-Za-z0-9_",
        replacement="_",
        max_length=64,
        leading_digit_prefix="_",
        reserved=(),
        transliterate=True,
        case=None,
        forbidden=None,
    ):
        self.name = name
        self.allowed = allowed
        #: ``forbidden`` states the characters to remove directly, for a policy
        #: that accepts nearly everything: "anything except control characters"
        #: cannot be expressed by negating an allowed set, because negating a
        #: negation is not what the regular expression engine does with it.
        self.forbidden = forbidden
        self._invalid = re.compile(forbidden if forbidden else "[^%s]" % allowed)
        self.replacement = replacement
        self.max_length = max_length
        self.leading_digit_prefix = leading_digit_prefix
        self.reserved = frozenset(reserved)
        self.transliterate = transliterate
        self.case = case

    def sanitize(self, label, fallback="Object"):
        """Make ``label`` safe for this target, without losing readability."""
        text = "" if label is None else str(label)
        if self.transliterate:
            text = _transliterate(text)
        text = self._invalid.sub(self.replacement, text)
        if self.replacement:
            # Collapse runs so "M6 bolt (x4)" reads M6_bolt_x4, not M6_bolt__x4_.
            text = re.sub(re.escape(self.replacement) + "{2,}", self.replacement, text)
            text = text.strip(self.replacement)
        if not text:
            text = fallback
        if self.leading_digit_prefix and text[0].isdigit():
            text = self.leading_digit_prefix + text
        if self.max_length and len(text) > self.max_length:
            text = text[: self.max_length].rstrip(self.replacement or " ")
        if self.case == "upper":
            text = text.upper()
        elif self.case == "lower":
            text = text.lower()
        if text.upper() in _RESERVED or text in self.reserved:
            text = text + self.replacement + "asset"
        return text

    def __repr__(self):
        return "NamePolicy(%r)" % self.name


def _transliterate(text):
    """Fold accents down to ASCII: ``Gehäuse`` becomes ``Gehause``.

    Unicode in an asset name survives Blender, mostly survives Unity and reliably
    trips Unreal's package name validation, so the bridge folds rather than
    strips - a mangled but readable name beats ``______``.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    # A few letters have no decomposition and would otherwise vanish entirely.
    for source, target in (("ß", "ss"), ("Ø", "O"), ("ø", "o"), ("Æ", "AE"),
                           ("æ", "ae"), ("Đ", "D"), ("đ", "d"), ("Ł", "L"),
                           ("ł", "l"), ("Þ", "Th"), ("þ", "th")):
        ascii_only = ascii_only.replace(source, target)
    return ascii_only


#: FreeCAD itself: anything goes, so this only removes control characters.
FREECAD_POLICY = NamePolicy(
    "freecad",
    forbidden=r"[\x00-\x1f\x7f]",
    replacement="",
    max_length=0,
    leading_digit_prefix="",
    transliterate=False,
)

#: Unreal asset names live in package paths; only word characters are safe.
UNREAL_POLICY = NamePolicy(
    "unreal", max_length=100, reserved=_UNREAL_RESERVED
)

#: Unity is happier than Unreal, but the assets are still files on disk.
UNITY_POLICY = NamePolicy(
    "unity", allowed=r"A-Za-z0-9_\- ", replacement="_", max_length=100
)

#: Blender truncates object names at 63 *bytes*, so the limit is on the
#: encoding.  Accents are folded rather than replaced: Blender would accept
#: them, but a name that survives a round trip through an engine and back is
#: worth more than one that renders its umlauts, and ``Gehause`` beats
#: ``Geh_use``.
BLENDER_POLICY = NamePolicy(
    "blender",
    allowed=r"A-Za-z0-9_\-. ",
    replacement="_",
    max_length=59,
    leading_digit_prefix="",
)

POLICIES = {
    p.name: p
    for p in (FREECAD_POLICY, UNREAL_POLICY, UNITY_POLICY, BLENDER_POLICY)
}


def get_policy(name):
    if isinstance(name, NamePolicy):
        return name
    return POLICIES.get(str(name).lower(), UNREAL_POLICY)


class NameAllocator:
    """Hands out unique names, remembering what it has already given away.

    Two FreeCAD objects may share a label, and after sanitising even more of
    them collide - ``Pad 1`` and ``Pad-1`` both become ``Pad_1``.  The allocator
    appends ``_001``, ``_002`` and so on, and keeps the result inside the
    policy's length limit by trimming the stem rather than the suffix.
    """

    def __init__(self, policy=UNREAL_POLICY, separator="_"):
        self.policy = get_policy(policy)
        self.separator = separator
        self._used = set()
        self._counters = {}
        self._assigned = {}

    def allocate(self, label, key=None, fallback="Object"):
        """Return a unique name for ``label``.

        ``key`` identifies the caller's object; asking twice with the same key
        gives the same name back rather than allocating a second one.
        """
        if key is not None and key in self._assigned:
            return self._assigned[key]
        stem = self.policy.sanitize(label, fallback)
        name = stem
        if name.lower() in self._used:
            counter = self._counters.get(stem.lower(), 0)
            limit = self.policy.max_length
            while True:
                counter += 1
                suffix = "%s%03d" % (self.separator, counter)
                trimmed = stem
                if limit and len(stem) + len(suffix) > limit:
                    trimmed = stem[: max(1, limit - len(suffix))]
                name = trimmed + suffix
                if name.lower() not in self._used:
                    break
            self._counters[stem.lower()] = counter
        self._used.add(name.lower())
        if key is not None:
            self._assigned[key] = name
        return name

    def reserve(self, name):
        """Mark a name as taken without allocating it, e.g. a fixed root."""
        self._used.add(name.lower())
        return name

    def get(self, key, default=None):
        return self._assigned.get(key, default)

    def __contains__(self, name):
        return name.lower() in self._used

    def __len__(self):
        return len(self._assigned)
