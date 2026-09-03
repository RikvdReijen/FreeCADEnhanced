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
"""Glue between the environment switcher (:mod:`xrenv`) and the XR viewer.

The viewer's world scenegraph ends up looking like this::

    world_separator
     ├── scale_transform      <- miniaturisation, applied to everything below
     ├── env_switch
     │    └── env_root        <- geometry built from the environment spec
     ├── doc_separator
     │    ├── doc_xr_transform <- the document's own placement (see below)
     │    └── sg               <- the FreeCAD scenegraph
     └── paint_separator

Shrinking the user is the same thing as growing the world, so
:class:`EnvironmentManager` scales the environment and the document together
and lets :mod:`xrenv.scale` work out the clip-plane compensation.

Dropping the model onto a machine's build plate is *not* done with an extra
node: the engine already owns ``doc_xr_transform``, which carries the document
out of millimetres and Z-up into the metres and Y-up the headset uses, and both
the model-scale slider and the XR-to-document coordinate helpers read it.  A
second transform in front of it would silently apply the unit conversion twice
and put picking out of step with what is drawn, so the fit computed by
:func:`xrenv.scale.fit_document_to_anchor` — which is a complete replacement,
conversion included — is written into that same node.

Everything degrades to a no-op (plus a stored preference) when the viewer is
not running, so the commands work from the desktop too.
"""

import FreeCAD

from xrcore import service

__all__ = [
    "EnvironmentManager",
    "manager",
    "attach",
    "detach",
    "available_environments",
    "set_environment",
    "cycle_environment",
    "nudge_scale",
    "reset_scale",
    "current_state",
]

# The scale range a user can reach with the shrink/grow commands.  1.0 is life
# size; 200 puts a person at roughly the size of a printed layer line.
MIN_SCALE = 1.0 / 8.0
MAX_SCALE = 400.0


class EnvironmentManager:
    """Owns the environment scenegraph and the miniaturisation transform."""

    def __init__(self):
        self.widget = None
        self.env_root = None  # SoSeparator holding the built environment
        self.env_switch = None  # SoSwitch toggling it on and off
        self.scale_transform = None  # SoTransform applied to world + document
        self.environment = None  # xrenv.registry.Environment
        self.controller = None  # xrenv.scale.ScaleController
        self._pending_id = None

    # ------------------------------------------------------------------
    # viewer lifecycle
    # ------------------------------------------------------------------

    def attach(self, widget, world_root):
        """Called by :mod:`xrcore.commonXR` once the scenegraph exists."""
        from pivy.coin import SoSeparator, SoSwitch, SoTransform, SO_SWITCH_ALL

        self.widget = widget
        self.scale_transform = SoTransform()
        self.env_switch = SoSwitch()
        self.env_switch.whichChild = SO_SWITCH_ALL
        self.env_root = SoSeparator()
        self.env_switch.addChild(self.env_root)

        world_root.insertChild(self.scale_transform, 0)
        world_root.insertChild(self.env_switch, 1)

        from xrenv.scale import ScaleController

        self.controller = ScaleController()

        env_id = self._pending_id or service.get_environment_id()
        self._pending_id = None
        try:
            self.set_environment(env_id)
        except Exception as exc:  # a broken user environment must not kill VR
            FreeCAD.Console.PrintWarning(f"XR: could not load environment '{env_id}': {exc}\n")

    def detach(self):
        self.widget = None
        self.env_root = None
        self.env_switch = None
        self.scale_transform = None
        self.controller = None

    @property
    def is_live(self):
        return self.widget is not None and self.env_root is not None

    # ------------------------------------------------------------------
    # environment selection
    # ------------------------------------------------------------------

    def set_environment(self, env_id):
        from xrenv import registry

        environment = registry.get(env_id)
        self.environment = environment
        service.set_environment_id(env_id)

        if not self.is_live:
            self._pending_id = env_id
            return environment

        self.env_root.removeAllChildren()
        built = environment.build_scenegraph()
        if built is not None:
            self.env_root.addChild(built)

        if self.controller is not None:
            self.controller.set_environment(environment)
            self.controller.set_scale(environment.user_scale, animate=False)
        self._apply_transform()
        self._place_document()
        FreeCAD.Console.PrintMessage(
            f"XR: environment '{environment.info.name}' at 1:{environment.user_scale:g}\n"
        )
        return environment

    def cycle(self, delta=1):
        from xrenv import registry

        infos = registry.list_environments()
        if not infos:
            raise service.XRServiceError("No environments are installed.")
        ids = [info.id for info in infos]
        current = service.get_environment_id()
        index = ids.index(current) if current in ids else -1
        return self.set_environment(ids[(index + delta) % len(ids)])

    # ------------------------------------------------------------------
    # scale
    # ------------------------------------------------------------------

    def set_scale(self, scale, animate=True):
        scale = max(MIN_SCALE, min(MAX_SCALE, float(scale)))
        if self.controller is None:
            service.preferences().SetFloat("UserScale", scale)
            return scale
        self.controller.set_scale(scale, animate=animate)
        service.preferences().SetFloat("UserScale", scale)
        self._apply_transform()
        return scale

    def nudge(self, factor):
        current = self.controller.scale if self.controller else service.preferences().GetFloat(
            "UserScale", 1.0
        )
        return self.set_scale(current * factor)

    def reset(self):
        default = self.environment.user_scale if self.environment else 1.0
        return self.set_scale(default)

    def step_animation(self, dt):
        """Called once per rendered frame by the viewer."""
        if self.controller is None:
            return False
        if self.controller.step(dt):
            self._apply_transform()
            return True
        return False

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _apply_transform(self):
        if self.scale_transform is None or self.controller is None:
            return
        from pivy.coin import SbVec3f

        scale = self.controller.world_scale
        offset = self.controller.world_offset
        self.scale_transform.scaleFactor.setValue(SbVec3f(scale, scale, scale))
        self.scale_transform.translation.setValue(SbVec3f(*offset))

        # Keep the near clip plane proportional or a miniaturised user's hands
        # disappear into the near plane.
        widget = self.widget
        if widget is not None and hasattr(widget, "set_clip_planes"):
            near, far = self.controller.clip_planes()
            widget.set_clip_planes(near, far)

    def _place_document(self):
        """Drop the document onto the environment's primary anchor.

        The fit replaces ``doc_xr_transform`` outright rather than stacking a
        node in front of it — see the module docstring.
        """
        widget = self.widget
        if widget is None or self.environment is None:
            return
        if not service.preferences().GetBool("PlaceOnAnchor", True):
            return
        anchor = self.environment.primary_anchor()
        transform = getattr(widget, "doc_xr_transform", None)
        if transform is None:
            return
        if anchor is None:
            self._reset_document_transform(transform)
            return

        bbox = None
        if hasattr(widget, "document_bounding_box"):
            try:
                bbox = widget.document_bounding_box()
            except Exception:
                bbox = None
        fit = self.controller.fit_document_to_anchor(bbox, anchor)
        if fit is None:
            self._reset_document_transform(transform)
            return

        from pivy.coin import SbRotation, SbVec3f

        transform.translation.setValue(SbVec3f(*fit.translation))
        transform.scaleFactor.setValue(SbVec3f(fit.scale, fit.scale, fit.scale))
        transform.rotation.setValue(SbRotation(*fit.rotation))

    @staticmethod
    def _reset_document_transform(transform):
        """Back to the engine's default: millimetres, Z-up, at the origin."""
        from math import pi

        from pivy.coin import SbRotation, SbVec3f

        transform.translation.setValue(SbVec3f(0.0, 0.0, 0.0))
        transform.scaleFactor.setValue(SbVec3f(0.001, 0.001, 0.001))
        transform.rotation.setValue(SbRotation(SbVec3f(1, 0, 0), -pi / 2))


_manager = EnvironmentManager()


def manager():
    return _manager


def attach(widget, world_root):
    _manager.attach(widget, world_root)


def detach():
    _manager.detach()


def available_environments():
    from xrenv import registry

    return registry.list_environments()


def set_environment(env_id):
    return _manager.set_environment(env_id)


def cycle_environment(delta=1):
    return _manager.cycle(delta)


def nudge_scale(factor):
    return _manager.nudge(factor)


def reset_scale():
    return _manager.reset()


def current_state():
    """Small dict used by the dialogs and the sync server."""
    scale = (
        _manager.controller.scale
        if _manager.controller is not None
        else service.preferences().GetFloat("UserScale", 1.0)
    )
    return {
        "environment": service.get_environment_id(),
        "scale": scale,
        "live": _manager.is_live,
    }
