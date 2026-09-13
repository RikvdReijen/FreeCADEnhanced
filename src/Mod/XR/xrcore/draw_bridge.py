# SPDX-License-Identifier: LGPL-2.1-or-later
"""A TechDraw sheet on a drafting table in the room.

``show_page(page)`` puts the page's rendering on the table (a textured quad
in the world separator), reads its views' geometry so the ray can snap to
vertices and edges, and switches the primary controller into pointing
mode: hovering shows a cursor on the sheet, the trigger picks, the
"Dimension" menu button (or saying "dimension that") places the inferred
dimension into the TechDraw page. Placed dimensions are drawn on the table
too, so the sheet updates without a round trip to the desktop.
"""

import FreeCAD

from xrcore import docmesh, service

__all__ = ["get_session", "ensure_session", "attach", "detach", "activate", "deactivate", "active", "handle_frame",
           "show_page", "place_dimension", "undo", "status_text", "table_for_environment"]

_root = None
_sheet_node = None
_cursor_node = None
_dims_node = None
_active = False
_trigger_was = False


def get_session():
    return service.get_feature("draw")


def table_for_environment(env_id=None):
    """A table placed in front of the user's spawn point, page size from prefs."""
    from xrdraw import DraftingTable

    prefs = service.preferences()
    page = prefs.GetString("DrawPageSize", "A3")
    position = (0.0, 0.9, -0.6)
    try:
        from xrenv import registry

        spawn = registry.get(env_id or service.get_environment_id()).spawn
        position = (spawn[0], spawn[1] + 0.9, spawn[2] - 0.6)
    except Exception:
        pass
    return DraftingTable(position=position, tilt_deg=prefs.GetFloat("DrawTableTilt", 20.0), page_size=page)


def ensure_session():
    session = get_session()
    if session is None:
        from xrdraw import DrawSession

        session = DrawSession(table_for_environment())
        service.set_feature("draw", session)
    return session


def attach(widget, root):
    global _root
    _root = root
    session = get_session()
    if session is not None and session.page is not None:
        _draw_sheet(session)


def detach():
    global _root, _sheet_node, _cursor_node, _dims_node
    if _root is not None:
        for node in (_sheet_node, _cursor_node, _dims_node):
            if node is not None:
                try:
                    _root.removeChild(node)
                except Exception:
                    pass
    _sheet_node = _cursor_node = _dims_node = None
    _root = None
    deactivate()


def activate():
    global _active
    session = ensure_session()
    if session.page is None:
        _load_first_page(session)
    _active = True


def deactivate():
    global _active
    _active = False


def active():
    return _active


def _load_first_page(session):
    doc = FreeCAD.ActiveDocument
    if doc is None:
        return None
    for obj in getattr(doc, "Objects", []) or []:
        if getattr(obj, "TypeId", "") == "TechDraw::DrawPage":
            return show_page(obj)
    FreeCAD.Console.PrintWarning("XR: the document has no TechDraw page to put on the table\n")
    return None


def show_page(page):
    session = ensure_session()
    views = session.load_page(page)
    for note in session.notes:
        FreeCAD.Console.PrintLog("XR draw: %s\n" % note)
    session.notes = []
    FreeCAD.Console.PrintMessage("XR draw: %s on the table with %d view(s)\n" % (page.Label, len(views)))
    _draw_sheet(session)
    return session


def _draw_sheet(session):
    global _sheet_node, _cursor_node
    if _root is None:
        return
    try:
        from xrcore import coin_util
    except Exception:
        return
    if _sheet_node is not None:
        _root.removeChild(_sheet_node)
    _sheet_node = coin_util.make_textured_quad(session.table.corners_world(), session.page_image)
    _root.addChild(_sheet_node)
    if _cursor_node is None:
        _cursor_node = coin_util.make_marker((0, 0, 0), (1.0, 0.2, 0.2), 0.006)
        _root.addChild(_cursor_node)
    _draw_dimensions(session)


def _draw_dimensions(session):
    global _dims_node
    if _root is None:
        return
    from xrcore import coin_util

    if _dims_node is not None:
        _root.removeChild(_dims_node)
    polylines = []
    for spec in session.placed:
        if spec.text_position is None:
            continue
        x, y = spec.text_position
        polylines.append([session.table.page_to_world(x - 3, y, 0.001), session.table.page_to_world(x + 3, y, 0.001)])
    _dims_node = coin_util.make_lines(polylines, (0.1, 0.3, 1.0), 2.0) if polylines else None
    if _dims_node is not None:
        _root.addChild(_dims_node)


def handle_frame(dt, controllers):
    global _trigger_was
    if not _active:
        return False
    session = get_session()
    if session is None or not controllers:
        return False
    widget = service.get_widget()
    ctl = docmesh.primary_controller(widget, controllers)
    origin, direction = docmesh.controller_ray(ctl)
    buttons = docmesh.controller_buttons(ctl)
    if origin is None or buttons is None:
        return False
    hover = session.point(origin, direction)
    if _cursor_node is not None and hover is not None:
        p = session.table.page_to_world(hover[0], hover[1], 0.002)
        _cursor_node.transform.translation.setValue(float(p[0]), float(p[1]), float(p[2]))
    trigger = buttons[0] >= 0.7
    if trigger and not _trigger_was:
        session.pick()
    _trigger_was = trigger
    try:
        from xrcore import haptics_bridge

        engine = haptics_bridge.engine()
        for event in session.drain_events():
            kind = {"hover": "ui_hover", "pick": "pick", "dimension": "constraint", "miss": "misheard"}.get(event.kind)
            if kind:
                engine.trigger(kind, widget.primary_con if widget is not None else 1)
    except Exception:
        session.drain_events()
    return hover is not None and hover[2]


def place_dimension():
    session = ensure_session()
    spec = session.place_dimension()
    for note in session.notes:
        FreeCAD.Console.PrintWarning("XR draw: %s\n" % note)
    session.notes = []
    if spec is not None:
        FreeCAD.Console.PrintMessage("XR draw: %s %s\n" % (spec.type, spec.label))
        _draw_dimensions(session)
        if session.page is not None and session.page_image is not None:
            try:
                from xrdraw import page_image

                session.page_image = page_image(session.page) or session.page_image
                _draw_sheet(session)
            except Exception:
                pass
    return spec


def undo():
    session = get_session()
    return session.undo() if session is not None else None


def status_text():
    session = get_session()
    if session is None or not _active:
        return ""
    preview = session.preview()
    if preview is not None:
        return "draw: %s %s — trigger/confirm to place" % (preview.type, preview.label)
    return "draw: %d pick(s)" % len(session.picks)
