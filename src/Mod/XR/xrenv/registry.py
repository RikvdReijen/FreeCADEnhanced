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
"""Discovery and lookup of VR background environments.

Environments come from three places, searched in this order of precedence:

1. ``~/.FreeCAD/xr/environments/*.json`` — the user's own drop-in specs.  A
   file here shadows a built-in with the same ``id``, which is how a user
   customises the shipped printer or laser cutter.
2. the built-in generator modules in :mod:`xrenv.environments` — Python is the
   source of truth inside the repository, so an edited generator shows up
   immediately without regenerating the JSON.
3. ``Resources/environments/*.json`` — the generated specs that ship with the
   workbench and are copied into the Quest APK.  These cover any id that has
   no generator module (e.g. a spec added by a third party add-on).

Listing order is stable: built-ins first, in their declared order, then the
extra Resources specs, then the user's, each alphabetically.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import spec as spec_mod
from .spec import Anchor

__all__ = [
    "EnvironmentInfo",
    "Environment",
    "list_environments",
    "get",
    "register",
    "unregister",
    "refresh",
    "ids",
    "has",
    "resources_dir",
    "user_environment_dir",
    "BUILTIN_ORDER",
    "PRIMARY_ANCHOR_NAMES",
]

#: The order built-in environments are offered to the user in.
BUILTIN_ORDER: Tuple[str, ...] = (
    "bambu_x1c",
    "laser_cutter",
    "workshop",
    "studio",
    "void",
)

#: Anchor names that count as "the surface you drop the document on".
PRIMARY_ANCHOR_NAMES: Tuple[str, ...] = (
    "build_plate",
    "bed_surface",
    "worktable",
    "table_surface",
    "workbench",
)

SOURCE_BUILTIN = "builtin"
SOURCE_RESOURCE = "resource"
SOURCE_USER = "user"
SOURCE_RUNTIME = "runtime"

_SOURCE_RANK = {
    SOURCE_RUNTIME: 0,   # explicitly registered at runtime wins outright
    SOURCE_USER: 1,
    SOURCE_BUILTIN: 2,
    SOURCE_RESOURCE: 3,
}


def _module_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def resources_dir() -> str:
    """``src/Mod/XR/Resources/environments`` — the shipped specs."""
    return os.path.abspath(
        os.path.join(_module_dir(), os.pardir, "Resources", "environments")
    )


def user_environment_dir() -> str:
    """``~/.FreeCAD/xr/environments`` — the user's drop-in specs.

    Honours ``$FREECAD_USER_HOME`` and ``$XRENV_USER_DIR`` so tests and
    portable installs can redirect it.
    """
    override = os.environ.get("XRENV_USER_DIR")
    if override:
        return os.path.abspath(override)
    home = os.environ.get("FREECAD_USER_HOME") or os.path.expanduser("~")
    return os.path.join(home, ".FreeCAD", "xr", "environments")


# ---------------------------------------------------------------------------
# public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnvironmentInfo:
    """Lightweight description of an environment, cheap to list."""

    id: str
    name: str
    description: str = ""
    user_scale: float = 1.0
    bounds: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    part_count: int = 0
    spawn: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    source: str = SOURCE_BUILTIN
    path: Optional[str] = None

    def __str__(self) -> str:  # pragma: no cover - display helper
        return "%s (%s)" % (self.name, self.id)


class Environment:
    """A background environment: its spec, anchors and Coin scenegraph.

    The spec is produced lazily — listing every environment does not run every
    generator — and cached afterwards.  The Coin scenegraph is built at most
    once and re-handed out on later calls.
    """

    def __init__(
        self,
        env_id: str,
        name: str = "",
        description: str = "",
        spec: Optional[Dict[str, Any]] = None,
        loader: Optional[Callable[[], Dict[str, Any]]] = None,
        source: str = SOURCE_RUNTIME,
        path: Optional[str] = None,
    ) -> None:
        if not env_id:
            raise ValueError("an environment needs an id")
        self.id = env_id
        self.source = source
        self.path = path
        self._spec: Optional[Dict[str, Any]] = spec
        self._loader = loader
        self._scenegraph: Any = None
        self._name = name
        self._description = description
        if spec is None and loader is None:
            raise ValueError("environment %r needs either a spec or a loader" % env_id)

    # -- spec --------------------------------------------------------------

    @property
    def spec(self) -> Dict[str, Any]:
        """The declarative spec dict (§2 of the architecture document)."""
        if self._spec is None:
            assert self._loader is not None
            built = self._loader()
            if not isinstance(built, dict):
                raise TypeError(
                    "environment %r generator returned %s, expected a dict"
                    % (self.id, type(built).__name__)
                )
            self._spec = built
        return self._spec

    @property
    def loaded(self) -> bool:
        return self._spec is not None

    # -- metadata ----------------------------------------------------------

    @property
    def name(self) -> str:
        if self._name:
            return self._name
        return str(self.spec.get("name") or self.id)

    @property
    def description(self) -> str:
        if self._description:
            return self._description
        return str(self.spec.get("description") or "")

    @property
    def user_scale(self) -> float:
        try:
            return float(self.spec.get("user_scale", 1.0))
        except (TypeError, ValueError):
            return 1.0

    @property
    def spawn(self) -> Tuple[float, float, float]:
        s = self.spec.get("spawn", (0.0, 0.0, 0.0))
        try:
            return (float(s[0]), float(s[1]), float(s[2]))
        except (TypeError, IndexError, ValueError):
            return (0.0, 0.0, 0.0)

    @property
    def bounds(self) -> Tuple[float, float, float]:
        b = self.spec.get("bounds", (1.0, 1.0, 1.0))
        try:
            return (float(b[0]), float(b[1]), float(b[2]))
        except (TypeError, IndexError, ValueError):
            return (1.0, 1.0, 1.0)

    @property
    def part_count(self) -> int:
        return spec_mod.count_parts(self.spec)

    @property
    def anchors(self) -> Dict[str, Anchor]:
        """Named anchors, as :class:`xrenv.spec.Anchor` objects."""
        raw = self.spec.get("anchors") or {}
        return {k: Anchor.from_dict(v, k) for k, v in raw.items()}

    def anchor(self, name: str) -> Optional[Anchor]:
        raw = (self.spec.get("anchors") or {}).get(name)
        if raw is None:
            return None
        return Anchor.from_dict(raw, name)

    def primary_anchor(self) -> Optional[Anchor]:
        """The build plate / bed.  ``None`` for the neutral environments."""
        raw = self.spec.get("anchors") or {}
        for candidate in PRIMARY_ANCHOR_NAMES:
            if candidate in raw:
                return Anchor.from_dict(raw[candidate], candidate)
        return None

    @property
    def info(self) -> EnvironmentInfo:
        return EnvironmentInfo(
            id=self.id,
            name=self.name,
            description=self.description,
            user_scale=self.user_scale,
            bounds=self.bounds,
            part_count=self.part_count,
            spawn=self.spawn,
            source=self.source,
            path=self.path,
        )

    # -- validation & geometry --------------------------------------------

    def validate(self) -> List[str]:
        """Problems with this environment's spec; empty means valid."""
        return spec_mod.validate_spec(self.spec)

    def build_scenegraph(self, rebuild: bool = False, **kwargs: Any) -> Any:
        """Build (and cache) the Coin3D scenegraph.

        Returns ``None`` when ``pivy.coin`` is not importable — the desktop
        preview is optional, the spec itself is not.
        """
        if self._scenegraph is not None and not rebuild:
            return self._scenegraph
        from . import builder

        if not builder.coin_available():
            return None
        self._scenegraph = builder.build_coin(self.spec, **kwargs)
        return self._scenegraph

    def release_scenegraph(self) -> None:
        """Drop the cached Coin graph (called when switching environments)."""
        self._scenegraph = None

    def to_json(self) -> str:
        return spec_mod.spec_to_json(self.spec)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<Environment %r source=%s loaded=%s>" % (self.id, self.source, self.loaded)


# ---------------------------------------------------------------------------
# the registry itself
# ---------------------------------------------------------------------------

_lock = threading.RLock()
_registry: "Dict[str, Environment]" = {}
_order: List[str] = []
_scanned = False


def _insert(env: Environment, replace_weaker: bool = True) -> bool:
    """Insert ``env``, honouring source precedence.  Returns True if stored."""
    existing = _registry.get(env.id)
    if existing is not None:
        if not replace_weaker:
            return False
        if _SOURCE_RANK.get(env.source, 9) >= _SOURCE_RANK.get(existing.source, 9):
            return False
        # keep the listing position of the entry we replace
        _registry[env.id] = env
        return True
    _registry[env.id] = env
    _order.append(env.id)
    return True


def _builtin_module_ids() -> List[str]:
    """All generator modules in :mod:`xrenv.environments`, ordered."""
    try:
        from . import environments as env_pkg
    except Exception:
        return []
    found: List[str] = []
    try:
        for mod in pkgutil.iter_modules(env_pkg.__path__):
            if mod.name.startswith("_"):
                continue
            found.append(mod.name)
    except Exception:
        return []
    ordered = [i for i in BUILTIN_ORDER if i in found]
    ordered.extend(sorted(i for i in found if i not in BUILTIN_ORDER))
    return ordered


def _make_module_loader(module_name: str) -> Callable[[], Dict[str, Any]]:
    def _load() -> Dict[str, Any]:
        mod = importlib.import_module("xrenv.environments." + module_name)
        builder_fn = getattr(mod, "build", None)
        if not callable(builder_fn):
            raise AttributeError(
                "xrenv.environments.%s has no build() function" % module_name
            )
        return builder_fn()

    return _load


def _module_metadata(module_name: str) -> Tuple[str, str, str]:
    """``(id, name, description)`` without running the generator."""
    try:
        mod = importlib.import_module("xrenv.environments." + module_name)
    except Exception:
        return (module_name, module_name, "")
    return (
        str(getattr(mod, "ENVIRONMENT_ID", module_name)),
        str(getattr(mod, "ENVIRONMENT_NAME", module_name)),
        str(getattr(mod, "ENVIRONMENT_DESCRIPTION", "")),
    )


def _make_json_loader(path: str) -> Callable[[], Dict[str, Any]]:
    def _load() -> Dict[str, Any]:
        return spec_mod.load_spec(path)

    return _load


def _scan_json_dir(directory: str, source: str) -> None:
    if not directory or not os.path.isdir(directory):
        return
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".json"))
    except OSError:
        return
    for name in names:
        path = os.path.join(directory, name)
        env_id = os.path.splitext(name)[0]
        display = env_id
        description = ""
        # peek at the header without keeping the whole spec in memory
        try:
            data = spec_mod.load_spec(path)
            env_id = str(data.get("id") or env_id)
            display = str(data.get("name") or env_id)
            description = str(data.get("description") or "")
            env = Environment(env_id, display, description, spec=data,
                              source=source, path=path)
        except Exception:
            env = Environment(env_id, display, description,
                              loader=_make_json_loader(path), source=source, path=path)
        _insert(env)


def _ensure_package_importable() -> None:
    """Make ``import xrenv.environments.<x>`` work when run from ``Mod/XR``."""
    pkg_parent = os.path.abspath(os.path.join(_module_dir(), os.pardir))
    if pkg_parent not in sys.path:
        sys.path.insert(0, pkg_parent)


def refresh(force: bool = True) -> None:
    """Rescan every environment source.

    Runtime registrations made through :func:`register` survive a refresh.
    """
    global _scanned
    with _lock:
        if _scanned and not force:
            return
        keep = {k: v for k, v in _registry.items() if v.source == SOURCE_RUNTIME}
        keep_order = [i for i in _order if i in keep]
        _registry.clear()
        _order.clear()
        _registry.update(keep)
        _order.extend(keep_order)

        _ensure_package_importable()

        # 1. built-in generator modules (declared order)
        for module_name in _builtin_module_ids():
            env_id, display, description = _module_metadata(module_name)
            _insert(
                Environment(
                    env_id,
                    display,
                    description,
                    loader=_make_module_loader(module_name),
                    source=SOURCE_BUILTIN,
                    path=os.path.join(_module_dir(), "environments", module_name + ".py"),
                )
            )
        # 2. shipped JSON (adds ids without a generator module)
        _scan_json_dir(resources_dir(), SOURCE_RESOURCE)
        # 3. the user's own specs, which shadow everything above
        _scan_json_dir(user_environment_dir(), SOURCE_USER)
        _scanned = True


def _ensure_scanned() -> None:
    if not _scanned:
        refresh(force=True)


def list_environments() -> List[EnvironmentInfo]:
    """Every known environment, built-ins first, in a stable order."""
    _ensure_scanned()
    with _lock:
        order = list(_order)
        registry = dict(_registry)
    infos: List[EnvironmentInfo] = []
    for env_id in order:
        env = registry.get(env_id)
        if env is None:
            continue
        try:
            infos.append(env.info)
        except Exception:
            infos.append(
                EnvironmentInfo(id=env.id, name=env._name or env.id,
                                description=env._description, source=env.source,
                                path=env.path)
            )
    return infos


def ids() -> List[str]:
    """The ids of every known environment, in listing order."""
    _ensure_scanned()
    with _lock:
        return list(_order)


def has(env_id: str) -> bool:
    _ensure_scanned()
    with _lock:
        return env_id in _registry


def get(env_id: str) -> Environment:
    """Look up an environment by id.

    Raises ``KeyError`` naming the available ids when ``env_id`` is unknown.
    """
    _ensure_scanned()
    with _lock:
        env = _registry.get(env_id)
        known = list(_order)
    if env is None:
        raise KeyError(
            "unknown environment %r; available: %s"
            % (env_id, ", ".join(known) if known else "(none)")
        )
    return env


def register(
    environment: Any,
    env_id: Optional[str] = None,
    name: str = "",
    description: str = "",
) -> Environment:
    """Register an environment at runtime; it shadows every discovered one.

    ``environment`` may be an :class:`Environment`, a spec ``dict``, a JSON
    file path, or a zero-argument callable returning a spec dict.
    """
    _ensure_scanned()

    if isinstance(environment, Environment):
        env = environment
    elif isinstance(environment, dict):
        ident = env_id or environment.get("id")
        if not ident:
            raise ValueError("cannot register a spec without an 'id'")
        env = Environment(
            str(ident),
            name or str(environment.get("name") or ident),
            description or str(environment.get("description") or ""),
            spec=environment,
            source=SOURCE_RUNTIME,
        )
    elif isinstance(environment, str) and os.path.isfile(environment):
        data = spec_mod.load_spec(environment)
        ident = env_id or data.get("id") or os.path.splitext(os.path.basename(environment))[0]
        env = Environment(
            str(ident),
            name or str(data.get("name") or ident),
            description or str(data.get("description") or ""),
            spec=data,
            source=SOURCE_RUNTIME,
            path=environment,
        )
    elif callable(environment):
        if not env_id:
            raise ValueError("registering a generator callable requires env_id")
        env = Environment(
            env_id,
            name or env_id,
            description,
            loader=environment,
            source=SOURCE_RUNTIME,
        )
    else:
        raise TypeError(
            "register() expects an Environment, a spec dict, a JSON path or a "
            "callable, got %s" % type(environment).__name__
        )

    env.source = SOURCE_RUNTIME
    with _lock:
        if env.id in _registry:
            _registry[env.id] = env
        else:
            _registry[env.id] = env
            _order.append(env.id)
    return env


def unregister(env_id: str) -> bool:
    """Remove an environment from the registry.  Returns True if it existed."""
    with _lock:
        if env_id not in _registry:
            return False
        del _registry[env_id]
        if env_id in _order:
            _order.remove(env_id)
        return True
