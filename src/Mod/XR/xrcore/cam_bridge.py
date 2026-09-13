# SPDX-License-Identifier: LGPL-2.1-or-later
"""CAM toolpath preview inside the machine environment.

``load_gcode(path)`` or ``load_job(job)`` builds a :class:`xrcam.CamSession`
with a :class:`xrcam.MachineSpec` taken from the current environment's
build-plate anchor, draws the toolpath at scale where the user is standing
(cutting moves in one colour, rapids in another, the layers below the
current one dimmed), and moves a toolhead marker along it during playback.
Bounds and collision issues found by ``session.check()`` are printed and,
when playback reaches one, felt as a haptic warning.
"""

import os

import FreeCAD

from xrcore import service
from xrsketch import vecmath as vm

__all__ = ["get_session", "load_gcode", "load_job", "attach", "detach", "activate", "deactivate", "handle_frame",
           "machine_for_environment", "play", "pause", "toggle", "set_speed", "goto_layer", "status_text", "clear"]

_root = None
_path_node = None
_head_node = None
_active = False


def get_session():
    return service.get_feature("cam")


def machine_for_environment(env_id=None):
    """A MachineSpec from the current (or named) environment's anchor."""
    from xrcam import MachineSpec

    env_id = env_id or service.get_environment_id()
    try:
        from xrenv import registry

        spec = registry.get(env_id).spec
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"XR: environment {env_id!r} unavailable ({exc}); using a generic 256 mm machine\n")
        return MachineSpec(name=env_id or "machine", anchor_position=(0.0, 0.0, 0.0))
    try:
        return MachineSpec.from_environment_spec(spec, env_id)
    except ValueError as exc:
        FreeCAD.Console.PrintWarning(f"XR: {exc}; the toolpath will be placed at the environment origin\n")
        return MachineSpec(name=env_id or "machine")


def load_gcode(path, env_id=None):
    from xrcam import CamSession, parse_gcode

    with open(path, "rb") as handle:
        text = handle.read()
    toolpath = parse_gcode(text, name=os.path.basename(path))
    return _install(CamSession(toolpath, machine_for_environment(env_id)))


def load_job(job, env_id=None):
    from xrcam import CamSession, from_freecad_job

    toolpath = from_freecad_job(job)
    return _install(CamSession(toolpath, machine_for_environment(env_id)))


def _install(session):
    old = get_session()
    if old is not None:
        clear()
    service.set_feature("cam", session)
    issues = session.check(collisions=False)
    for issue in issues[:10]:
        FreeCAD.Console.PrintWarning("XR CAM: %s\n" % issue.message)
    FreeCAD.Console.PrintMessage("XR CAM: %s — %d segments, %s, %d issue(s)\n" % (
        session.toolpath.name, len(session.toolpath), session.status()["remaining"], len(issues)))
    _draw(session)
    return session


def attach(widget, root):
    global _root
    _root = root
    session = get_session()
    if session is not None:
        _draw(session)


def detach():
    global _root
    clear(keep_session=True)
    _root = None


def clear(keep_session=False):
    global _path_node, _head_node
    if _root is not None:
        for node in (_path_node, _head_node):
            if node is not None:
                try:
                    _root.removeChild(node)
                except Exception:
                    pass
    _path_node = _head_node = None
    if not keep_session:
        service.set_feature("cam", None)


def activate():
    global _active
    _active = True


def deactivate():
    global _active
    _active = False


def _draw(session):
    global _path_node, _head_node
    if _root is None:
        return
    try:
        from pivy import coin

        from xrcam import world_polyline
        from xrcore import coin_util
    except Exception:
        return
    if _path_node is not None:
        _root.removeChild(_path_node)
    node = coin.SoSeparator()
    lines = world_polyline(session.toolpath, session.machine, max_error=0.2)
    node.addChild(coin_util.make_lines([pts for pts, cut, _ in lines if cut], (0.2, 0.9, 1.0), 2.0))
    node.addChild(coin_util.make_lines([pts for pts, cut, _ in lines if not cut], (0.6, 0.6, 0.6), 1.0))
    _root.addChild(node)
    _path_node = node
    if _head_node is None:
        _head_node = coin_util.make_marker(session.tool_position_world(), (1.0, 0.4, 0.1), 0.006)
        _root.addChild(_head_node)


def handle_frame(dt, controllers):
    session = get_session()
    if session is None:
        return False
    session.update(dt)
    if _head_node is not None:
        p = session.tool_position_world()
        _head_node.transform.translation.setValue(float(p[0]), float(p[1]), float(p[2]))
    try:
        from xrcore import haptics_bridge
        from xrhaptics import CAM_EVENTS

        events = session.drain_events()
        for event in events:
            FreeCAD.Console.PrintWarning("XR CAM: %s\n" % event.issue.message)
        haptics_bridge.feed(events, CAM_EVENTS, hand=None)
    except Exception:
        session.drain_events()
    return False


def _require():
    session = get_session()
    if session is None:
        raise service.XRServiceError("No toolpath loaded. Use Virtual Reality → Load G-code first.")
    return session


def play():
    _require().play()


def pause():
    _require().pause()


def toggle():
    return _require().toggle()


def set_speed(factor):
    return _require().set_speed(factor)


def goto_layer(layer):
    return _require().goto_layer(layer)


def status_text():
    session = get_session()
    if session is None:
        return ""
    s = session.status()
    return "cam: %s L%d %s %s x%g" % ("▶" if s["playing"] else "▮▮", s["layer"], s["remaining"], session.toolpath.name[:16], s["speed"])
