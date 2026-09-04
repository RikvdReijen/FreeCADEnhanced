# SPDX-License-Identifier: LGPL-2.1-or-later
"""The in-VR drawing session: point at the sheet, pick, place.

The controller ray lands on the table; the session snaps it to the nearest
vertex or edge of whichever view is under it, shows that as the hover, and
``pick()`` (the trigger) keeps it. ``place_dimension()`` infers a
dimension from the picks and commits it to TechDraw when FreeCAD is there
— otherwise it is kept in ``placed`` as a preview the renderer draws.
"""

from .dimension import InferenceError, Pick, infer
from .table import DraftingTable


class DrawEvent(object):
    __slots__ = ("kind", "detail")

    def __init__(self, kind, detail=None):
        self.kind = kind
        self.detail = detail or {}

    def __repr__(self):
        return "DrawEvent(%s)" % self.kind


class DrawSession(object):
    def __init__(self, table=None, views=(), page=None, snap_tolerance=3.0):
        self.table = table or DraftingTable()
        self.views = list(views)
        self.page = page
        self.snap_tolerance = float(snap_tolerance)
        self.hover = None        # (x, y, on_page, view, element)
        self.picks = []
        self.placed = []         # DimensionSpec previews / committed
        self.events = []
        self.notes = []
        self.page_image = None

    # -- pointing --------------------------------------------------------

    def point(self, origin, direction):
        """Update the hover from a controller ray. Returns the hover tuple or None."""
        hit = self.table.ray_to_page(origin, direction)
        if hit is None:
            self.hover = None
            return None
        x, y, on_page, _ = hit
        view, element = None, None
        if on_page:
            best = None
            for v in self.views:
                found = v.nearest((x, y), self.snap_tolerance)
                if found and (best is None or found[1] < best[1]):
                    best = (found[1], v, found[0])
            if best is not None:
                _, view, element = best
        previous = self.hover
        self.hover = (x, y, on_page, view, element)
        if element is not None and (previous is None or previous[4] is not element):
            self.events.append(DrawEvent("hover", {"element": element.name, "view": view.name}))
        return self.hover

    def pick(self):
        """Keep the hovered element. Returns the Pick or None when nothing is under the ray."""
        if self.hover is None or self.hover[4] is None:
            self.events.append(DrawEvent("miss"))
            return None
        x, y, _, view, element = self.hover
        pick = Pick(view, element, (x, y))
        self.picks.append(pick)
        if len(self.picks) > 2:
            self.picks = self.picks[-2:]
        self.events.append(DrawEvent("pick", {"element": element.name, "count": len(self.picks)}))
        return pick

    def clear_picks(self):
        self.picks = []

    # -- dimensions ------------------------------------------------------

    def preview(self):
        """The dimension the current picks would make, or None."""
        try:
            return infer(self.picks) if self.picks else None
        except InferenceError:
            return None

    def place_dimension(self):
        try:
            spec = infer(self.picks)
        except InferenceError as exc:
            self.notes.append(str(exc))
            self.events.append(DrawEvent("error", {"message": str(exc)}))
            return None
        committed = None
        if self.page is not None:
            from .to_techdraw import make_dimension

            committed = make_dimension(spec, self.page, notes=self.notes)
        self.placed.append(spec)
        self.picks = []
        self.events.append(DrawEvent("dimension", {"type": spec.type, "label": spec.label,
                                                   "committed": committed is not None}))
        return spec

    def undo(self):
        if self.placed:
            spec = self.placed.pop()
            self.events.append(DrawEvent("undo", {"label": spec.label}))
            return spec
        return None

    # -- page ------------------------------------------------------------

    def load_page(self, page):
        """Read a TechDraw page's views (FreeCAD only)."""
        from .to_techdraw import page_image, page_views, view_geometry

        self.page = page
        self.views = [g for g in (view_geometry(v, page, self.notes) for v in page_views(page)) if g is not None]
        self.page_image = page_image(page, notes=self.notes)
        return self.views

    def drain_events(self):
        events, self.events = self.events, []
        return events

    def to_dict(self):
        return {"table": self.table.to_dict(), "views": [v.name for v in self.views],
                "picks": [p.reference for p in self.picks], "placed": [d.to_dict() for d in self.placed]}
