# SPDX-License-Identifier: LGPL-2.1-or-later
"""Target paths: the strings operations and claims use to name things.

Two forms are understood:

* ``param:wall_min`` — a document-level parameter.
* ``Body.Flange.Fillet2.Radius`` — a dotted path. Segments are containers
  down to a feature, optionally followed by a property. Which segment is the
  feature is decided against a document (the last segment that names an
  existing feature); without one, prefix matching is used, so ``Body.Flange``
  is taken to cover ``Body.Flange.Sketch``.
"""

PARAM_PREFIX = "param:"


def is_param(target):
    return isinstance(target, str) and target.startswith(PARAM_PREFIX)


def param_name(target):
    return target[len(PARAM_PREFIX) :] if is_param(target) else None


def segments(target):
    return [] if is_param(target) else [s for s in target.split(".") if s]


def covers(claim, target):
    """True when the claimed region ``claim`` includes ``target``.

    ``Body`` covers ``Body.Pad3`` and ``Body.Pad3.Length``; a bare feature
    name ``Pad3`` covers ``Pad3.Length`` and also ``Body.Pad3``, since the same
    feature may be named with or without its container. Parameters only cover
    themselves.
    """
    if claim == target:
        return True
    if is_param(claim) or is_param(target):
        return False
    claim_segments, target_segments = segments(claim), segments(target)
    if not claim_segments or not target_segments:
        return False
    if target_segments[: len(claim_segments)] == claim_segments:
        return True
    # A bare feature name matches that feature wherever it sits in a container.
    if len(claim_segments) == 1:
        return claim_segments[0] in target_segments[:-1] or (
            len(target_segments) >= 1 and target_segments[-1] == claim_segments[0]
        )
    return False


def overlap(a, b):
    return covers(a, b) or covers(b, a)


def split(target, doc):
    """Split a target against a document into ``(feature_name, property_path)``.

    ``feature_name`` is ``None`` for a document parameter or when no segment
    names a feature; ``property_path`` is the remainder as a dotted string
    (empty when the target names the feature itself).
    """
    if is_param(target):
        return None, param_name(target)
    parts = segments(target)
    for index in range(len(parts) - 1, -1, -1):
        if doc.has_feature(parts[index]):
            return parts[index], ".".join(parts[index + 1 :])
    return None, target


def feature_of(target, doc=None):
    """The feature a target refers to; without a document, the best guess is
    the last segment that is not obviously a property (capitalised paths are
    features; the final segment of a 3+-segment path is assumed a property)."""
    if doc is not None:
        return split(target, doc)[0]
    parts = segments(target)
    if not parts:
        return None
    return parts[-1] if len(parts) == 1 else parts[-2] if len(parts) > 2 else parts[-1]
