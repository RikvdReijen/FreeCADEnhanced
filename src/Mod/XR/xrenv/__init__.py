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
"""``xrenv`` — VR background environments and miniaturisation.

While modelling in VR the user can swap the world around them.  Two flagship
environments put them *inside* a machine at model scale — standing on the
build plate of a CoreXY printer, or on the honeycomb bed of a CO2 laser — and
two neutral fallbacks (a photo studio and a dark void) get out of the way.

Layout
------
:mod:`xrenv.spec`
    The declarative spec of §2 of the architecture document: dataclasses,
    :func:`~xrenv.spec.validate_spec`, JSON round trip and the reference
    tessellator :func:`~xrenv.spec.tessellate_shape` that the Quest GLES
    renderer mirrors.  Pure stdlib.
:mod:`xrenv.builder`
    :func:`~xrenv.builder.build_coin` turns a spec into a Coin3D scenegraph,
    handling the single Y-up to Z-up basis change.  Imports pivy lazily.
:mod:`xrenv.registry`
    Discovery and lookup: built-in generators, the shipped JSON in
    ``Resources/environments`` and the user's own in
    ``~/.FreeCAD/xr/environments``.
:mod:`xrenv.scale`
    :class:`~xrenv.scale.ScaleController` — shrinking the user by growing the
    world, with eased transitions, clip plane adjustment and document
    placement onto an anchor.
:mod:`xrenv.environments`
    The built-in procedural generators.

Typical use::

    from xrenv import registry

    for info in registry.list_environments():
        print(info.id, info.name, info.part_count)

    env = registry.get("bambu_x1c")
    root = env.build_scenegraph()

    from xrenv.scale import ScaleController
    ctl = ScaleController()
    ctl.set_environment(env)
"""

from __future__ import annotations

from . import builder, registry, scale, spec
from .builder import build_coin, spec_to_coin_matrix, yup_to_zup, zup_to_yup
from .registry import (
    Environment,
    EnvironmentInfo,
    get,
    list_environments,
    refresh,
    register,
    unregister,
)
from .scale import FitTransform, ScaleController, fit_document_to_anchor
from .spec import (
    SPEC_VERSION,
    Anchor,
    EnvironmentSpec,
    Light,
    Material,
    Node,
    TessellationError,
    count_parts,
    load_spec,
    save_spec,
    spec_bounds,
    spec_from_json,
    spec_to_json,
    tessellate_shape,
    tessellate_spec,
    validate_spec,
)

__version__ = "1.0"

__all__ = [
    # modules
    "spec",
    "builder",
    "registry",
    "scale",
    # spec
    "SPEC_VERSION",
    "EnvironmentSpec",
    "Material",
    "Light",
    "Anchor",
    "Node",
    "TessellationError",
    "validate_spec",
    "tessellate_shape",
    "tessellate_spec",
    "spec_bounds",
    "count_parts",
    "load_spec",
    "save_spec",
    "spec_to_json",
    "spec_from_json",
    # builder
    "build_coin",
    "spec_to_coin_matrix",
    "yup_to_zup",
    "zup_to_yup",
    # registry
    "Environment",
    "EnvironmentInfo",
    "list_environments",
    "get",
    "register",
    "unregister",
    "refresh",
    # scale
    "ScaleController",
    "FitTransform",
    "fit_document_to_anchor",
    "__version__",
]
