# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI commands, the XR server manager and the settings dialog."""

import os
import webbrowser

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui, QtWidgets

from . import document as ghdoc, markers, protocol, qrcode_gen, resources
from .session import Session
from .xrserver import XRServer

PREF_PATH = "User parameter:BaseApp/Preferences/Mod/Grasshopper"

TOOLBAR = [
    "Grasshopper_NewDefinition",
    "Grasshopper_OpenCanvas",
    "Grasshopper_Recompute",
    "Grasshopper_Bake",
]
XR_TOOLBAR = ["Grasshopper_StartXR", "Grasshopper_PrintMarker", "Grasshopper_Settings"]
MENU = TOOLBAR + ["Separator"] + XR_TOOLBAR + ["Separator", "Grasshopper_Example"]

_canvases = {}  # (doc name, obj name) -> CanvasWidget


def prefs():
    return FreeCAD.ParamGet(PREF_PATH)


def pref_values():
    p = prefs()
    return {
        "port": p.GetInt("Port", 8765),
        "host": p.GetString("Host", "0.0.0.0"),
        "certfile": p.GetString("CertFile", ""),
        "keyfile": p.GetString("KeyFile", ""),
        "marker_mm": p.GetFloat("MarkerSizeMm", markers.DEFAULT_MARKER_MM),
        "scale_mm": p.GetFloat("CanvasScaleMm", markers.DEFAULT_SCALE_MM),
        "profile": p.GetString("Profile", protocol.DEFAULT_PROFILE),
        "open_browser": p.GetBool("OpenBrowser", True),
    }


def _selected_definition():
    for obj in FreeCADGui.Selection.getSelection():
        if ghdoc.is_definition(obj):
            return obj
    defs = ghdoc.definitions()
    if len(defs) == 1:
        return defs[0]
    if defs:
        # prefer the one whose canvas is open
        for obj in defs:
            if (obj.Document.Name, obj.Name) in _canvases:
                return obj
        return defs[0]
    return None


# ---------------------------------------------------------------- canvas
def open_canvas(obj):
    """Open (or raise) the node editor of a definition as an MDI window."""
    from . import canvas_qt

    key = (obj.Document.Name, obj.Name)
    widget = _canvases.get(key)
    if widget is not None and widget.parent() is not None:
        widget.parent().show()
        widget.parent().raise_()
        return widget
    graph = ghdoc.graph_for(obj)
    widget = canvas_qt.CanvasWidget(graph, "Grasshopper: %s" % obj.Label)
    widget.setWindowIcon(QtGui.QIcon(resources.icon_path("GrasshopperWorkbench.svg")))
    widget.add_action(
        "xr", "Start XR", lambda: XRManager.instance().toggle(obj, widget), checkable=True
    )
    widget.add_action("marker", "Print marker", lambda: print_marker(obj))
    widget.add_action("bake", "Bake", lambda: ghdoc.bake(obj))
    widget.extra_actions["xr"].setChecked(XRManager.instance().is_running_for(obj))
    main = FreeCADGui.getMainWindow()
    mdi = main.findChild(QtWidgets.QMdiArea)
    if mdi is not None:
        sub = mdi.addSubWindow(widget)
        sub.setWindowTitle(widget.windowTitle())
        sub.resize(900, 600)
        sub.show()
    else:  # pragma: no cover - no MDI area (unusual)
        dock = QtWidgets.QDockWidget(widget.windowTitle(), main)
        dock.setWidget(widget)
        main.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    _canvases[key] = widget
    widget.status(
        "Double-click empty space or press Space to add nodes. Drag from an output dot to wire."
    )
    return widget


# ------------------------------------------------------------------ XR
class _Dispatcher(QtCore.QObject):
    """Hops callables from the server thread onto the Qt main thread."""

    call = QtCore.Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, QtCore.Qt.QueuedConnection)

    def _run(self, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            FreeCAD.Console.PrintError("Grasshopper XR: %s\n" % exc)


class XRManager:
    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.server = None
        self.session = None
        self.obj_key = None
        self.dispatcher = _Dispatcher()

    def is_running_for(self, obj):
        return (
            self.server is not None
            and self.server.running
            and self.obj_key == (obj.Document.Name, obj.Name)
        )

    def toggle(self, obj, widget=None):
        if self.is_running_for(obj):
            self.stop()
            if widget:
                widget.extra_actions["xr"].setChecked(False)
                widget.status("XR server stopped")
        else:
            self.start(obj, widget)

    def start(self, obj, widget=None):
        self.stop()
        cfg = pref_values()
        graph = ghdoc.graph_for(obj)

        def autosave(g, obj_key=(obj.Document.Name, obj.Name)):
            doc = FreeCAD.getDocument(obj_key[0])
            target = doc.getObject(obj_key[1]) if doc else None
            if target is not None:
                target.Proxy.save(target)
                if target.AutoRecompute:
                    doc.recompute()

        self.session = Session(
            graph,
            marker_mm=cfg["marker_mm"],
            scale_mm=cfg["scale_mm"],
            profile=cfg["profile"],
            autosave=autosave,
        )
        self.server = XRServer(
            self.session,
            resources.xr_client_dir(),
            host=cfg["host"],
            port=cfg["port"],
            certfile=cfg["certfile"] or None,
            keyfile=cfg["keyfile"] or None,
            dispatch=lambda fn: self.dispatcher.call.emit(fn),
        )
        try:
            self.server.start()
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(
                None, "Grasshopper XR", "Could not start the XR server:\n%s" % exc
            )
            self.server = None
            return
        self.session.base_url = self.server.base_url()
        self.obj_key = (obj.Document.Name, obj.Name)
        urls = self.server.urls()
        FreeCAD.Console.PrintMessage("Grasshopper XR server: %s\n" % ", ".join(urls))
        if widget:
            widget.extra_actions["xr"].setChecked(True)
            widget.status("XR server running at " + urls[-1])
        self.show_connect_dialog(urls)

    def stop(self):
        if self.server is not None:
            self.server.stop()
        self.server = None
        self.session = None
        self.obj_key = None

    def show_connect_dialog(self, urls):
        cfg = pref_values()
        dlg = QtWidgets.QDialog(FreeCADGui.getMainWindow())
        dlg.setWindowTitle("Grasshopper mode - XR canvas")
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.addWidget(QtWidgets.QLabel("<b>Open this on the headset / phone:</b>"))
        for url in urls:
            edit = QtWidgets.QLineEdit(url)
            edit.setReadOnly(True)
            lay.addWidget(edit)
        best = [u for u in urls if "localhost" not in u] or urls
        lay.addWidget(_qr_label(best[-1]))
        lay.addWidget(
            QtWidgets.QLabel(
                "WebXR needs a secure context. Easiest on Quest: <code>adb reverse tcp:%d tcp:%d</code> and open "
                "<code>http://localhost:%d/xr</code>. Otherwise set a certificate in the settings.<br>"
                "Print the canvas marker (<i>Print marker</i>) at 100%% and lay it on the table."
                % (self.server.bound_port, self.server.bound_port, self.server.bound_port)
            )
        )
        buttons = QtWidgets.QDialogButtonBox()
        marker = buttons.addButton("Open marker page", QtWidgets.QDialogButtonBox.ActionRole)
        browser = buttons.addButton("Open in browser", QtWidgets.QDialogButtonBox.ActionRole)
        buttons.addButton(QtWidgets.QDialogButtonBox.Close)
        marker.clicked.connect(lambda: webbrowser.open(urls[0].replace("/xr", "/marker.html")))
        browser.clicked.connect(lambda: webbrowser.open(urls[0]))
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        if cfg["open_browser"]:
            QtCore.QTimer.singleShot(300, lambda: webbrowser.open(urls[0]))
        dlg.show()
        self._dialog = dlg


def _qr_label(text, size=180):
    modules = qrcode_gen.encode_best_available(text, "M")
    n = len(modules)
    scale = max(1, size // (n + 8))
    px = (n + 8) * scale
    image = QtGui.QImage(px, px, QtGui.QImage.Format_RGB32)
    image.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(image)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor("black"))
    for y, row in enumerate(modules):
        for x, dark in enumerate(row):
            if dark:
                painter.drawRect((x + 4) * scale, (y + 4) * scale, scale, scale)
    painter.end()
    label = QtWidgets.QLabel()
    label.setPixmap(QtGui.QPixmap.fromImage(image))
    label.setToolTip(text)
    return label


def print_marker(obj):
    """Save the canvas marker as SVG (and open the printable page if the server runs)."""
    cfg = pref_values()
    mgr = XRManager.instance()
    base = mgr.server.base_url() if mgr.is_running_for(obj) else ""
    spec = markers.MarkerSpec(
        ghdoc.graph_for(obj).canvas_id, cfg["marker_mm"], base, cfg["scale_mm"]
    )
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        None, "Save canvas marker", "grasshopper-marker-%s.svg" % spec.canvas_id, "SVG (*.svg)"
    )
    if not path:
        return
    modules = qrcode_gen.encode_best_available(spec.payload(), "M")
    module_mm = spec.size_mm / (len(modules) + 8)
    svg = qrcode_gen.to_svg(
        modules,
        module_mm=module_mm,
        quiet=4,
        label=spec.label,
        caption="%g mm - print at 100%%" % spec.size_mm,
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    FreeCAD.Console.PrintMessage(
        "Marker saved to %s (print at 100%%, %g mm)\n" % (path, spec.size_mm)
    )
    if base:
        webbrowser.open(base + "/marker.html")


# ------------------------------------------------------------- settings
class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Grasshopper mode settings")
        cfg = pref_values()
        form = QtWidgets.QFormLayout(self)
        self.port = QtWidgets.QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(cfg["port"])
        self.host = QtWidgets.QLineEdit(cfg["host"])
        self.cert = QtWidgets.QLineEdit(cfg["certfile"])
        self.key = QtWidgets.QLineEdit(cfg["keyfile"])
        self.marker = QtWidgets.QDoubleSpinBox()
        self.marker.setRange(20, 400)
        self.marker.setValue(cfg["marker_mm"])
        self.scale = QtWidgets.QDoubleSpinBox()
        self.scale.setRange(0.1, 5)
        self.scale.setSingleStep(0.1)
        self.scale.setValue(cfg["scale_mm"])
        self.profile = QtWidgets.QComboBox()
        for pid, p in protocol.INTERACTION_PROFILES.items():
            self.profile.addItem(p["label"], pid)
        self.profile.setCurrentIndex(max(0, self.profile.findData(cfg["profile"])))
        self.browser = QtWidgets.QCheckBox(
            "Open the XR page in the desktop browser when the server starts"
        )
        self.browser.setChecked(cfg["open_browser"])
        form.addRow("Server port", self.port)
        form.addRow("Bind address", self.host)
        form.addRow("TLS certificate (optional)", self.cert)
        form.addRow("TLS key (optional)", self.key)
        form.addRow("Marker size (mm)", self.marker)
        form.addRow("Canvas scale (mm per canvas unit)", self.scale)
        form.addRow("Default interaction concept", self.profile)
        form.addRow(self.browser)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def accept(self):
        p = prefs()
        p.SetInt("Port", self.port.value())
        p.SetString("Host", self.host.text().strip() or "0.0.0.0")
        p.SetString("CertFile", self.cert.text().strip())
        p.SetString("KeyFile", self.key.text().strip())
        p.SetFloat("MarkerSizeMm", self.marker.value())
        p.SetFloat("CanvasScaleMm", self.scale.value())
        p.SetString("Profile", self.profile.currentData())
        p.SetBool("OpenBrowser", self.browser.isChecked())
        super().accept()


# ------------------------------------------------------------- commands
class _Command:
    icon = "GrasshopperWorkbench.svg"
    text = ""
    tooltip = ""

    def GetResources(self):
        return {
            "Pixmap": resources.icon_path(self.icon),
            "MenuText": self.text,
            "ToolTip": self.tooltip,
        }

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None


class NewDefinition(_Command):
    icon = "Grasshopper_NewDefinition.svg"
    text = "New definition"
    tooltip = "Create an empty Grasshopper mode definition and open its canvas"

    def IsActive(self):
        return True

    def Activated(self):
        doc = FreeCAD.ActiveDocument or FreeCAD.newDocument("Grasshopper")
        obj = ghdoc.make_definition(doc, "Definition")
        doc.recompute()
        open_canvas(obj)


class ExampleDefinition(_Command):
    icon = "Grasshopper_NewDefinition.svg"
    text = "Example definition"
    tooltip = "Create the example: sliders driving a polar array cut from a cylinder"

    def IsActive(self):
        return True

    def Activated(self):
        doc = FreeCAD.ActiveDocument or FreeCAD.newDocument("Grasshopper")
        obj = ghdoc.make_definition(doc, "Example", example=True)
        doc.recompute()
        open_canvas(obj)
        FreeCADGui.SendMsgToActiveView("ViewFit")


class OpenCanvas(_Command):
    icon = "Grasshopper_OpenCanvas.svg"
    text = "Open canvas"
    tooltip = "Open the node canvas of the selected definition"

    def IsActive(self):
        return _selected_definition() is not None

    def Activated(self):
        obj = _selected_definition()
        if obj:
            open_canvas(obj)


class Recompute(_Command):
    icon = "Grasshopper_OpenCanvas.svg"
    text = "Recompute definition"
    tooltip = "Re-evaluate every node of the selected definition"

    def IsActive(self):
        return _selected_definition() is not None

    def Activated(self):
        obj = _selected_definition()
        if obj:
            ghdoc.graph_for(obj).evaluate(force=True)
            obj.touch()
            obj.Document.recompute()


class Bake(_Command):
    icon = "Grasshopper_Bake.svg"
    text = "Bake"
    tooltip = "Create real Part objects from the geometry reaching Bake nodes"

    def IsActive(self):
        return _selected_definition() is not None

    def Activated(self):
        obj = _selected_definition()
        if obj:
            created = ghdoc.bake(obj)
            FreeCAD.Console.PrintMessage("Baked %d object(s)\n" % len(created))


class StartXR(_Command):
    icon = "Grasshopper_StartXR.svg"
    text = "Start/stop XR canvas"
    tooltip = "Serve the mixed-reality canvas (WebXR) for the selected definition"

    def IsActive(self):
        return _selected_definition() is not None

    def Activated(self):
        obj = _selected_definition()
        if obj:
            widget = _canvases.get((obj.Document.Name, obj.Name))
            XRManager.instance().toggle(obj, widget)


class PrintMarker(_Command):
    icon = "Grasshopper_PrintMarker.svg"
    text = "Print canvas marker"
    tooltip = "Save the QR marker that anchors this canvas on your table"

    def IsActive(self):
        return _selected_definition() is not None

    def Activated(self):
        obj = _selected_definition()
        if obj:
            print_marker(obj)


class Settings(_Command):
    icon = "GrasshopperWorkbench.svg"
    text = "Settings..."
    tooltip = "Server port, TLS, marker size, canvas scale and default interaction concept"

    def IsActive(self):
        return True

    def Activated(self):
        SettingsDialog(FreeCADGui.getMainWindow()).exec_()


_COMMANDS = {
    "Grasshopper_NewDefinition": NewDefinition,
    "Grasshopper_Example": ExampleDefinition,
    "Grasshopper_OpenCanvas": OpenCanvas,
    "Grasshopper_Recompute": Recompute,
    "Grasshopper_Bake": Bake,
    "Grasshopper_StartXR": StartXR,
    "Grasshopper_PrintMarker": PrintMarker,
    "Grasshopper_Settings": Settings,
}


def register():
    for name, cls in _COMMANDS.items():
        FreeCADGui.addCommand(name, cls())
