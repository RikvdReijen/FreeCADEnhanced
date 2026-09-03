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
"""Preferences page for the environment, painting and sync features.

The upstream page in :mod:`xrcore.preferences` keeps its ``.ui`` file and
covers the OpenXR viewer itself; this second page is built in code and covers
everything added on top of it.
"""

from PySide.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from xrcore.service import preferences

__all__ = ["XRSyncPreferencesPage"]

_BLEND_MODES = ["normal", "multiply", "add", "screen", "erase"]
_TEXTURE_SIZES = ["512", "1024", "2048", "4096"]


class XRSyncPreferencesPage:
    """FreeCAD preferences page: ``Virtual Reality → Environment, painting, sync``."""

    def __init__(self, parent=None):
        self.form = QWidget(parent)
        self.form.setWindowTitle("Environment, painting, sync")
        layout = QVBoxLayout(self.form)

        # -- environment --------------------------------------------------
        env_box = QGroupBox("Environment")
        env_form = QFormLayout(env_box)
        self.environment = QComboBox()
        self._fill_environments()
        env_form.addRow("Default environment", self.environment)

        self.follow_env_scale = QCheckBox(
            "Adopt the environment's suggested scale when switching"
        )
        env_form.addRow(self.follow_env_scale)

        self.user_scale = QDoubleSpinBox()
        self.user_scale.setRange(0.125, 400.0)
        self.user_scale.setDecimals(2)
        self.user_scale.setSingleStep(0.5)
        env_form.addRow("Your scale (1:n)", self.user_scale)

        self.scale_transition = QDoubleSpinBox()
        self.scale_transition.setRange(0.0, 5.0)
        self.scale_transition.setDecimals(2)
        self.scale_transition.setSuffix(" s")
        env_form.addRow("Scale transition time", self.scale_transition)

        self.place_on_anchor = QCheckBox(
            "Drop the document onto the machine's build plate / bed"
        )
        env_form.addRow(self.place_on_anchor)
        layout.addWidget(env_box)

        # -- painting -----------------------------------------------------
        paint_box = QGroupBox("Painting")
        paint_form = QFormLayout(paint_box)
        self.texture_size = QComboBox()
        self.texture_size.addItems(_TEXTURE_SIZES)
        paint_form.addRow("Default texture size", self.texture_size)

        self.brush_radius = QDoubleSpinBox()
        self.brush_radius.setRange(0.1, 200.0)
        self.brush_radius.setSuffix(" mm")
        paint_form.addRow("Default brush radius", self.brush_radius)

        self.blend_mode = QComboBox()
        self.blend_mode.addItems(_BLEND_MODES)
        paint_form.addRow("Default blend mode", self.blend_mode)

        self.pressure = QCheckBox("Trigger pressure controls brush size and flow")
        paint_form.addRow(self.pressure)

        self.auto_uv = QCheckBox("Generate UVs automatically for objects that have none")
        paint_form.addRow(self.auto_uv)

        self.undo_steps = QSpinBox()
        self.undo_steps.setRange(4, 256)
        paint_form.addRow("Paint undo steps", self.undo_steps)

        self.vector_autocommit = QCheckBox(
            "Commit vector drawings received from the headset immediately"
        )
        paint_form.addRow(self.vector_autocommit)
        layout.addWidget(paint_box)

        # -- sync ---------------------------------------------------------
        sync_box = QGroupBox("Headset sync")
        sync_form = QFormLayout(sync_box)
        self.sync_autostart = QCheckBox("Start the companion server with the workbench")
        sync_form.addRow(self.sync_autostart)

        self.sync_port = QSpinBox()
        self.sync_port.setRange(0, 65535)
        self.sync_port.setSpecialValueText("automatic")
        sync_form.addRow("Server port", self.sync_port)

        self.discovery = QCheckBox("Answer discovery broadcasts on the local network")
        sync_form.addRow(self.discovery)

        self.require_pairing = QCheckBox("Require a pairing code (recommended)")
        sync_form.addRow(self.require_pairing)

        self.export_lod = QComboBox()
        self.export_lod.addItems(["draft", "normal", "fine", "very fine"])
        sync_form.addRow("Scene detail sent to the headset", self.export_lod)

        self.drive_folder = QLineEdit()
        sync_form.addRow("Google Drive folder", self.drive_folder)

        note = QLabel(
            "The headset connects to this computer over your local network, and can also "
            "load documents straight from Google Drive when no computer is running."
        )
        note.setWordWrap(True)
        sync_form.addRow(note)
        layout.addWidget(sync_box)
        layout.addStretch(1)

    # ------------------------------------------------------------------

    def _fill_environments(self):
        self.environment.addItem("Studio (neutral)", "studio")
        try:
            from xrenv import registry

            self.environment.clear()
            for info in registry.list_environments():
                self.environment.addItem(info.name, info.id)
        except Exception:
            # xrenv is optional at preference-page time; keep the fallback entry.
            pass

    def saveSettings(self):
        pref = preferences()
        pref.SetString("Environment", self.environment.currentData() or "studio")
        pref.SetBool("FollowEnvironmentScale", self.follow_env_scale.isChecked())
        pref.SetFloat("UserScale", self.user_scale.value())
        pref.SetFloat("ScaleTransition", self.scale_transition.value())
        pref.SetBool("PlaceOnAnchor", self.place_on_anchor.isChecked())

        pref.SetInt("TextureSize", int(self.texture_size.currentText()))
        pref.SetFloat("BrushRadius", self.brush_radius.value())
        pref.SetString("BlendMode", self.blend_mode.currentText())
        pref.SetBool("PressureEnabled", self.pressure.isChecked())
        pref.SetBool("AutoUV", self.auto_uv.isChecked())
        pref.SetInt("PaintUndoSteps", self.undo_steps.value())
        pref.SetBool("VectorAutoCommit", self.vector_autocommit.isChecked())

        pref.SetBool("SyncAutostart", self.sync_autostart.isChecked())
        pref.SetInt("SyncPort", self.sync_port.value())
        pref.SetBool("SyncDiscovery", self.discovery.isChecked())
        pref.SetBool("SyncRequirePairing", self.require_pairing.isChecked())
        pref.SetInt("ExportLod", self.export_lod.currentIndex())
        pref.SetString("DriveFolder", self.drive_folder.text())

    def loadSettings(self):
        pref = preferences()
        env_id = pref.GetString("Environment", "studio")
        index = self.environment.findData(env_id)
        self.environment.setCurrentIndex(index if index >= 0 else 0)
        self.follow_env_scale.setChecked(pref.GetBool("FollowEnvironmentScale", True))
        self.user_scale.setValue(pref.GetFloat("UserScale", 1.0))
        self.scale_transition.setValue(pref.GetFloat("ScaleTransition", 0.6))
        self.place_on_anchor.setChecked(pref.GetBool("PlaceOnAnchor", True))

        size = str(pref.GetInt("TextureSize", 2048))
        size_index = self.texture_size.findText(size)
        self.texture_size.setCurrentIndex(size_index if size_index >= 0 else 2)
        self.brush_radius.setValue(pref.GetFloat("BrushRadius", 4.0))
        blend = pref.GetString("BlendMode", "normal")
        blend_index = self.blend_mode.findText(blend)
        self.blend_mode.setCurrentIndex(blend_index if blend_index >= 0 else 0)
        self.pressure.setChecked(pref.GetBool("PressureEnabled", True))
        self.auto_uv.setChecked(pref.GetBool("AutoUV", True))
        self.undo_steps.setValue(pref.GetInt("PaintUndoSteps", 32))
        self.vector_autocommit.setChecked(pref.GetBool("VectorAutoCommit", True))

        self.sync_autostart.setChecked(pref.GetBool("SyncAutostart", False))
        self.sync_port.setValue(pref.GetInt("SyncPort", 0))
        self.discovery.setChecked(pref.GetBool("SyncDiscovery", True))
        self.require_pairing.setChecked(pref.GetBool("SyncRequirePairing", True))
        self.export_lod.setCurrentIndex(pref.GetInt("ExportLod", 1))
        self.drive_folder.setText(pref.GetString("DriveFolder", "FreeCAD XR"))
