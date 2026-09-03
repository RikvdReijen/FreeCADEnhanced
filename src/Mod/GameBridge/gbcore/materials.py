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
"""Converting FreeCAD's Phong appearance into a metallic-roughness material.

FreeCAD describes a surface the way OpenGL 1.x did: ambient, diffuse, specular,
emissive and a shininess exponent.  Unreal, Unity's scriptable pipelines and
Blender's Principled BSDF all want base colour, metallic and roughness instead.
The mapping is lossy in one direction only - there is no Phong appearance that
means "metal" - so the heuristics below are conservative: they keep the colour
faithful and only claim metalness when the appearance really looks like it.
"""

import math

from .scene import Material

__all__ = [
    "phong_to_pbr",
    "shininess_to_roughness",
    "material_from_appearance",
    "materials_from_object",
    "DEFAULT_MATERIAL",
]

#: What an object with no appearance at all becomes: FreeCAD's own default grey.
DEFAULT_MATERIAL = Material(
    "FreeCAD_Default", base_color=(0.8, 0.8, 0.8, 1.0), metallic=0.0, roughness=0.55
)


def _rgb(value, default=(0.8, 0.8, 0.8)):
    """Accept a FreeCAD colour tuple, a Base.Color or an (r, g, b[, a]) tuple."""
    if value is None:
        return default
    if all(hasattr(value, a) for a in ("r", "g", "b")):
        return (float(value.r), float(value.g), float(value.b))
    try:
        parts = [float(v) for v in value]
    except (TypeError, ValueError):
        return default
    if len(parts) < 3:
        return default
    return tuple(max(0.0, min(1.0, v)) for v in parts[:3])


def _alpha(value, default=1.0):
    if value is None:
        return default
    if hasattr(value, "a"):
        return float(value.a)
    try:
        parts = [float(v) for v in value]
    except (TypeError, ValueError):
        return default
    return parts[3] if len(parts) > 3 else default


def shininess_to_roughness(shininess):
    """Map FreeCAD's 0..1 shininess onto a perceptual roughness.

    FreeCAD normalises the OpenGL exponent to 0..1, where 1 is a mirror.  The
    usual inversion ``roughness = 1 - shininess`` makes everything in the middle
    look like sandblasted plastic; taking the square root of the complement
    keeps a default part (shininess 0.2) at a believable 0.89 while still
    letting a polished one get properly sharp.
    """
    s = max(0.0, min(1.0, float(shininess)))
    return math.sqrt(1.0 - s)


def phong_to_pbr(diffuse, specular=None, shininess=0.2, emissive=None, transparency=0.0):
    """The actual conversion, kept free of FreeCAD types so it can be tested.

    Returns ``(base_color, metallic, roughness, emissive)``.
    """
    diffuse_rgb = _rgb(diffuse)
    specular_rgb = _rgb(specular, (0.0, 0.0, 0.0))
    emissive_rgb = _rgb(emissive, (0.0, 0.0, 0.0))
    roughness = shininess_to_roughness(shininess)

    # A metal in the Phong world is dark diffuse with a strong, *tinted*
    # specular: brushed aluminium is grey-on-grey, gold is dark yellow with a
    # yellow highlight.  A plastic is bright diffuse with a white highlight.
    # Anything that does not fit that shape stays dielectric.
    diffuse_level = max(diffuse_rgb)
    specular_level = max(specular_rgb)
    metallic = 0.0
    if specular_level > 0.35 and diffuse_level < 0.6:
        tinted = _chromatic_distance(specular_rgb, diffuse_rgb) < 0.25
        if tinted or specular_level > 0.75:
            metallic = min(1.0, (specular_level - 0.35) / 0.5)
    base_color = diffuse_rgb
    if metallic > 0.5:
        # For a metal the base colour is the reflectance, which Phong keeps in
        # the specular term, so blend towards it as confidence rises.
        base_color = tuple(
            d * (1.0 - metallic) + s * metallic
            for d, s in zip(diffuse_rgb, specular_rgb)
        )
    alpha = 1.0 - max(0.0, min(1.0, float(transparency)))
    return (base_color + (alpha,), metallic, roughness, emissive_rgb)


def _chromatic_distance(a, b):
    """How differently two colours are tinted, ignoring their brightness."""

    def normalise(c):
        peak = max(c)
        return (1.0, 1.0, 1.0) if peak <= 1e-6 else tuple(v / peak for v in c)

    na, nb = normalise(a), normalise(b)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(na, nb)))


def material_from_appearance(appearance, name="Material", source=None, transparency=None):
    """Build a :class:`~gbcore.scene.Material` from a FreeCAD appearance.

    ``appearance`` is one entry of an object's ``ShapeAppearance`` - a
    ``FreeCAD.Material`` with ``DiffuseColor``, ``SpecularColor``, ``Shininess``
    and friends.  A plain ``(r, g, b[, a])`` tuple works too, which is how the
    per-face ``DiffuseColor`` list is handled.

    ``transparency`` is a fraction from 0 to 1.  It is deliberately not guessed
    from the magnitude: a view provider states transparency out of 100, and
    ``1`` would then mean either one per cent or completely invisible.
    """
    if appearance is None:
        return Material(
            name,
            DEFAULT_MATERIAL.base_color,
            DEFAULT_MATERIAL.metallic,
            DEFAULT_MATERIAL.roughness,
            source=source,
        )

    if isinstance(appearance, (tuple, list)):
        diffuse = appearance
        specular = None
        shininess = 0.2
        emissive = None
        alpha_transparency = 1.0 - _alpha(appearance)
    else:
        diffuse = getattr(appearance, "DiffuseColor", None)
        specular = getattr(appearance, "SpecularColor", None)
        shininess = getattr(appearance, "Shininess", 0.2)
        emissive = getattr(appearance, "EmissiveColor", None)
        alpha_transparency = float(getattr(appearance, "Transparency", 0.0) or 0.0)
        if not alpha_transparency:
            alpha_transparency = 1.0 - _alpha(diffuse)

    if transparency is not None:
        alpha_transparency = max(0.0, min(1.0, float(transparency)))

    base_color, metallic, roughness, emissive_rgb = phong_to_pbr(
        diffuse, specular, shininess, emissive, alpha_transparency
    )
    return Material(
        name,
        base_color=base_color,
        metallic=metallic,
        roughness=roughness,
        emissive=emissive_rgb,
        alpha_mode="BLEND" if base_color[3] < 0.999 else "OPAQUE",
        source=source,
    )


def materials_from_object(obj):
    """Read every appearance an object carries, in face-assignment order.

    A FreeCAD solid can be painted per face, in which case the view provider
    holds a ``ShapeAppearance`` (or, on older documents, a ``DiffuseColor``)
    list with one entry per face.  The exporter needs them in that order so it
    can split the tessellation into one mesh part per material.
    """
    view = getattr(obj, "ViewObject", None)
    label = getattr(obj, "Label", None) or getattr(obj, "Name", "Material")
    source = getattr(obj, "Name", None)
    if view is None:
        return [material_from_appearance(None, str(label), source)]

    # A view provider states transparency as a percentage; everything below
    # this line works in fractions.
    transparency = (getattr(view, "Transparency", 0) or 0) / 100.0

    for attribute in ("ShapeAppearance", "DiffuseColor"):
        appearances = getattr(view, attribute, None)
        if not appearances:
            continue
        try:
            entries = list(appearances)
        except TypeError:
            entries = [appearances]
        if not entries:
            continue
        return [
            material_from_appearance(
                entry,
                "%s_%d" % (label, index) if len(entries) > 1 else str(label),
                source,
                transparency,
            )
            for index, entry in enumerate(entries)
        ]

    return [
        material_from_appearance(
            getattr(view, "ShapeColor", None), str(label), source, transparency
        )
    ]
