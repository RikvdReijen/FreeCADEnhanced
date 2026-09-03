# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2026 FreeCAD XR contributors                            *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2.1 of   *
# *   the License, or (at your option) any later version.                   *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# ***************************************************************************
"""Turn a declarative environment spec into a Coin3D scenegraph.

``pivy.coin`` is imported lazily *inside* the functions so the rest of
:mod:`xrenv` stays unit testable without FreeCAD (see §6 of the architecture
document).

The geometry comes from :func:`xrenv.spec.tessellate_shape`, the very same
tessellator the Quest GLES renderer mirrors, so the desktop preview and the
headset show identical triangles.

Coordinate systems
------------------
The spec is authored **Y up** (OpenXR).  Coin/FreeCAD is **Z up**.  The whole
conversion happens in exactly one place, :func:`spec_to_coin_matrix`, which is
applied once at the root of the returned scenegraph.  Everything below that
node stays in unmodified spec coordinates, so node translations, light
directions and anchor positions never need per-call fixing up.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple

from . import spec as spec_mod

__all__ = [
    "build_coin",
    "spec_to_coin_matrix",
    "coin_available",
    "yup_to_zup",
    "zup_to_yup",
]


# ---------------------------------------------------------------------------
# the one and only Y-up  <->  Z-up conversion
# ---------------------------------------------------------------------------
#
# Spec (OpenXR):  +X right, +Y up,      +Z towards the viewer
# Coin/FreeCAD:   +X right, +Z up,      +Y away from the viewer
#
# The mapping is a right handed +90 degree rotation about X:
#
#     x_coin = x_spec
#     y_coin = -z_spec
#     z_coin =  y_spec
#
# stored row-major below; ``spec_to_coin_matrix()`` hands it to Coin in the
# transposed (column-major) layout SbMatrix expects.

_YUP_TO_ZUP: Tuple[Tuple[float, float, float], ...] = (
    (1.0, 0.0, 0.0),
    (0.0, 0.0, -1.0),
    (0.0, 1.0, 0.0),
)


def yup_to_zup(p: Sequence[float]) -> Tuple[float, float, float]:
    """Convert a spec (Y up) point or direction to Coin/FreeCAD (Z up)."""
    return (float(p[0]), -float(p[2]), float(p[1]))


def zup_to_yup(p: Sequence[float]) -> Tuple[float, float, float]:
    """Convert a Coin/FreeCAD (Z up) point or direction back to spec (Y up)."""
    return (float(p[0]), float(p[2]), -float(p[1]))


def coin_available() -> bool:
    """True when ``pivy.coin`` can be imported in this interpreter."""
    try:
        import pivy.coin  # noqa: F401  (import is the probe)
    except Exception:
        return False
    return True


def spec_to_coin_matrix():
    """Return the spec (Y up) -> Coin (Z up) basis change as an ``SbMatrix``."""
    from pivy import coin

    m = coin.SbMatrix()
    r = _YUP_TO_ZUP
    # SbMatrix.setValue takes rows of the *transform* matrix as Coin applies
    # ``vec * matrix`` (row vectors), so the rotation part is transposed.
    m.setValue(
        r[0][0], r[1][0], r[2][0], 0.0,
        r[0][1], r[1][1], r[2][1], 0.0,
        r[0][2], r[1][2], r[2][2], 0.0,
        0.0, 0.0, 0.0, 1.0,
    )
    return m


# ---------------------------------------------------------------------------
# materials and lights
# ---------------------------------------------------------------------------


def _make_material(coin, mat: Dict[str, Any]):
    """Map a spec PBR material onto Coin's Phong ``SoMaterial``."""
    node = coin.SoMaterial()
    base = list(mat.get("base_color", [0.8, 0.8, 0.8, 1.0]))
    while len(base) < 4:
        base.append(1.0)
    r, g, b, a = (float(v) for v in base[:4])
    metallic = float(mat.get("metallic", 0.0))
    roughness = max(1e-3, min(1.0, float(mat.get("roughness", 0.6))))
    emissive = list(mat.get("emissive", [0.0, 0.0, 0.0]))
    while len(emissive) < 3:
        emissive.append(0.0)

    # Metals tint their specular highlight with the base colour and darken
    # their diffuse term; dielectrics keep a neutral 4% highlight.
    diffuse = (r * (1.0 - 0.85 * metallic), g * (1.0 - 0.85 * metallic), b * (1.0 - 0.85 * metallic))
    spec_str = 0.04 + 0.96 * metallic
    specular = (
        spec_str * (metallic * r + (1.0 - metallic)),
        spec_str * (metallic * g + (1.0 - metallic)),
        spec_str * (metallic * b + (1.0 - metallic)),
    )
    node.diffuseColor.setValue(*diffuse)
    node.specularColor.setValue(*specular)
    node.ambientColor.setValue(r * 0.25, g * 0.25, b * 0.25)
    node.emissiveColor.setValue(float(emissive[0]), float(emissive[1]), float(emissive[2]))
    # Coin shininess 0..1, inverse of roughness with a mild curve.
    node.shininess.setValue(max(0.0, min(1.0, (1.0 - roughness) ** 1.5)))
    node.transparency.setValue(max(0.0, min(1.0, 1.0 - a)))
    return node


def _make_light(coin, light: Dict[str, Any]):
    ltype = light.get("type", "directional")
    color = list(light.get("color", [1.0, 1.0, 1.0]))
    while len(color) < 3:
        color.append(1.0)
    intensity = float(light.get("intensity", 1.0))

    if ltype == "directional":
        node = coin.SoDirectionalLight()
        d = list(light.get("direction", [0.0, -1.0, 0.0]))
        node.direction.setValue(float(d[0]), float(d[1]), float(d[2]))
    elif ltype == "point":
        node = coin.SoPointLight()
        p = list(light.get("position", [0.0, 0.0, 0.0]))
        node.location.setValue(float(p[0]), float(p[1]), float(p[2]))
    elif ltype == "spot":
        node = coin.SoSpotLight()
        p = list(light.get("position", [0.0, 0.0, 0.0]))
        d = list(light.get("direction", [0.0, -1.0, 0.0]))
        node.location.setValue(float(p[0]), float(p[1]), float(p[2]))
        node.direction.setValue(float(d[0]), float(d[1]), float(d[2]))
        node.cutOffAngle.setValue(math.radians(float(light.get("cutoff_deg", 45.0))))
        node.dropOffRate.setValue(0.3)
    else:
        return None

    node.color.setValue(float(color[0]), float(color[1]), float(color[2]))
    # Coin clamps intensity to 0..1; brighter lights are expressed by tinting
    # the colour up so authoring stays physical.
    if intensity > 1.0:
        scale = min(4.0, intensity)
        node.color.setValue(
            min(1.0, float(color[0]) * scale),
            min(1.0, float(color[1]) * scale),
            min(1.0, float(color[2]) * scale),
        )
        node.intensity.setValue(1.0)
    else:
        node.intensity.setValue(max(0.0, intensity))
    node.on.setValue(True)
    return node


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------


def _shape_key(shape: Dict[str, Any]) -> str:
    """A stable key so identical primitives share one tessellation."""
    import json

    try:
        return json.dumps(shape, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(shape)


def _build_shape_node(coin, shape: Dict[str, Any]):
    """Tessellate ``shape`` into an ``SoSeparator`` holding an indexed face set."""
    positions, normals, uvs, indices = spec_mod.tessellate_shape(shape)

    sep = coin.SoSeparator()
    sep.setName("xrenv_geom")

    hints = coin.SoShapeHints()
    hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
    hints.shapeType = coin.SoShapeHints.SOLID
    hints.faceType = coin.SoShapeHints.CONVEX
    hints.creaseAngle = 0.0
    sep.addChild(hints)

    nverts = len(positions) // 3
    coords = coin.SoCoordinate3()
    coords.point.setValues(
        0, nverts,
        [(positions[3 * i], positions[3 * i + 1], positions[3 * i + 2])
         for i in range(nverts)],
    )
    sep.addChild(coords)

    nbind = coin.SoNormalBinding()
    nbind.value = coin.SoNormalBinding.PER_VERTEX_INDEXED
    sep.addChild(nbind)

    nrm = coin.SoNormal()
    nrm.vector.setValues(
        0, nverts,
        [(normals[3 * i], normals[3 * i + 1], normals[3 * i + 2])
         for i in range(nverts)],
    )
    sep.addChild(nrm)

    if uvs and len(uvs) == nverts * 2:
        tbind = coin.SoTextureCoordinateBinding()
        tbind.value = coin.SoTextureCoordinateBinding.PER_VERTEX_INDEXED
        sep.addChild(tbind)
        tc = coin.SoTextureCoordinate2()
        tc.point.setValues(
            0, nverts, [(uvs[2 * i], uvs[2 * i + 1]) for i in range(nverts)])
        sep.addChild(tc)

    faces = coin.SoIndexedFaceSet()
    ntris = len(indices) // 3
    coord_index = []
    for t in range(ntris):
        coord_index.extend(
            (indices[3 * t], indices[3 * t + 1], indices[3 * t + 2], -1))
    faces.coordIndex.setValues(0, len(coord_index), coord_index)
    # An empty normalIndex/textureCoordIndex makes Coin reuse coordIndex.
    sep.addChild(faces)
    return sep


def _is_identity_trs(node: Dict[str, Any]) -> bool:
    t = node.get("translation", (0.0, 0.0, 0.0))
    r = node.get("rotation", (0.0, 0.0, 0.0, 1.0))
    s = node.get("scale", (1.0, 1.0, 1.0))
    return (
        abs(t[0]) < 1e-12 and abs(t[1]) < 1e-12 and abs(t[2]) < 1e-12
        and abs(r[0]) < 1e-12 and abs(r[1]) < 1e-12 and abs(r[2]) < 1e-12 and abs(abs(r[3]) - 1.0) < 1e-9
        and abs(s[0] - 1.0) < 1e-12 and abs(s[1] - 1.0) < 1e-12 and abs(s[2] - 1.0) < 1e-12
    )


def _build_node(coin, node: Dict[str, Any], materials: List[Any],
                cache: Dict[str, Any], stats: Dict[str, int]):
    sep = coin.SoSeparator()
    name = node.get("name") or ""
    if name:
        # Coin node names must be valid identifiers.
        safe = "".join(c if (c.isalnum() or c == "_") else "_" for c in name)
        if safe and not safe[0].isdigit():
            sep.setName(safe)

    if not _is_identity_trs(node):
        tr = coin.SoTransform()
        t = node.get("translation", (0.0, 0.0, 0.0))
        r = node.get("rotation", (0.0, 0.0, 0.0, 1.0))
        s = node.get("scale", (1.0, 1.0, 1.0))
        tr.translation.setValue(float(t[0]), float(t[1]), float(t[2]))
        tr.rotation.setValue(float(r[0]), float(r[1]), float(r[2]), float(r[3]))
        tr.scaleFactor.setValue(float(s[0]), float(s[1]), float(s[2]))
        sep.addChild(tr)

    mat_index = node.get("material")
    if isinstance(mat_index, int) and 0 <= mat_index < len(materials):
        sep.addChild(materials[mat_index])

    shape = node.get("shape")
    if shape is not None:
        key = _shape_key(shape)
        geom = cache.get(key)
        if geom is None:
            geom = _build_shape_node(coin, shape)
            cache[key] = geom
            stats["tessellated"] = stats.get("tessellated", 0) + 1
        else:
            stats["reused"] = stats.get("reused", 0) + 1
        sep.addChild(geom)
        stats["parts"] = stats.get("parts", 0) + 1

    for child in node.get("children") or []:
        sep.addChild(_build_node(coin, child, materials, cache, stats))
    return sep


def build_coin(spec: Dict[str, Any], validate: bool = True, add_lights: bool = True):
    """Build a Coin3D scenegraph for ``spec``.

    Parameters
    ----------
    spec:
        A declarative environment spec (see :mod:`xrenv.spec`).
    validate:
        Raise :class:`ValueError` when the spec does not validate.
    add_lights:
        Include the spec's lights in the graph.  Set to ``False`` when the
        surrounding viewer supplies its own lighting.

    Returns an ``SoSeparator`` whose contents are in **FreeCAD/Coin Z-up**
    coordinates: the root carries the single Y-up to Z-up basis change from
    :func:`spec_to_coin_matrix`.

    Identical shape primitives share one tessellated ``SoSeparator`` (Coin
    reference counts nodes), which keeps a several-hundred-part environment
    such as ``bambu_x1c`` down to a few dozen unique geometry nodes.
    """
    if validate:
        problems = spec_mod.validate_spec(spec)
        if problems:
            raise ValueError(
                "invalid environment spec %r:\n  %s"
                % (spec.get("id"), "\n  ".join(problems[:20]))
            )

    from pivy import coin

    root = coin.SoSeparator()
    root.setName("xrenv_root")
    root.renderCaching = coin.SoSeparator.ON
    root.boundingBoxCaching = coin.SoSeparator.ON

    basis = coin.SoMatrixTransform()
    basis.matrix.setValue(spec_to_coin_matrix())
    root.addChild(basis)

    if add_lights:
        env = coin.SoEnvironment()
        amb = list(spec.get("ambient", [0.1, 0.1, 0.1]))
        while len(amb) < 3:
            amb.append(0.0)
        strength = max(float(amb[0]), float(amb[1]), float(amb[2]))
        env.ambientIntensity.setValue(max(0.0, min(1.0, strength)))
        env.ambientColor.setValue(float(amb[0]), float(amb[1]), float(amb[2]))
        root.addChild(env)
        for light in spec.get("lights") or []:
            node = _make_light(coin, light)
            if node is not None:
                root.addChild(node)

    materials = [_make_material(coin, m) for m in (spec.get("materials") or [])]

    cache: Dict[str, Any] = {}
    stats: Dict[str, int] = {}
    body = coin.SoSeparator()
    body.setName("xrenv_body")
    body.renderCaching = coin.SoSeparator.ON
    for node in spec.get("nodes") or []:
        body.addChild(_build_node(coin, node, materials, cache, stats))
    root.addChild(body)
    return root


# Backwards friendly alias used by the workbench GUI layer.
build_scenegraph = build_coin
