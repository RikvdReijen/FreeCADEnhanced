# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tiny Coin3D helpers shared by the feature renderers.

Each builder returns an ``SoSeparator`` the caller parents wherever it
likes; ``set_transform`` writes a Transform into an ``SoTransform``. Kept
deliberately minimal — the point is that a bridge can show a preview line,
a marker or a peer avatar in five lines, not that this is a scene library.
"""


def _coin():
    from pivy import coin

    return coin


def make_lines(polylines, colour=(1.0, 0.8, 0.2), width=2.0):
    """Polylines as one SoLineSet. ``polylines`` is a list of point lists."""
    coin = _coin()
    sep = coin.SoSeparator()
    mat = coin.SoMaterial()
    mat.diffuseColor.setValue(*colour)
    mat.emissiveColor.setValue(*colour)
    sep.addChild(mat)
    style = coin.SoDrawStyle()
    style.lineWidth = width
    sep.addChild(style)
    coords = coin.SoCoordinate3()
    lines = coin.SoLineSet()
    points, counts = [], []
    for poly in polylines:
        if len(poly) < 2:
            continue
        points.extend(tuple(float(c) for c in p) for p in poly)
        counts.append(len(poly))
    coords.point.setValues(0, len(points), points)
    lines.numVertices.setValues(0, len(counts), counts)
    sep.addChild(coords)
    sep.addChild(lines)
    return sep


def make_marker(position=(0.0, 0.0, 0.0), colour=(1.0, 0.3, 0.3), size=0.01, shape="sphere"):
    coin = _coin()
    sep = coin.SoSeparator()
    tr = coin.SoTransform()
    tr.translation.setValue(*[float(c) for c in position])
    sep.addChild(tr)
    mat = coin.SoMaterial()
    mat.diffuseColor.setValue(*colour)
    sep.addChild(mat)
    if shape == "cube":
        node = coin.SoCube()
        node.width = node.height = node.depth = float(size)
    else:
        node = coin.SoSphere()
        node.radius = float(size) / 2.0
    sep.addChild(node)
    sep.transform = tr
    return sep


def make_label(text, position=(0.0, 0.0, 0.0), colour=(1.0, 1.0, 1.0), size=14):
    coin = _coin()
    sep = coin.SoSeparator()
    tr = coin.SoTransform()
    tr.translation.setValue(*[float(c) for c in position])
    sep.addChild(tr)
    mat = coin.SoMaterial()
    mat.diffuseColor.setValue(*colour)
    mat.emissiveColor.setValue(*colour)
    sep.addChild(mat)
    font = coin.SoFont()
    font.size = size
    sep.addChild(font)
    label = coin.SoText2()
    label.string = text
    sep.addChild(label)
    sep.transform = tr
    sep.label = label
    return sep


def make_mesh(mesh, colour=(0.7, 0.7, 0.75), transparency=0.0):
    """A TriMesh as an SoIndexedFaceSet under its own SoTransform."""
    coin = _coin()
    sep = coin.SoSeparator()
    tr = coin.SoTransform()
    sep.addChild(tr)
    mat = coin.SoMaterial()
    mat.diffuseColor.setValue(*colour)
    mat.transparency = float(transparency)
    sep.addChild(mat)
    hints = coin.SoShapeHints()
    hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
    hints.shapeType = coin.SoShapeHints.SOLID
    sep.addChild(hints)
    coords = coin.SoCoordinate3()
    coords.point.setValues(0, len(mesh.vertices), [tuple(v) for v in mesh.vertices])
    sep.addChild(coords)
    faces = coin.SoIndexedFaceSet()
    indices = []
    for a, b, c in mesh.triangles:
        indices.extend((a, b, c, -1))
    faces.coordIndex.setValues(0, len(indices), indices)
    sep.addChild(faces)
    sep.transform = tr
    return sep


def make_textured_quad(corners, png_bytes=None, colour=(0.95, 0.95, 0.9)):
    """A quad through four world points, textured with PNG bytes when given."""
    coin = _coin()
    sep = coin.SoSeparator()
    if png_bytes:
        image = _png_to_sfimage(png_bytes)
        if image is not None:
            tex = coin.SoTexture2()
            tex.image = image
            sep.addChild(tex)
    mat = coin.SoMaterial()
    mat.diffuseColor.setValue(*colour)
    sep.addChild(mat)
    coords = coin.SoCoordinate3()
    coords.point.setValues(0, 4, [tuple(float(c) for c in p) for p in corners])
    sep.addChild(coords)
    tc = coin.SoTextureCoordinate2()
    tc.point.setValues(0, 4, [(0, 0), (1, 0), (1, 1), (0, 1)])
    sep.addChild(tc)
    faces = coin.SoFaceSet()
    faces.numVertices.setValue(4)
    sep.addChild(faces)
    return sep


def _png_to_sfimage(png_bytes):
    """Decode PNG bytes through Qt into an SoSFImage-compatible tuple."""
    try:
        from PySide import QtGui
        from pivy import coin
    except ImportError:
        return None
    image = QtGui.QImage()
    if not image.loadFromData(png_bytes, "PNG"):
        return None
    image = image.convertToFormat(QtGui.QImage.Format_RGBA8888).mirrored(False, True)
    w, h = image.width(), image.height()
    data = bytes(image.constBits())[: w * h * 4]
    sf = coin.SoSFImage()
    sf.setValue(coin.SbVec2s(w, h), 4, data)
    return sf


def set_transform(node, transform):
    """Write a Transform (metres) into an SoTransform."""
    coin = _coin()
    t, q = transform.translation, transform.rotation
    node.translation.setValue(coin.SbVec3f(float(t[0]), float(t[1]), float(t[2])))
    node.rotation.setValue(coin.SbRotation(float(q[0]), float(q[1]), float(q[2]), float(q[3])))
    if abs(transform.scale - 1.0) > 1e-9:
        node.scaleFactor.setValue(transform.scale, transform.scale, transform.scale)


def clear(separator):
    separator.removeAllChildren()
