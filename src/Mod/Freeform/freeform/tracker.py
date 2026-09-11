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
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""Coin3D previews and mouse capture for the interactive Freeform tools.

:class:`LineTracker`
    a temporary polyline drawn straight into the 3D view scene graph
:class:`PlaneTracker`
    a translucent square showing the current drawing plane
:class:`StrokeCapture`
    hooks the 3D view events and turns a press-drag-release into a list of
    3D points on the work plane, with live preview (and a mirrored preview
    when symmetry is on)
:class:`ClickCapture`
    a press-drag-release capture used to place primitives (position and
    size in one gesture)

This module is only importable with the GUI up.
"""

import FreeCADGui
from pivy import coin

from . import workplane

__all__ = ["LineTracker", "PlaneTracker", "StrokeCapture", "ClickCapture"]


def _scene_graph(view=None):
    if view is None:
        view = FreeCADGui.ActiveDocument.ActiveView
    return view.getSceneGraph()


class _Tracker:
    """Base for temporary scene graph nodes."""

    def __init__(self, color=(1.0, 0.5, 0.0), line_width=3.0, view=None):
        self.view = view or FreeCADGui.ActiveDocument.ActiveView
        self.switch = coin.SoSwitch()
        self.node = coin.SoSeparator()
        self.color = coin.SoBaseColor()
        self.color.rgb = color
        self.style = coin.SoDrawStyle()
        self.style.lineWidth = line_width
        self.node.addChild(self.color)
        self.node.addChild(self.style)
        self.switch.addChild(self.node)
        self.switch.whichChild = -1
        self._attached = False
        self.attach()

    def attach(self):
        if not self._attached:
            _scene_graph(self.view).addChild(self.switch)
            self._attached = True

    def on(self):
        self.switch.whichChild = 0

    def off(self):
        self.switch.whichChild = -1

    def set_color(self, rgb):
        self.color.rgb = tuple(rgb)

    def finalize(self):
        if self._attached:
            try:
                _scene_graph(self.view).removeChild(self.switch)
            except Exception:  # pylint: disable=broad-except
                pass
            self._attached = False


class LineTracker(_Tracker):
    """A polyline preview."""

    def __init__(self, color=(1.0, 0.5, 0.0), line_width=3.0, view=None):
        super().__init__(color, line_width, view)
        self.coords = coin.SoCoordinate3()
        self.line = coin.SoLineSet()
        self.node.addChild(self.coords)
        self.node.addChild(self.line)
        self.clear()

    def clear(self):
        self.coords.point.setNum(0)
        self.line.numVertices.setValue(0)
        self.count = 0
        self.off()

    def set_points(self, points):
        pts = [(p.x, p.y, p.z) for p in points]
        self.coords.point.setValues(0, len(pts), pts)
        self.coords.point.setNum(len(pts))
        self.line.numVertices.setValue(len(pts))
        self.count = len(pts)
        if pts:
            self.on()

    def add_point(self, point):
        self.coords.point.set1Value(self.count, point.x, point.y, point.z)
        self.count += 1
        self.line.numVertices.setValue(self.count)
        self.on()


class PlaneTracker(_Tracker):
    """A translucent square patch showing the work plane."""

    def __init__(self, color=(0.3, 0.6, 1.0), size=None, view=None):
        super().__init__(color, 1.0, view)
        self.material = coin.SoMaterial()
        self.material.diffuseColor = color
        self.material.transparency = 0.8
        self.coords = coin.SoCoordinate3()
        self.face = coin.SoFaceSet()
        self.face.numVertices.setValue(4)
        shape_hints = coin.SoShapeHints()
        shape_hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
        shape_hints.shapeType = coin.SoShapeHints.UNKNOWN_SHAPE_TYPE
        self.node.addChild(shape_hints)
        self.node.addChild(self.material)
        self.node.addChild(self.coords)
        self.node.addChild(self.face)
        self.size = size
        self.update()

    def update(self, plane=None):
        plane = plane or workplane.get_work_plane()
        size = self.size
        if size is None:
            size = 100.0
            try:
                cam = self.view.getCameraNode()
                size = (
                    max(20.0, float(cam.height.getValue()) * 0.6)
                    if hasattr(cam, "height")
                    else 100.0
                )
            except Exception:  # pylint: disable=broad-except
                pass
        corners = plane.local_polyline(size)
        self.coords.point.setValues(0, 4, [(c.x, c.y, c.z) for c in corners])
        self.on()


class _Capture:
    """Common event hook handling for the capture classes."""

    def __init__(self, view=None):
        self.view = view or FreeCADGui.ActiveDocument.ActiveView
        self.callback = None
        self.active = False

    def start(self):
        if not self.active:
            self.callback = self.view.addEventCallback("SoEvent", self._event)
            self.active = True

    def stop(self):
        if self.active:
            try:
                self.view.removeEventCallback("SoEvent", self.callback)
            except Exception:  # pylint: disable=broad-except
                pass
            self.active = False

    def _event(self, event):
        raise NotImplementedError


class StrokeCapture(_Capture):
    """Capture freehand strokes drawn with the left mouse button.

    ``on_stroke(points)`` is called with the raw 3D points for every
    completed stroke; ``on_finish()`` when the user presses Escape. Set
    ``mirror`` to a :class:`SymmetryPlane` to preview the mirrored stroke.
    """

    def __init__(
        self,
        on_stroke,
        on_finish=None,
        view=None,
        min_pixel_step=3,
        color=(1.0, 0.5, 0.0),
        width=3.0,
    ):
        super().__init__(view)
        self.on_stroke = on_stroke
        self.on_finish = on_finish
        self.min_pixel_step = max(1, int(min_pixel_step))
        self.plane = workplane.get_work_plane()
        self.mirror = None
        self.tracker = LineTracker(color, width, self.view)
        self.mirror_tracker = LineTracker(tuple(c * 0.6 for c in color), width, self.view)
        self.points = []
        self.last_pixel = None
        self.drawing = False
        self.on_surface = None

    def set_color(self, rgb):
        self.tracker.set_color(rgb)
        self.mirror_tracker.set_color(tuple(c * 0.6 for c in rgb))

    def _append(self, position):
        if self.last_pixel is not None:
            dx = position[0] - self.last_pixel[0]
            dy = position[1] - self.last_pixel[1]
            if dx * dx + dy * dy < self.min_pixel_step * self.min_pixel_step:
                return
        point, _ = self.plane.point_from_screen(self.view, position, self.on_surface)
        self.last_pixel = position
        self.points.append(point)
        self.tracker.add_point(point)
        if self.mirror is not None and self.mirror.enabled:
            self.mirror_tracker.add_point(self.mirror.mirror_point(point))

    def _event(self, event):
        kind = event.get("Type")
        if kind == "SoKeyboardEvent":
            if event.get("Key") == "ESCAPE" and event.get("State") == "DOWN":
                self.cancel()
                if self.on_finish:
                    self.on_finish()
            return
        if kind == "SoMouseButtonEvent" and event.get("Button") == "BUTTON1":
            if event.get("State") == "DOWN":
                self.drawing = True
                self.points = []
                self.last_pixel = None
                self.tracker.clear()
                self.mirror_tracker.clear()
                self._append(event["Position"])
            elif event.get("State") == "UP" and self.drawing:
                self._append(event["Position"])
                self.drawing = False
                points = self.points
                self.points = []
                self.tracker.clear()
                self.mirror_tracker.clear()
                if len(points) >= 2:
                    self.on_stroke(points)
            return
        if kind == "SoLocation2Event" and self.drawing:
            self._append(event["Position"])

    def cancel(self):
        self.drawing = False
        self.points = []
        self.tracker.clear()
        self.mirror_tracker.clear()

    def finalize(self):
        self.stop()
        self.tracker.finalize()
        self.mirror_tracker.finalize()


class ClickCapture(_Capture):
    """Capture a press-drag-release gesture as ``(point, size, direction)``.

    ``on_place(point, size)`` receives the press point on the work plane and
    the distance dragged (0 for a simple click). ``on_finish()`` is called
    on Escape. A circle preview shows the size while dragging.
    """

    def __init__(self, on_place, on_finish=None, view=None, color=(0.3, 0.8, 0.3)):
        super().__init__(view)
        self.on_place = on_place
        self.on_finish = on_finish
        self.plane = workplane.get_work_plane()
        self.tracker = LineTracker(color, 2.0, self.view)
        self.start_point = None
        self.on_surface = None

    def _circle(self, center, radius):
        import math

        pts = []
        u, v = self.plane.u, self.plane.v
        for i in range(33):
            a = 2 * math.pi * i / 32.0
            pts.append(center + u * (radius * math.cos(a)) + v * (radius * math.sin(a)))
        return pts

    def _event(self, event):
        kind = event.get("Type")
        if kind == "SoKeyboardEvent":
            if event.get("Key") == "ESCAPE" and event.get("State") == "DOWN":
                self.start_point = None
                self.tracker.clear()
                if self.on_finish:
                    self.on_finish()
            return
        if kind == "SoMouseButtonEvent" and event.get("Button") == "BUTTON1":
            if event.get("State") == "DOWN":
                self.start_point, _ = self.plane.point_from_screen(
                    self.view, event["Position"], self.on_surface
                )
            elif event.get("State") == "UP" and self.start_point is not None:
                end, _ = self.plane.point_from_screen(self.view, event["Position"], self.on_surface)
                size = (end - self.start_point).Length
                start = self.start_point
                self.start_point = None
                self.tracker.clear()
                self.on_place(start, size)
            return
        if kind == "SoLocation2Event" and self.start_point is not None:
            end, _ = self.plane.point_from_screen(self.view, event["Position"], self.on_surface)
            radius = (end - self.start_point).Length
            if radius > 1e-6:
                self.tracker.set_points(self._circle(self.start_point, radius))

    def finalize(self):
        self.stop()
        self.tracker.finalize()
