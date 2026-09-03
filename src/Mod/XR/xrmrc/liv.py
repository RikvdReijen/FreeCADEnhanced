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
"""LIV integration — what is actually possible, and what is not.

The short version, established from primary sources and written up with URLs in
``Resources/doc/MIXED_REALITY_CAPTURE.md``:

* **There is no public native LIV SDK.**  LIV ships an SDK for Unity and one
  for Unreal.  Both are obtained from a developer portal behind a login, are
  Windows-only, and — per LIV's own Unreal documentation — require **DirectX
  11**, with DirectX 12 explicitly unsupported.  FreeCAD's XR viewer is a
  Coin3D/OpenGL renderer on Windows *and* Linux, so even if the SDK were
  vendorable it could not be linked against here.
* **There is no LIV OpenXR extension.**  LIV holds an author tag in the Khronos
  OpenXR registry and has reserved ten extension numbers, ``XR_LIV_extension_187``
  through ``XR_LIV_extension_196``, but every one of them is marked
  ``supported="disabled"`` — reserved slots with no structs, no functions and no
  published specification.  Nothing there can be called.
* **What LIV can consume without any SDK is the legacy quadrant mode**, driven
  by ``externalcamera.cfg``.  That path is fully documented by its reference
  implementation, needs nothing proprietary, and is what :mod:`xrmrc` implements.

So this module does not pretend to bind an SDK it cannot see.  It provides an
honest capability probe — :func:`liv_available`, :func:`probe` — that says
exactly what is present and what is missing, and it drives the external-camera
path that LIV's legacy quadrant mode reads.  If LIV ever publishes one of those
reserved extensions, :func:`probe` will notice it (it looks for the
``XR_LIV_`` prefix in the runtime's extension list rather than for a name we
made up) and say so, at which point a real binding can be written against a
real specification.

Pure stdlib, plus an optional ``import xr`` inside the probe.
"""

import os
import platform

from . import externalcamera

__all__ = [
    "Check",
    "LivStatus",
    "LivIntegration",
    "liv_available",
    "probe",
    "MODE_UNAVAILABLE",
    "MODE_EXTERNAL_CAMERA",
    "MODE_NATIVE_SDK",
    "LIV_OPENXR_EXTENSION_PREFIX",
    "RESERVED_OPENXR_EXTENSIONS",
]

#: LIV cannot be driven at all from here.
MODE_UNAVAILABLE = "unavailable"
#: LIV's legacy quadrant mode, fed through ``externalcamera.cfg``.  The only
#: mode this workbench can actually implement.
MODE_EXTERNAL_CAMERA = "external_camera"
#: A real SDK binding.  Reachable only if LIV publishes a native or OpenXR
#: interface; kept as a named constant so callers can test for it.
MODE_NATIVE_SDK = "native_sdk"

#: Extensions in the Khronos registry are namespaced by author tag.  LIV's tag
#: is ``LIV``, so any future extension of theirs starts with this.
LIV_OPENXR_EXTENSION_PREFIX = "XR_LIV_"

#: The ten slots LIV has reserved in the OpenXR registry.  All are
#: ``supported="disabled"``: names only, with nothing behind them.
RESERVED_OPENXR_EXTENSIONS = tuple(
    f"XR_LIV_extension_{number}" for number in range(187, 197)
)


class Check:
    """One thing the probe looked for."""

    __slots__ = ("name", "ok", "detail", "required")

    def __init__(self, name, ok, detail, required=False):
        self.name = name
        self.ok = bool(ok)
        self.detail = detail
        self.required = bool(required)

    def as_dict(self):
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "required": self.required,
        }

    def __eq__(self, other):
        if not isinstance(other, Check):
            return NotImplemented
        return (self.name, self.ok, self.detail, self.required) == (
            other.name,
            other.ok,
            other.detail,
            other.required,
        )

    def __hash__(self):
        return hash((self.name, self.ok, self.detail, self.required))

    def __repr__(self):
        mark = "ok" if self.ok else "missing"
        return f"Check({self.name!r}, {mark}, {self.detail!r})"

    def __str__(self):
        return f"[{'x' if self.ok else ' '}] {self.name}: {self.detail}"


class LivStatus:
    """The result of :func:`probe`: what works, what does not, and why."""

    __slots__ = ("mode", "checks", "config_path")

    def __init__(self, mode, checks, config_path=None):
        self.mode = mode
        self.checks = list(checks)
        self.config_path = config_path

    @property
    def available(self):
        """True only when LIV can actually be driven from here."""
        return self.mode != MODE_UNAVAILABLE

    @property
    def missing(self):
        """The checks that failed, most important first."""
        failed = [check for check in self.checks if not check.ok]
        failed.sort(key=lambda check: 0 if check.required else 1)
        return failed

    @property
    def satisfied(self):
        return [check for check in self.checks if check.ok]

    def check(self, name):
        for item in self.checks:
            if item.name == name:
                return item
        return None

    def summary(self):
        """One line a user can act on."""
        if self.mode == MODE_NATIVE_SDK:
            return "LIV: native integration available"
        if self.mode == MODE_EXTERNAL_CAMERA:
            return (
                "LIV: external-camera (legacy quadrant) mode available"
                f" via {self.config_path}"
            )
        blockers = [check for check in self.missing if check.required]
        if blockers:
            return "LIV unavailable: " + "; ".join(check.detail for check in blockers)
        return "LIV unavailable"

    def report(self):
        """A multi-line explanation, for the status dialog and the log."""
        lines = [self.summary(), ""]
        for check in self.checks:
            lines.append(str(check))
        return "\n".join(lines)

    def as_dict(self):
        return {
            "mode": self.mode,
            "available": self.available,
            "config_path": self.config_path,
            "summary": self.summary(),
            "checks": [check.as_dict() for check in self.checks],
            "missing": [check.name for check in self.missing],
        }

    def __repr__(self):
        return f"LivStatus(mode={self.mode!r}, missing={[c.name for c in self.missing]})"


def _openxr_extensions():
    """Instance extensions the runtime advertises, or ``None`` if unknowable.

    ``None`` is a genuinely different answer from "an empty list": it means no
    OpenXR runtime could be queried at all, which is not evidence that LIV is
    absent.
    """
    try:
        import xr
    except Exception:
        return None
    try:
        extensions = xr.enumerate_instance_extension_properties()
    except Exception:
        return None
    names = []
    for extension in extensions or ():
        if isinstance(extension, (bytes, bytearray)):
            names.append(extension.decode("utf-8", "replace"))
        elif isinstance(extension, str):
            names.append(extension)
        else:
            name = getattr(extension, "extension_name", None)
            if isinstance(name, (bytes, bytearray)):
                name = name.decode("utf-8", "replace")
            if name:
                names.append(str(name))
    return names


def probe(config_paths=(), extensions=None, system=None):
    """Work out what LIV integration is possible right now.

    ``extensions`` and ``system`` are injectable so the probe can be tested
    without an OpenXR runtime or a particular OS; leave them out in real use.
    """
    system = system or platform.system()
    checks = []

    # 1. A native SDK.  There is none to find, and saying so plainly is more
    #    useful than a check that could never pass.
    checks.append(
        Check(
            "native_sdk",
            False,
            "no public native/C LIV SDK exists; the Unity and Unreal SDKs are "
            "distributed through a developer portal behind a login, are Windows "
            "only, and require DirectX 11 (DirectX 12 is unsupported), so they "
            "cannot be linked against an OpenGL viewer",
            required=False,
        )
    )

    # 2. An OpenXR extension.  Look for the author-tag prefix rather than a
    #    made-up name, so this starts passing by itself if LIV ever ships one.
    if extensions is None:
        extensions = _openxr_extensions()
    if extensions is None:
        checks.append(
            Check(
                "openxr_extension",
                False,
                "no OpenXR runtime could be queried (pyopenxr missing or no "
                "runtime installed), so LIV extensions could not be looked for",
            )
        )
    else:
        found = sorted(
            name for name in extensions
            if name.startswith(LIV_OPENXR_EXTENSION_PREFIX)
        )
        if found:
            checks.append(
                Check(
                    "openxr_extension",
                    True,
                    "runtime advertises " + ", ".join(found),
                )
            )
        else:
            checks.append(
                Check(
                    "openxr_extension",
                    False,
                    "the runtime advertises no XR_LIV_* extension; LIV has "
                    "reserved registry slots 187-196 but all are marked "
                    "disabled, with no published specification",
                )
            )

    # 3. Windows.  LIV's compositor is a Windows application; the legacy
    #    quadrant path still produces a correct frame anywhere, so this is a
    #    warning rather than a blocker for our own output.
    is_windows = system.lower().startswith("win")
    checks.append(
        Check(
            "platform",
            is_windows,
            "LIV's compositor runs on Windows only"
            + ("" if is_windows else f"; this is {system}"),
        )
    )

    # 4. The calibration file, which is the whole of the legacy path.
    found_config = externalcamera.find_config(config_paths)
    if found_config:
        checks.append(
            Check("externalcamera_cfg", True, f"found at {found_config}", required=True)
        )
    else:
        candidates = externalcamera.default_paths(config_paths)
        checks.append(
            Check(
                "externalcamera_cfg",
                False,
                "no externalcamera.cfg found; looked in "
                + ", ".join(candidates),
                required=True,
            )
        )

    if any(check.name == "openxr_extension" and check.ok for check in checks):
        mode = MODE_NATIVE_SDK
    elif found_config:
        mode = MODE_EXTERNAL_CAMERA
    else:
        mode = MODE_UNAVAILABLE
    return LivStatus(mode, checks, found_config)


def liv_available(config_paths=()):
    """True when LIV can be driven from here at all.

    This is deliberately strict: it is False when the only thing missing is the
    calibration file, because without one there is nothing for LIV to consume.
    :meth:`LivIntegration.prepare` will write a starting one for you.
    """
    return probe(config_paths).available


class LivIntegration:
    """Drives the one LIV path this workbench can honestly implement.

    The legacy quadrant mode is a contract between three parties: the game
    writes a four-quadrant frame, ``externalcamera.cfg`` states the calibration,
    and LIV composites.  Our side of it is the compositor in
    :mod:`xrmrc.compositor`; this class owns the file.
    """

    def __init__(self, config_paths=(), config_path=None):
        self.config_paths = tuple(config_paths)
        self.config_path = config_path
        self.config = None
        self.status = None

    # -- capability -----------------------------------------------------

    def refresh(self, extensions=None, system=None):
        self.status = probe(self.config_paths, extensions=extensions, system=system)
        if self.config_path is None:
            self.config_path = self.status.config_path
        return self.status

    @property
    def available(self):
        if self.status is None:
            self.refresh()
        return self.status.available

    @property
    def mode(self):
        if self.status is None:
            self.refresh()
        return self.status.mode

    # -- the calibration file -------------------------------------------

    def target_path(self):
        """Where the calibration should live if we have to create one."""
        if self.config_path:
            return self.config_path
        candidates = externalcamera.default_paths(self.config_paths)
        return candidates[0] if candidates else externalcamera.CONFIG_FILENAME

    def prepare(self, create_missing=True, config=None):
        """Make sure there is a usable calibration, and return it.

        Returns ``(config, path)``.  When nothing is on disk and
        ``create_missing`` is set, a default calibration is written so that LIV
        (and the user's calibration tool) has something to edit.
        """
        path = externalcamera.find_config(self.config_paths) or self.config_path
        if path and os.path.isfile(path):
            self.config = externalcamera.load(path)
            self.config_path = path
            return self.config, path
        if config is None:
            config = externalcamera.default_config()
        if not create_missing:
            self.config = config
            return config, None
        path = self.target_path()
        externalcamera.save(config, path)
        self.config = config
        self.config_path = path
        return config, path

    def describe(self):
        if self.status is None:
            self.refresh()
        data = self.status.as_dict()
        data["config_path"] = self.config_path
        return data
