# SPDX-License-Identifier: LGPL-2.1-or-later
"""TechDraw on one side, the VR table on the other.

* :func:`view_geometry` reads a ``TechDraw::DrawViewPart``'s projected
  vertices and edges into a :class:`~xrdraw.dimension.ViewGeometry` in
  page millimetres.
* :func:`make_dimension` creates a ``TechDraw::DrawViewDimension`` from a
  :class:`~xrdraw.dimension.DimensionSpec` and adds it to the page.
* :func:`page_image` renders the page to PNG bytes (through SVG and Qt)
  for the table's texture.

All three need FreeCAD; each returns ``None`` (and says why in the
``notes`` list) when it cannot run, so the session degrades to preview-only.
"""

from .dimension import Edge, Vertex, ViewGeometry


def _freecad():
    try:
        import FreeCAD
        return FreeCAD
    except ImportError:
        return None


def page_views(page):
    """The DrawViewPart objects on a page."""
    return [v for v in getattr(page, "Views", []) or [] if "DrawViewPart" in getattr(v, "TypeId", "") or hasattr(v, "getVisibleVertexes")]


def view_geometry(view, page=None, notes=None):
    """Projected geometry of a TechDraw view in page mm. Best effort across
    TechDraw's Python surface: ``getVisibleVertexes``/``getVisibleEdges`` (1.0)."""
    notes = notes if notes is not None else []
    try:
        x = float(getattr(view.X, "Value", view.X))
        y = float(getattr(view.Y, "Value", view.Y))
        scale = float(getattr(view, "Scale", 1.0))
    except Exception as exc:
        notes.append("view %s: no placement (%s)" % (getattr(view, "Name", "?"), exc))
        return None
    verts, edges = [], []
    try:
        for i, v in enumerate(view.getVisibleVertexes()):
            p = getattr(v, "Point", v)
            verts.append(Vertex(i + 1, x + p.x * scale, y + p.y * scale))
    except Exception as exc:
        notes.append("view %s: vertices unavailable (%s)" % (view.Name, exc))
    try:
        for i, e in enumerate(view.getVisibleEdges()):
            curve = getattr(e, "Curve", None)
            kind = type(curve).__name__
            if kind == "Circle":
                c = curve.Center
                closed = getattr(e, "isClosed", lambda: False)()
                edges.append(Edge(i + 1, "circle" if closed else "arc", center=(x + c.x * scale, y + c.y * scale),
                                  radius=float(curve.Radius) * scale, closed=closed))
            else:
                a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
                edges.append(Edge(i + 1, "line", (x + a.x * scale, y + a.y * scale), (x + b.x * scale, y + b.y * scale)))
    except Exception as exc:
        notes.append("view %s: edges unavailable (%s)" % (view.Name, exc))
    return ViewGeometry(view.Name, x, y, scale, verts, edges)


def make_dimension(spec, page, document=None, notes=None):
    """Create the TechDraw dimension for ``spec``; returns the object or None."""
    notes = notes if notes is not None else []
    App = _freecad()
    if App is None:
        notes.append("FreeCAD not available; dimension kept as a preview only")
        return None
    doc = document or getattr(page, "Document", None) or App.ActiveDocument
    if doc is None:
        notes.append("no document")
        return None
    try:
        dim = doc.addObject("TechDraw::DrawViewDimension", "Dimension")
        dim.Type = spec.type
        dim.MeasureType = "Projected"
        refs = []
        for view_name, sub in spec.references:
            view = doc.getObject(view_name)
            if view is None:
                notes.append("view %s not found" % view_name)
                doc.removeObject(dim.Name)
                return None
            refs.append((view, sub))
        dim.References2D = refs
        if spec.text_position is not None:
            view = doc.getObject(spec.references[0][0])
            vx = float(getattr(view.X, "Value", view.X))
            vy = float(getattr(view.Y, "Value", view.Y))
            dim.X = spec.text_position[0] - vx
            dim.Y = spec.text_position[1] - vy
        page.addView(dim)
        doc.recompute()
        return dim
    except Exception as exc:
        notes.append("TechDraw refused the dimension: %s" % exc)
        return None


def page_image(page, width=2048, notes=None):
    """PNG bytes of the page, or None. Needs FreeCADGui and Qt SVG."""
    notes = notes if notes is not None else []
    try:
        import FreeCADGui
        import TechDrawGui
        from PySide import QtCore, QtGui, QtSvg
    except ImportError as exc:
        notes.append("page rendering needs the GUI and Qt SVG (%s)" % exc)
        return None
    import os
    import tempfile

    tmp = tempfile.NamedTemporaryFile(suffix=".svg", delete=False)
    tmp.close()
    try:
        TechDrawGui.exportPageAsSvg(page, tmp.name)
        renderer = QtSvg.QSvgRenderer(tmp.name)
        w_mm = float(getattr(page.Template, "Width", 420.0))
        h_mm = float(getattr(page.Template, "Height", 297.0))
        height = int(width * h_mm / w_mm)
        image = QtGui.QImage(width, height, QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor(255, 255, 255))
        painter = QtGui.QPainter(image)
        renderer.render(painter)
        painter.end()
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(buffer.data())
    except Exception as exc:
        notes.append("page rendering failed: %s" % exc)
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
