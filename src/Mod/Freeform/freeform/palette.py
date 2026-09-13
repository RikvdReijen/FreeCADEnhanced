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
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""Colour palette and layer helpers of the Freeform workbench.

Immersive sketching tools keep a small, always visible palette: pick a
colour once and everything you draw afterwards uses it. The functions
here store that *current colour* in the user preferences, colour existing
objects, and provide a small Qt widget with swatches that is embedded in
the stroke task panel and in the palette dialog.

The colour functions work headlessly (they only touch view providers when
the GUI is up); the widget needs PySide.
"""

import FreeCAD

PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/Freeform"

translate = FreeCAD.Qt.translate

# A compact palette: neutrals, saturated hues and their pastel versions.
DEFAULT_PALETTE = [
    ("Ink", (0.13, 0.13, 0.15)),
    ("Graphite", (0.35, 0.35, 0.38)),
    ("Silver", (0.65, 0.66, 0.68)),
    ("Paper", (0.93, 0.93, 0.90)),
    ("Coral", (0.95, 0.35, 0.30)),
    ("Tangerine", (0.98, 0.58, 0.16)),
    ("Sun", (0.99, 0.82, 0.20)),
    ("Lime", (0.60, 0.80, 0.20)),
    ("Jade", (0.15, 0.68, 0.48)),
    ("Sky", (0.24, 0.62, 0.92)),
    ("Cobalt", (0.20, 0.32, 0.80)),
    ("Violet", (0.55, 0.35, 0.80)),
    ("Magenta", (0.85, 0.25, 0.60)),
    ("Blush", (0.98, 0.72, 0.72)),
    ("Peach", (0.99, 0.82, 0.62)),
    ("Butter", (0.99, 0.94, 0.62)),
    ("Mint", (0.72, 0.92, 0.72)),
    ("Aqua", (0.62, 0.90, 0.90)),
    ("Lavender", (0.78, 0.74, 0.95)),
    ("Rose", (0.95, 0.70, 0.85)),
]


def _params():
    return FreeCAD.ParamGet(PARAM_PATH)


def pack_color(rgb):
    """Pack an ``(r, g, b)`` float tuple into FreeCAD's unsigned RGBA format."""
    r, g, b = (max(0, min(255, int(round(c * 255)))) for c in rgb[:3])
    return (r << 24) | (g << 16) | (b << 8) | 0xFF


def unpack_color(packed):
    return (
        ((packed >> 24) & 0xFF) / 255.0,
        ((packed >> 16) & 0xFF) / 255.0,
        ((packed >> 8) & 0xFF) / 255.0,
    )


def get_current_color():
    """The colour new Freeform objects receive; ``None`` when unset."""
    packed = _params().GetUnsigned("CurrentColor", 0)
    if packed == 0:
        return None
    return unpack_color(packed)


def set_current_color(rgb):
    """Remember ``rgb`` (floats 0..1) as the colour for new objects."""
    if rgb is None:
        _params().SetUnsigned("CurrentColor", 0)
    else:
        _params().SetUnsigned("CurrentColor", pack_color(rgb))


def apply_color(objects, rgb):
    """Colour lines, points and faces of ``objects`` with ``rgb``."""
    if not FreeCAD.GuiUp:
        return
    color = tuple(float(c) for c in rgb[:3])
    for obj in objects:
        vobj = getattr(obj, "ViewObject", None)
        if vobj is None:
            continue
        for prop in ("LineColor", "PointColor", "ShapeColor"):
            if hasattr(vobj, prop):
                try:
                    setattr(vobj, prop, color)
                except Exception:  # pylint: disable=broad-except
                    pass
        if hasattr(vobj, "ShapeAppearance"):
            try:
                material = vobj.ShapeAppearance[0]
                material.DiffuseColor = color
                vobj.ShapeAppearance = (material,)
            except Exception:  # pylint: disable=broad-except
                pass


def color_name(rgb):
    """Name of the closest palette entry."""
    best, best_dist = "Custom", 1e9
    for name, swatch in DEFAULT_PALETTE:
        dist = sum((a - b) ** 2 for a, b in zip(rgb, swatch))
        if dist < best_dist:
            best, best_dist = name, dist
    return best


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------


def make_layer(name, rgb=None, doc=None):
    """Create a layer named ``name`` coloured ``rgb``.

    Uses the Draft layer when the Draft workbench is available (its view
    provider drives the colours of the members); otherwise a plain group.
    """
    doc = doc or FreeCAD.ActiveDocument
    layer = None
    try:
        import Draft

        kwargs = {}
        if rgb is not None:
            kwargs["line_color"] = tuple(rgb)
            kwargs["shape_color"] = tuple(rgb)
        layer = Draft.make_layer(name, **kwargs)
    except Exception:  # pylint: disable=broad-except
        layer = None
    if layer is None:
        layer = doc.addObject("App::DocumentObjectGroup", name)
        layer.Label = name
    return layer


def add_to_layer(layer, objects):
    """Move ``objects`` into ``layer`` (Draft layer or group)."""
    if hasattr(layer, "Proxy") and hasattr(layer.Proxy, "addObject"):
        for obj in objects:
            layer.Proxy.addObject(layer, obj)
    else:
        group = list(layer.Group)
        for obj in objects:
            if obj not in group:
                group.append(obj)
        layer.Group = group


# ---------------------------------------------------------------------------
# Qt widget (GUI only)
# ---------------------------------------------------------------------------

if FreeCAD.GuiUp:
    import FreeCADGui
    from PySide import QtCore, QtGui, QtWidgets

    class PaletteWidget(QtWidgets.QWidget):
        """A grid of colour swatches plus a custom colour button.

        Emits ``colorChanged(tuple)`` when a swatch is picked. When
        ``apply_to_selection`` is True the colour is also applied to the
        selected objects.
        """

        colorChanged = QtCore.Signal(object)

        def __init__(self, parent=None, columns=10, apply_to_selection=True, swatch_size=22):
            super().__init__(parent)
            self.apply_to_selection = apply_to_selection
            self.swatch_size = swatch_size
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            grid = QtWidgets.QGridLayout()
            grid.setSpacing(2)
            self.buttons = []
            for index, (name, rgb) in enumerate(DEFAULT_PALETTE):
                button = self._make_swatch(name, rgb)
                grid.addWidget(button, index // columns, index % columns)
                self.buttons.append(button)
            layout.addLayout(grid)
            row = QtWidgets.QHBoxLayout()
            self.current = QtWidgets.QLabel()
            self.current.setFixedSize(swatch_size * 2, swatch_size)
            self.current.setToolTip(translate("Freeform", "Current colour"))
            row.addWidget(self.current)
            self.custom_button = QtWidgets.QPushButton(translate("Freeform", "Custom…"))
            self.custom_button.clicked.connect(self.pick_custom)
            row.addWidget(self.custom_button)
            self.clear_button = QtWidgets.QPushButton(translate("Freeform", "Default"))
            self.clear_button.setToolTip(
                translate("Freeform", "Use the FreeCAD default colours for new objects")
            )
            self.clear_button.clicked.connect(self.clear_color)
            row.addWidget(self.clear_button)
            row.addStretch()
            layout.addLayout(row)
            self.refresh()

        def _make_swatch(self, name, rgb):
            button = QtWidgets.QToolButton()
            button.setFixedSize(self.swatch_size, self.swatch_size)
            button.setToolTip(name)
            button.setAutoRaise(True)
            button.setStyleSheet(
                "QToolButton { background-color: rgb(%d,%d,%d); border: 1px solid #555; border-radius: 3px; }"
                % tuple(int(c * 255) for c in rgb)
            )
            button.clicked.connect(lambda checked=False, c=rgb: self.choose(c))
            return button

        def refresh(self):
            rgb = get_current_color()
            if rgb is None:
                self.current.setStyleSheet(
                    "QLabel { border: 1px dashed #888; border-radius: 3px; }"
                )
                self.current.setText(translate("Freeform", "auto"))
                self.current.setAlignment(QtCore.Qt.AlignCenter)
            else:
                self.current.setText("")
                self.current.setStyleSheet(
                    "QLabel { background-color: rgb(%d,%d,%d); border: 1px solid #555; border-radius: 3px; }"
                    % tuple(int(c * 255) for c in rgb)
                )

        def choose(self, rgb):
            set_current_color(rgb)
            self.refresh()
            if self.apply_to_selection:
                selection = FreeCADGui.Selection.getSelection()
                if selection:
                    doc = FreeCAD.ActiveDocument
                    if doc is not None:
                        doc.openTransaction(translate("Freeform", "Colour objects"))
                    apply_color(selection, rgb)
                    if doc is not None:
                        doc.commitTransaction()
            self.colorChanged.emit(rgb)

        def pick_custom(self):
            start = get_current_color() or (0.8, 0.8, 0.8)
            initial = QtGui.QColor.fromRgbF(*start)
            color = QtWidgets.QColorDialog.getColor(
                initial, self, translate("Freeform", "Choose colour")
            )
            if color.isValid():
                self.choose((color.redF(), color.greenF(), color.blueF()))

        def clear_color(self):
            set_current_color(None)
            self.refresh()
            self.colorChanged.emit(None)

    class PaletteTaskPanel:
        """Task panel wrapper around :class:`PaletteWidget`."""

        def __init__(self):
            self.form = QtWidgets.QWidget()
            self.form.setWindowTitle(translate("Freeform", "Colour palette"))
            layout = QtWidgets.QVBoxLayout(self.form)
            hint = QtWidgets.QLabel(
                translate(
                    "Freeform",
                    "Pick a colour for new strokes. With objects selected, the colour is applied to them too.",
                )
            )
            hint.setWordWrap(True)
            layout.addWidget(hint)
            self.palette = PaletteWidget(self.form)
            layout.addWidget(self.palette)
            layout.addStretch()

        def getStandardButtons(self):
            return QtWidgets.QDialogButtonBox.Close

        def reject(self):
            FreeCADGui.Control.closeDialog()
            return True

        def isAllowedAlterSelection(self):
            return True

        def isAllowedAlterView(self):
            return True

        def isAllowedAlterDocument(self):
            return True
