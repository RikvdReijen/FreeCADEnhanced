# Third party notices — XR workbench

## freecad-xr-workbench

The OpenXR session handling, the Coin3D scenegraph plumbing, the motion
controller abstraction, the in-VR Coin menus, the Qt-widget-in-VR renderer and
the document interaction modes in `xrcore/` are derived from

* **freecad-xr-workbench** — <https://github.com/kwahoo2/freecad-xr-workbench>
* Copyright (c) 2023–2026 Adrian Przekwas <adrian.v.przekwas@gmail.com>
* Licensed **LGPL-3.0-or-later**; the full text is in `LICENSE-upstream.txt`.

The following files are ported from that project, with their original copyright
headers left intact:

| File | Upstream file |
|------|---------------|
| `xrcore/commonXR.py` | `freecad/XR/commonXR.py` |
| `xrcore/controllerXR.py` | `freecad/XR/controllerXR.py` |
| `xrcore/movementXR.py` | `freecad/XR/movementXR.py` |
| `xrcore/menuCoin.py` | `freecad/XR/menuCoin.py` |
| `xrcore/previewCoin.py` | `freecad/XR/previewCoin.py` |
| `xrcore/qtWidgetRender.py` | `freecad/XR/qtWidgetRender.py` |
| `xrcore/documentInteraction.py` | `freecad/XR/documentInteraction.py` |
| `xrcore/preferences.py` | `freecad/XR/preferences.py` |
| `Resources/controllers/*.iv` | `Resources/controllers/*.iv` |
| `Resources/icons/Stepien_Glasses.svg` and the other viewer icons | `Resources/Gui/Resources/icons/` |
| `Resources/XRPreferences.ui` | `Resources/Gui/Resources/preferences/XRPreferences.ui` |
| `Resources/doc/*.md` | `Resources/doc/` |

Changes made during the port:

* the Python package was renamed from `freecad.XR` to `xrcore`, because
  `pyopenxr` already owns the top-level module name `xr`;
* resource lookups follow FreeCAD's in-tree `Mod/XR/Resources` layout instead of
  a compiled Qt resource file;
* the preference group moved to `BaseApp/Preferences/Mod/XR`;
* `commonXR.XRwidget` gained `attach_extensions`/`detach_extensions`,
  `update_extensions`, `set_clip_planes` and `document_bounding_box`, and its
  scenegraph gained a document separator and a paint separator, so the
  environment switcher and the painting module can plug in;
* the workbench class, the command set and the second preferences page were
  rewritten for FreeCAD's in-tree conventions.

Everything under `xrenv/`, `xrpaint/`, `xrsync/`, `quest/`, and the files
`Init.py`, `InitGui.py`, `XRFileIO.py`, `xrcore/commands.py`,
`xrcore/service.py`, `xrcore/environment_bridge.py`, `xrcore/paint_bridge.py`,
`xrcore/ui_dialogs.py` and `xrcore/preferences_xr.py` is new work, licensed
`LGPL-2.1-or-later` like the rest of FreeCAD.

### Licence compatibility

FreeCAD is LGPL-2.1-**or-later**, which permits distribution of the combined
work under LGPL-3.0. The ported files therefore keep their LGPL-3.0-or-later
headers, and the module as a whole is distributed under LGPL-3.0-or-later.

## Trademarks

"Bambu Lab" and "X1 Carbon" are trademarks of their respective owners. The
`bambu_x1c` environment is an independent, procedurally generated
*representation* of a consumer CoreXY 3D printer chamber created for this
project; it contains no manufacturer-supplied CAD data, artwork, firmware or
other assets, and the project is not affiliated with or endorsed by any printer
or laser manufacturer. The same applies to the laser cutter environment.
