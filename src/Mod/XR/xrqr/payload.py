# SPDX-License-Identifier: LGPL-2.1-or-later
"""What an anchor QR code says.

A printed code is a spatial anchor: its four corners give a pose, and its
text says what to put there. The payload is a URI so any phone can read
it too::

    fcxr://anchor?id=bench-1&size=80&doc=housing.FCStd&origin=model&up=z
    fcxr://anchor?id=plate&size=60&target=build_plate

``size`` is the printed edge length in millimetres — without it the pose
has no scale. ``origin`` says what to snap to the code: ``model`` (the
document origin), ``env`` (the environment origin) or ``part:<name>``;
``target`` names an environment anchor the code stands for. ``up`` says
which model axis the code's normal is (``z`` on a table, ``y`` on a wall
means the model's Y).
"""

import urllib.parse

SCHEME = "fcxr"
KIND = "anchor"


class AnchorPayload(object):
    __slots__ = ("id", "size_mm", "doc", "origin", "target", "up", "extras")

    def __init__(self, id, size_mm, doc=None, origin="model", target=None, up="z", extras=None):
        if not id:
            raise ValueError("an anchor needs an id")
        if size_mm is None or float(size_mm) <= 0:
            raise ValueError("an anchor needs its printed size in mm")
        if up not in ("x", "y", "z", "-x", "-y", "-z"):
            raise ValueError("up must be an axis, got %r" % (up,))
        self.id = str(id)
        self.size_mm = float(size_mm)
        self.doc = doc
        self.origin = origin
        self.target = target
        self.up = up
        self.extras = dict(extras or {})

    @property
    def part(self):
        return self.origin[len("part:"):] if self.origin and self.origin.startswith("part:") else None

    def encode(self):
        params = [("id", self.id), ("size", ("%g" % self.size_mm))]
        if self.doc:
            params.append(("doc", self.doc))
        if self.origin and self.origin != "model":
            params.append(("origin", self.origin))
        if self.target:
            params.append(("target", self.target))
        if self.up != "z":
            params.append(("up", self.up))
        params.extend(sorted(self.extras.items()))
        return "%s://%s?%s" % (SCHEME, KIND, urllib.parse.urlencode(params))

    @classmethod
    def decode(cls, text):
        text = (text or "").strip()
        parsed = urllib.parse.urlparse(text)
        if parsed.scheme != SCHEME or parsed.netloc != KIND:
            raise ValueError("not an %s anchor code: %r" % (SCHEME, text[:40]))
        q = dict(urllib.parse.parse_qsl(parsed.query))
        known = {"id", "size", "doc", "origin", "target", "up"}
        try:
            size = float(q.get("size", ""))
        except ValueError:
            raise ValueError("anchor code has no usable size: %r" % (q.get("size"),))
        return cls(q.get("id"), size, q.get("doc"), q.get("origin", "model"), q.get("target"), q.get("up", "z"),
                   {k: v for k, v in q.items() if k not in known})

    def to_dict(self):
        return {"id": self.id, "size_mm": self.size_mm, "doc": self.doc, "origin": self.origin, "target": self.target,
                "up": self.up, "extras": dict(self.extras)}

    def __repr__(self):
        return "AnchorPayload(%s, %g mm)" % (self.id, self.size_mm)


def is_anchor(text):
    try:
        AnchorPayload.decode(text)
        return True
    except ValueError:
        return False
