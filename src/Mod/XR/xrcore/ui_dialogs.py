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
"""Desktop dialogs for the XR workbench.

These are deliberately built in code rather than in ``.ui`` files: they are
mostly lists driven by data that only exists at runtime (installed
environments, paired devices, Drive folders), and building them by hand keeps
the resource story simple.
"""

import os

import FreeCAD
import FreeCADGui as Gui
from PySide.QtCore import Qt, QTimer
from PySide.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from xrcore import service

__all__ = [
    "show_environment_dialog",
    "show_layers_dialog",
    "show_pairing_dialog",
    "show_server_info",
    "show_drive_browser",
    "show_drive_account_dialog",
]


def _main_window():
    return Gui.getMainWindow()


def _warn(title, text):
    QMessageBox.warning(_main_window(), title, text)


# --------------------------------------------------------------------------
# environment switcher
# --------------------------------------------------------------------------


class EnvironmentDialog(QDialog):
    """Pick the world that surrounds you in VR, and how small you are in it."""

    def __init__(self, parent=None):
        super().__init__(parent or _main_window())
        self.setWindowTitle("XR environment")
        self.resize(680, 460)

        from xrcore import environment_bridge

        self.bridge = environment_bridge
        self.infos = environment_bridge.available_environments()

        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        layout.addLayout(body, 1)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        body.addWidget(self.list, 1)

        side = QVBoxLayout()
        body.addLayout(side, 2)
        self.description = QTextBrowser()
        self.description.setOpenExternalLinks(True)
        side.addWidget(self.description, 1)

        form = QFormLayout()
        side.addLayout(form)
        self.scale_label = QLabel()
        self.scale_slider = QSlider(Qt.Horizontal)
        # Logarithmic: slider 0..100 maps to 1/8 .. 400
        self.scale_slider.setRange(0, 100)
        self.scale_slider.valueChanged.connect(self._scale_changed)
        form.addRow("Your scale", self.scale_slider)
        form.addRow("", self.scale_label)

        self.follow_default = QCheckBox("Use the environment's suggested scale")
        self.follow_default.setChecked(True)
        self.follow_default.toggled.connect(self._follow_toggled)
        form.addRow(self.follow_default)

        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        current = service.get_environment_id()
        for info in self.infos:
            item = QListWidgetItem(info.name)
            item.setData(Qt.UserRole, info.id)
            self.list.addItem(item)
            if info.id == current:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.infos:
            self.list.setCurrentRow(0)
        self.list.currentRowChanged.connect(self._selection_changed)
        self._selection_changed(self.list.currentRow())

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _slider_to_scale(value):
        import math

        lo, hi = math.log(1.0 / 8.0), math.log(400.0)
        return math.exp(lo + (hi - lo) * value / 100.0)

    @staticmethod
    def _scale_to_slider(scale):
        import math

        lo, hi = math.log(1.0 / 8.0), math.log(400.0)
        scale = max(1.0 / 8.0, min(400.0, scale))
        return int(round(100.0 * (math.log(scale) - lo) / (hi - lo)))

    def _current_info(self):
        row = self.list.currentRow()
        if row < 0 or row >= len(self.infos):
            return None
        return self.infos[row]

    def _selection_changed(self, _row):
        info = self._current_info()
        if info is None:
            return
        scale = info.user_scale
        html = [
            f"<h3>{info.name}</h3>",
            f"<p>{info.description}</p>",
            "<table cellpadding='3'>",
            f"<tr><td><b>Identifier</b></td><td><code>{info.id}</code></td></tr>",
            f"<tr><td><b>Suggested scale</b></td><td>1:{scale:g}</td></tr>",
        ]
        if getattr(info, "bounds", None):
            w, d, h = info.bounds
            html.append(
                f"<tr><td><b>Interior</b></td><td>{w * 1000:.0f} × {d * 1000:.0f} × {h * 1000:.0f} mm</td></tr>"
            )
        if getattr(info, "part_count", None):
            html.append(f"<tr><td><b>Parts</b></td><td>{info.part_count}</td></tr>")
        html.append("</table>")
        if scale > 1.5:
            html.append(
                f"<p><i>At 1:{scale:g} you are about {1700.0 / scale:.0f} mm tall inside this "
                "machine — small enough to walk around the parts you are drawing.</i></p>"
            )
        self.description.setHtml("".join(html))
        if self.follow_default.isChecked():
            self.scale_slider.setValue(self._scale_to_slider(scale))

    def _scale_changed(self, value):
        scale = self._slider_to_scale(value)
        height_mm = 1700.0 / scale
        if height_mm >= 1000.0:
            size = f"{height_mm / 1000.0:.2f} m tall"
        else:
            size = f"{height_mm:.0f} mm tall"
        self.scale_label.setText(f"1:{scale:.2f} — you are {size}")

    def _follow_toggled(self, checked):
        self.scale_slider.setEnabled(not checked)
        if checked:
            self._selection_changed(self.list.currentRow())

    def _apply(self):
        info = self._current_info()
        if info is None:
            return
        try:
            self.bridge.set_environment(info.id)
            if not self.follow_default.isChecked():
                self.bridge.manager().set_scale(self._slider_to_scale(self.scale_slider.value()))
        except Exception as exc:
            _warn("XR environment", str(exc))


def show_environment_dialog():
    EnvironmentDialog().exec_()


# --------------------------------------------------------------------------
# paint layers
# --------------------------------------------------------------------------


class LayersDialog(QDialog):
    """Layer stack of the current painting target."""

    BLEND_MODES = ["normal", "multiply", "add", "screen", "erase"]

    def __init__(self, parent=None):
        super().__init__(parent or _main_window())
        self.setWindowTitle("XR paint layers")
        self.resize(460, 420)

        from xrcore import paint_bridge

        self.session = paint_bridge.get_session()

        layout = QVBoxLayout(self)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)

        form = QFormLayout()
        layout.addLayout(form)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.valueChanged.connect(self._opacity_changed)
        form.addRow("Opacity", self.opacity)
        self.blend = QComboBox()
        self.blend.addItems(self.BLEND_MODES)
        self.blend.currentTextChanged.connect(self._blend_changed)
        form.addRow("Blend", self.blend)

        row = QHBoxLayout()
        layout.addLayout(row)
        for label, slot in (
            ("Add", self._add),
            ("Remove", self._remove),
            ("Up", lambda: self._move(-1)),
            ("Down", lambda: self._move(1)),
            ("Merge down", self._merge),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.list.currentRowChanged.connect(self._selection_changed)
        self._reload()

    def _stack(self):
        if self.session is None:
            return None
        return self.session.active_layer_stack()

    def _reload(self):
        self.list.clear()
        stack = self._stack()
        if stack is None:
            self.list.addItem("(nothing painted yet)")
            self.list.setEnabled(False)
            return
        for layer in stack.layers:
            item = QListWidgetItem(f"{'●' if layer.visible else '○'}  {layer.name}")
            item.setData(Qt.UserRole, layer.name)
            self.list.addItem(item)
        self.list.setCurrentRow(stack.active_index)

    def _current_layer(self):
        stack = self._stack()
        if stack is None:
            return None
        row = self.list.currentRow()
        if row < 0 or row >= len(stack.layers):
            return None
        return stack.layers[row]

    def _selection_changed(self, row):
        stack = self._stack()
        if stack is None or row < 0 or row >= len(stack.layers):
            return
        stack.active_index = row
        layer = stack.layers[row]
        self.opacity.blockSignals(True)
        self.opacity.setValue(int(round(layer.opacity * 100)))
        self.opacity.blockSignals(False)
        self.blend.blockSignals(True)
        self.blend.setCurrentText(layer.blend)
        self.blend.blockSignals(False)

    def _opacity_changed(self, value):
        layer = self._current_layer()
        if layer is not None:
            layer.opacity = value / 100.0
            self.session.invalidate_composite()

    def _blend_changed(self, text):
        layer = self._current_layer()
        if layer is not None:
            layer.blend = text
            self.session.invalidate_composite()

    def _add(self):
        stack = self._stack()
        if stack is not None:
            stack.add_layer(f"Layer {len(stack.layers) + 1}")
            self._reload()

    def _remove(self):
        stack = self._stack()
        if stack is not None and self.list.currentRow() >= 0:
            stack.remove_layer(self.list.currentRow())
            self._reload()

    def _move(self, delta):
        stack = self._stack()
        row = self.list.currentRow()
        if stack is not None and row >= 0:
            stack.move_layer(row, row + delta)
            self._reload()

    def _merge(self):
        stack = self._stack()
        row = self.list.currentRow()
        if stack is not None and row > 0:
            stack.merge_down(row)
            self.session.invalidate_composite()
            self._reload()


def show_layers_dialog():
    LayersDialog().exec_()


# --------------------------------------------------------------------------
# sync server / pairing
# --------------------------------------------------------------------------


def show_server_info(instance):
    addresses = "<br>".join(f"<code>{url}</code>" for url in instance.urls())
    QMessageBox.information(
        _main_window(),
        "XR sync server",
        "<p>The headset can now find this computer on your local network.</p>"
        f"<p>{addresses}</p>"
        "<p>In the Quest application choose <b>Connect → Scan network</b>, then pair "
        "with the code from <b>Pair headset…</b>.</p>",
    )


class PairingDialog(QDialog):
    """Shows a short-lived pairing code and reports when a device uses it."""

    def __init__(self, parent=None):
        super().__init__(parent or _main_window())
        self.setWindowTitle("Pair a headset")
        self.resize(420, 260)

        self.server = service.sync_server() or service.start_sync_server()
        self.code, self.expires_in = self.server.begin_pairing()

        layout = QVBoxLayout(self)
        heading = QLabel("Type this code in the Quest application:")
        layout.addWidget(heading)

        self.code_label = QLabel(" ".join(self.code))
        font = self.code_label.font()
        font.setPointSize(font.pointSize() * 3)
        font.setBold(True)
        self.code_label.setFont(font)
        self.code_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.code_label)

        self.status = QLabel()
        self.status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status)

        self.addresses = QLabel("\n".join(self.server.urls()))
        self.addresses.setAlignment(Qt.AlignCenter)
        self.addresses.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.addresses)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._remaining = int(self.expires_in)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self._tick()

    def _tick(self):
        if self.server.pairing_completed():
            self.timer.stop()
            self.code_label.setText("Paired")
            self.status.setText("The headset is connected.")
            return
        self._remaining -= 1
        if self._remaining <= 0:
            self.timer.stop()
            self.code_label.setText("Expired")
            self.status.setText("The code timed out — close and try again.")
            return
        self.status.setText(f"Valid for {self._remaining} s")

    def reject(self):
        self.timer.stop()
        self.server.cancel_pairing()
        super().reject()


def show_pairing_dialog():
    PairingDialog().exec_()


# --------------------------------------------------------------------------
# Google Drive
# --------------------------------------------------------------------------


def _drive_client(interactive=True):
    from xrsync import gdrive

    try:
        client = gdrive.GoogleDriveClient.from_stored_credentials()
    except gdrive.NotConfiguredError as exc:
        if interactive:
            _warn(
                "Google Drive",
                f"{exc}\n\nUse “Google Drive account…” to set up an OAuth client "
                "and sign in.",
            )
        return None
    except gdrive.NotAuthenticatedError:
        if interactive and _sign_in_flow():
            return _drive_client(interactive=False)
        return None
    return client


def _sign_in_flow():
    from xrsync import gdrive

    dialog = QDialog(_main_window())
    dialog.setWindowTitle("Sign in to Google Drive")
    layout = QVBoxLayout(dialog)
    info = QLabel("Requesting a device code…")
    info.setTextInteractionFlags(Qt.TextSelectableByMouse)
    info.setWordWrap(True)
    layout.addWidget(info)
    buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    state = {"ok": False}

    try:
        flow = gdrive.DeviceCodeFlow()
        request = flow.start()
    except Exception as exc:
        _warn("Google Drive", str(exc))
        return False

    info.setText(
        f"<p>On your phone or computer open:</p><p><b>{request.verification_url}</b></p>"
        f"<p>and enter the code</p><h2>{request.user_code}</h2>"
        "<p>Waiting for you to finish…</p>"
    )

    timer = QTimer(dialog)

    def poll():
        try:
            if flow.poll_once():
                state["ok"] = True
                timer.stop()
                dialog.accept()
        except Exception as exc:
            timer.stop()
            _warn("Google Drive", str(exc))
            dialog.reject()

    timer.timeout.connect(poll)
    timer.start(max(1000, int(request.interval * 1000)))
    dialog.exec_()
    timer.stop()
    return state["ok"]


class DriveBrowser(QDialog):
    """Minimal Drive browser for opening and saving documents."""

    def __init__(self, mode="open", parent=None):
        super().__init__(parent or _main_window())
        self.mode = mode
        self.setWindowTitle("Google Drive — " + ("open" if mode == "open" else "save"))
        self.resize(640, 480)
        self.client = _drive_client()
        self.folder_stack = [("root", "My Drive")]

        layout = QVBoxLayout(self)
        self.path_label = QLabel()
        layout.addWidget(self.path_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Modified", "Size"])
        self.tree.itemDoubleClicked.connect(self._activate)
        layout.addWidget(self.tree, 1)

        if mode == "save":
            row = QHBoxLayout()
            row.addWidget(QLabel("File name"))
            self.name_edit = QLineEdit()
            document = FreeCAD.ActiveDocument
            self.name_edit.setText(f"{document.Label}.FCStd" if document else "Untitled.FCStd")
            row.addWidget(self.name_edit, 1)
            layout.addLayout(row)
            self.also_fcxr = QCheckBox("Also upload an .fcxr scene for the headset")
            self.also_fcxr.setChecked(True)
            layout.addWidget(self.also_fcxr)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Open if mode == "open" else QDialogButtonBox.Save
        )
        buttons.addButton(QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh()

    def _refresh(self):
        self.tree.clear()
        self.path_label.setText(" / ".join(name for _id, name in self.folder_stack))
        if self.client is None:
            self.tree.addTopLevelItem(QTreeWidgetItem(["(not signed in)", "", ""]))
            return
        parent_id = self.folder_stack[-1][0]
        if len(self.folder_stack) > 1:
            up = QTreeWidgetItem(["..", "", ""])
            up.setData(0, Qt.UserRole, {"kind": "up"})
            self.tree.addTopLevelItem(up)
        try:
            entries = self.client.list_children(parent_id)
        except Exception as exc:
            _warn("Google Drive", str(exc))
            return
        for entry in entries:
            size = "" if entry.is_folder else f"{entry.size / 1024.0:.0f} kB"
            item = QTreeWidgetItem(
                [("📁 " if entry.is_folder else "") + entry.name, entry.modified_time or "", size]
            )
            item.setData(0, Qt.UserRole, {"kind": "folder" if entry.is_folder else "file", "entry": entry})
            self.tree.addTopLevelItem(item)

    def _activate(self, item, _column):
        data = item.data(0, Qt.UserRole) or {}
        if data.get("kind") == "up":
            self.folder_stack.pop()
            self._refresh()
        elif data.get("kind") == "folder":
            entry = data["entry"]
            self.folder_stack.append((entry.file_id, entry.name))
            self._refresh()
        elif data.get("kind") == "file" and self.mode == "open":
            self._open_entry(data["entry"])

    def _accept(self):
        if self.client is None:
            self.reject()
            return
        if self.mode == "open":
            item = self.tree.currentItem()
            data = (item.data(0, Qt.UserRole) or {}) if item else {}
            if data.get("kind") != "file":
                _warn("Google Drive", "Select a file to open.")
                return
            self._open_entry(data["entry"])
        else:
            self._save_current()

    def _open_entry(self, entry):
        from xrsync import project

        try:
            path = project.pull_drive_file(self.client, entry)
        except Exception as exc:
            _warn("Google Drive", str(exc))
            return
        if path.lower().endswith(".fcstd"):
            FreeCAD.openDocument(path)
        else:
            Gui.insert(path, FreeCAD.ActiveDocument.Name if FreeCAD.ActiveDocument else None)
        FreeCAD.Console.PrintMessage(f"XR: opened {path} from Google Drive\n")
        self.accept()

    def _save_current(self):
        document = FreeCAD.ActiveDocument
        if document is None:
            _warn("Google Drive", "There is no active document.")
            return
        from xrsync import project

        try:
            result = project.push_document_to_drive(
                self.client,
                document,
                parent_id=self.folder_stack[-1][0],
                name=self.name_edit.text(),
                also_fcxr=self.also_fcxr.isChecked(),
            )
        except Exception as exc:
            _warn("Google Drive", str(exc))
            return
        FreeCAD.Console.PrintMessage(f"XR: uploaded {result.name} to Google Drive\n")
        self.accept()


def show_drive_browser(mode="open"):
    DriveBrowser(mode=mode).exec_()


class DriveAccountDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or _main_window())
        self.setWindowTitle("Google Drive account")
        self.resize(560, 320)

        layout = QVBoxLayout(self)
        self.info = QTextBrowser()
        self.info.setOpenExternalLinks(True)
        layout.addWidget(self.info, 1)

        row = QHBoxLayout()
        layout.addLayout(row)
        self.sign_in = QPushButton("Sign in…")
        self.sign_in.clicked.connect(self._sign_in)
        row.addWidget(self.sign_in)
        self.sign_out = QPushButton("Sign out")
        self.sign_out.clicked.connect(self._sign_out)
        row.addWidget(self.sign_out)
        configure = QPushButton("OAuth client…")
        configure.clicked.connect(self._configure)
        row.addWidget(configure)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _refresh(self):
        from xrsync import gdrive

        status = gdrive.account_status()
        lines = ["<h3>Google Drive</h3>"]
        if not status.configured:
            lines.append(
                "<p>No OAuth client is configured. Google requires every application to use "
                "its own client credentials, so create one (Desktop / TV &amp; limited input "
                "device) in the "
                "<a href='https://console.cloud.google.com/apis/credentials'>Google Cloud "
                "console</a>, enable the Drive API, then press <b>OAuth client…</b>.</p>"
            )
        elif not status.signed_in:
            lines.append("<p>An OAuth client is configured, but nobody is signed in.</p>")
        else:
            lines.append(f"<p>Signed in as <b>{status.account}</b>.</p>")
            lines.append(f"<p>Cache: <code>{status.cache_dir}</code></p>")
        lines.append(
            "<p>The same account is used by the Quest application, so files you save here "
            "appear in the headset without a cable.</p>"
        )
        self.info.setHtml("".join(lines))
        self.sign_in.setEnabled(status.configured and not status.signed_in)
        self.sign_out.setEnabled(status.signed_in)

    def _sign_in(self):
        if _sign_in_flow():
            self._refresh()

    def _sign_out(self):
        from xrsync import gdrive

        gdrive.sign_out()
        self._refresh()

    def _configure(self):
        from xrsync import gdrive

        dialog = QDialog(self)
        dialog.setWindowTitle("Google OAuth client")
        layout = QFormLayout(dialog)
        client_id = QLineEdit()
        client_secret = QLineEdit()
        existing = gdrive.load_client_config()
        if existing:
            client_id.setText(existing.get("client_id", ""))
            client_secret.setText(existing.get("client_secret", ""))
        layout.addRow("Client ID", client_id)
        layout.addRow("Client secret", client_secret)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec_() == QDialog.Accepted:
            gdrive.save_client_config(client_id.text().strip(), client_secret.text().strip())
            self._refresh()


def show_drive_account_dialog():
    DriveAccountDialog().exec_()
