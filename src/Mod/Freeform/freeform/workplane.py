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

"""Drawing plane and symmetry plane used by the interactive Freeform tools.

In an immersive sketching tool every stroke is drawn "in the air"; with a
mouse we need a surface to draw on. The :class:`WorkPlane` provides one in
several modes:

``Top`` / ``Front`` / ``Side``
    the global XY, XZ and YZ planes
``View``
    a plane facing the camera through the current origin (draw in the air
    at the depth of the camera focal point)
``Surface``
    draw directly on whatever geometry is under the cursor; falls back to
    the plane when the cursor is not over an object
``Custom``
    an arbitrary plane, for example picked from a face
``Draft``
    follow the Draft workbench working plane when Draft is available

The :class:`SymmetryPlane` is the live mirror plane used by the symmetry
mode and the mirror command.
"""

import FreeCAD
from FreeCAD import Vector

from . import geometry

PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/Freeform"

PLANE_MODES = ("Top", "Front", "Side", "View", "Surface", "Custom", "Draft")

_AXES = {
    "Top": (Vector(1, 0, 0), Vector(0, 1, 0), Vector(0, 0, 1)),
    "Front": (Vector(1, 0, 0), Vector(0, 0, 1), Vector(0, -1, 0)),
    "Side": (Vector(0, 1, 0), Vector(0, 0, 1), Vector(1, 0, 0)),
}


def _params():
    return FreeCAD.ParamGet(PARAM_PATH)


def _active_view():
    if not FreeCAD.GuiUp:
        return None
    import FreeCADGui

    doc = FreeCADGui.ActiveDocument
    if doc is None:
        return None
    view = doc.ActiveView
    if view is None or not hasattr(view, "getViewDirection"):
        return None
    return view


class WorkPlane:
    """The plane the interactive tools draw on."""

    def __init__(self):
        self.mode = "Top"
        self.origin = Vector(0, 0, 0)
        self.u = Vector(1, 0, 0)
        self.v = Vector(0, 1, 0)
        self.normal = Vector(0, 0, 1)
        self.snap = False
        self.grid = 1.0
        self.load()

    # -- persistence -------------------------------------------------------

    def load(self):
        params = _params()
        mode = params.GetString("PlaneMode", "Top")
        self.snap = params.GetBool("SnapToGrid", False)
        self.grid = params.GetFloat("GridSpacing", 1.0)
        self.set_mode(mode if mode in PLANE_MODES else "Top", save=False)

    def save(self):
        params = _params()
        params.SetString("PlaneMode", self.mode)
        params.SetBool("SnapToGrid", self.snap)
        params.SetFloat("GridSpacing", self.grid)

    # -- configuration -----------------------------------------------------

    def set_mode(self, mode, save=True):
        """Switch the plane mode; see the module docstring for the modes."""
        if mode not in PLANE_MODES:
            raise ValueError("unknown plane mode %r" % mode)
        self.mode = mode
        if mode in _AXES:
            self.u, self.v, self.normal = (Vector(a) for a in _AXES[mode])
        elif mode == "View":
            self.align_to_view()
        elif mode == "Draft":
            self.align_to_draft()
        # "Surface" and "Custom" keep the current axes as fallback plane
        if save:
            self.save()

    def set_axes(self, origin, normal, u=None, mode="Custom"):
        """Define the plane from an origin, a normal and an optional u axis."""
        self.origin = Vector(origin)
        self.normal = geometry._safe_normalize(Vector(normal))
        if u is None or abs(Vector(u).dot(self.normal)) > 0.999:
            u = geometry._perpendicular(self.normal)
        u = Vector(u)
        u = u - self.normal * u.dot(self.normal)
        self.u = geometry._safe_normalize(u)
        self.v = self.normal.cross(self.u)
        self.mode = mode
        self.save()

    def align_to_view(self, origin=None):
        """Face the camera (like drawing on a sheet of glass in front of you)."""
        view = _active_view()
        if view is None:
            return False
        direction = Vector(*view.getViewDirection())
        up = Vector(*view.getUpDirection()) if hasattr(view, "getUpDirection") else Vector(0, 0, 1)
        normal = direction * -1.0
        u = up.cross(normal)
        if u.Length < 1e-9:
            u = geometry._perpendicular(normal)
        if origin is None:
            origin = self.origin
        self.set_axes(origin, normal, u, mode="View")
        return True

    def align_to_draft(self):
        """Copy the Draft workbench working plane when it is available."""
        try:
            import WorkingPlane

            draft_plane = WorkingPlane.get_working_plane(update=False)
        except Exception:  # pylint: disable=broad-except
            return False
        self.origin = Vector(draft_plane.position)
        self.u = Vector(draft_plane.u)
        self.v = Vector(draft_plane.v)
        self.normal = Vector(draft_plane.axis)
        self.mode = "Draft"
        self.save()
        return True

    def align_to_face(self, shape, subname=None, point=None):
        """Set a custom plane from a (planar) face of ``shape``."""
        face = shape
        if subname:
            face = shape.getElement(subname)
        if face.ShapeType != "Face":
            return False
        if point is None:
            point = face.CenterOfMass
        try:
            uv = face.Surface.parameter(point)
            normal = face.normalAt(uv[0], uv[1])
        except Exception:  # pylint: disable=broad-except
            normal = face.normalAt(0, 0)
        self.set_axes(point, normal, None, mode="Custom")
        return True

    # -- geometry ----------------------------------------------------------

    def placement(self):
        rotation = FreeCAD.Rotation(self.u, self.v, self.normal, "ZXY")
        return FreeCAD.Placement(self.origin, rotation)

    def to_local(self, point):
        d = Vector(point) - self.origin
        return Vector(d.dot(self.u), d.dot(self.v), d.dot(self.normal))

    def to_global(self, local):
        return self.origin + self.u * local.x + self.v * local.y + self.normal * local.z

    def intersect_ray(self, point, direction):
        """Intersect the line through ``point`` along ``direction`` with the plane."""
        direction = Vector(direction)
        denominator = direction.dot(self.normal)
        if abs(denominator) < 1e-9:
            # ray parallel to the plane: project the point instead
            return self.project(point)
        t = (self.origin - Vector(point)).dot(self.normal) / denominator
        return Vector(point) + direction * t

    def project(self, point):
        d = Vector(point) - self.origin
        return Vector(point) - self.normal * d.dot(self.normal)

    def snap_point(self, point):
        """Snap ``point`` to the plane grid when snapping is enabled."""
        if not self.snap or self.grid <= 0:
            return Vector(point)
        local = self.to_local(point)
        local.x = round(local.x / self.grid) * self.grid
        local.y = round(local.y / self.grid) * self.grid
        return self.to_global(local)

    def point_from_screen(self, view, position, on_surface=None):
        """Convert a screen position of ``view`` into a point on the plane.

        ``on_surface`` overrides the mode: True picks geometry under the
        cursor first, False never does. Returns ``(point, hit)`` where
        ``hit`` is True when actual geometry was hit.
        """
        if on_surface is None:
            on_surface = self.mode == "Surface"
        x, y = int(position[0]), int(position[1])
        if on_surface:
            info = view.getObjectInfo((x, y))
            if info is not None:
                return Vector(info["x"], info["y"], info["z"]), True
        if self.mode == "Draft":
            self.align_to_draft()
        focal = Vector(*view.getPoint(x, y))
        direction = Vector(*view.getViewDirection())
        if self.mode == "View":
            # keep the plane at the focal depth of the camera
            self.origin = focal
            return self.snap_point(focal), False
        return self.snap_point(self.intersect_ray(focal, direction)), False

    def local_polyline(self, size=10.0, divisions=10):
        """Corner points of a square patch of the plane (for previews)."""
        half = size / 2.0
        corners = [
            Vector(-half, -half, 0),
            Vector(half, -half, 0),
            Vector(half, half, 0),
            Vector(-half, half, 0),
        ]
        return [self.to_global(c) for c in corners]


class SymmetryPlane:
    """The plane used by the live symmetry mode and the mirror tool."""

    def __init__(self):
        self.enabled = False
        self.origin = Vector(0, 0, 0)
        self.normal = Vector(1, 0, 0)
        self.load()

    def load(self):
        params = _params()
        self.enabled = params.GetBool("SymmetryEnabled", False)
        self.origin = Vector(
            params.GetFloat("SymmetryOriginX", 0.0),
            params.GetFloat("SymmetryOriginY", 0.0),
            params.GetFloat("SymmetryOriginZ", 0.0),
        )
        self.normal = Vector(
            params.GetFloat("SymmetryNormalX", 1.0),
            params.GetFloat("SymmetryNormalY", 0.0),
            params.GetFloat("SymmetryNormalZ", 0.0),
        )
        if self.normal.Length < 1e-9:
            self.normal = Vector(1, 0, 0)
        self.normal.normalize()

    def save(self):
        params = _params()
        params.SetBool("SymmetryEnabled", self.enabled)
        params.SetFloat("SymmetryOriginX", self.origin.x)
        params.SetFloat("SymmetryOriginY", self.origin.y)
        params.SetFloat("SymmetryOriginZ", self.origin.z)
        params.SetFloat("SymmetryNormalX", self.normal.x)
        params.SetFloat("SymmetryNormalY", self.normal.y)
        params.SetFloat("SymmetryNormalZ", self.normal.z)

    def set(self, origin, normal, enabled=None):
        self.origin = Vector(origin)
        self.normal = geometry._safe_normalize(Vector(normal), Vector(1, 0, 0))
        if enabled is not None:
            self.enabled = bool(enabled)
        self.save()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.save()

    def mirror(self, points):
        return geometry.mirror_points(points, self.origin, self.normal)

    def mirror_point(self, point):
        return geometry.mirror_point(point, self.origin, self.normal)

    def is_on_plane(self, point, tolerance=1e-6):
        return abs((Vector(point) - self.origin).dot(self.normal)) <= tolerance

    def axis_name(self):
        """Human readable name of an axis aligned plane, else 'Custom'."""
        n = self.normal
        for name, axis in (
            ("YZ", Vector(1, 0, 0)),
            ("XZ", Vector(0, 1, 0)),
            ("XY", Vector(0, 0, 1)),
        ):
            if abs(abs(n.dot(axis)) - 1.0) < 1e-6:
                return name
        return "Custom"


_work_plane = None
_symmetry_plane = None


def get_work_plane():
    """Return the shared :class:`WorkPlane` instance."""
    global _work_plane  # pylint: disable=global-statement
    if _work_plane is None:
        _work_plane = WorkPlane()
    return _work_plane


def get_symmetry_plane():
    """Return the shared :class:`SymmetryPlane` instance."""
    global _symmetry_plane  # pylint: disable=global-statement
    if _symmetry_plane is None:
        _symmetry_plane = SymmetryPlane()
    return _symmetry_plane
