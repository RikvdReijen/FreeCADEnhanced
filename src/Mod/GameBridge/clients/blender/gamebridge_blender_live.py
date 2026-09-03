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
"""Blender add-on for the GameBridge live link.

Connects to a running FreeCAD and rebuilds the document in Blender as it is
edited: adjust a fillet in FreeCAD, see it in the viewport without exporting,
importing, or deleting the previous copy.

    Edit > Preferences > Add-ons > Install, pick this file
    3D viewport > sidebar (N) > GameBridge > Connect

The link's own protocol lives in FreeCAD's ``Mod/GameBridge`` folder, and this
add-on imports it from there rather than carrying a second copy: two
implementations of one wire format is how a bridge starts silently disagreeing
with itself.  The usual install locations are searched automatically and the
add-on's preferences take a path when FreeCAD is somewhere unusual.

Blender's API is only safe to touch from the main thread, so nothing is applied
in the network thread.  Incoming messages are queued and drained by a timer,
which is Blender's supported way to get work from a background thread onto the
main one.
"""

import os
import queue
import sys

bl_info = {
    "name": "FreeCAD GameBridge live link",
    "author": "FreeCAD Project Association",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "3D viewport > sidebar > GameBridge",
    "description": "Mirror a FreeCAD document into Blender as it is edited",
    "category": "Import-Export",
}

#: How often the main thread drains the queue, in seconds.
POLL_INTERVAL = 0.1

_MODULE_CANDIDATES = (
    "/usr/share/freecad/Mod/GameBridge",
    "/usr/lib/freecad/Mod/GameBridge",
    "/usr/lib/freecad-python3/Mod/GameBridge",
    "/opt/freecad/Mod/GameBridge",
    "/Applications/FreeCAD.app/Contents/Resources/Mod/GameBridge",
    "C:/Program Files/FreeCAD/Mod/GameBridge",
    os.path.expanduser("~/.local/share/FreeCAD/Mod/GameBridge"),
    os.path.expanduser("~/.FreeCAD/Mod/GameBridge"),
)


# ---------------------------------------------------------------------------
# Finding the bridge, and turning its mirror into something Blender can build.
# Both halves are plain Python and are tested in FreeCAD's own suite.
# ---------------------------------------------------------------------------


def candidate_paths(extra=None):
    """Where the GameBridge module might live, best guess first."""
    paths = []
    if extra:
        paths.append(os.path.expanduser(str(extra)))
    environment = os.environ.get("GAMEBRIDGE_MODULE")
    if environment:
        paths.append(os.path.expanduser(environment))
    # A copy sitting next to this file wins over an installed one, which is
    # what makes running from a source checkout work.
    paths.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    paths.extend(_MODULE_CANDIDATES)
    return paths


def find_module_path(extra=None, exists=os.path.isdir):
    """The first candidate that actually holds the bridge, or ``None``."""
    for path in candidate_paths(extra):
        if path and exists(os.path.join(path, "gblink")):
            return path
    return None


def import_link(extra=None):
    """Import ``gblink`` from wherever FreeCAD installed it."""
    path = find_module_path(extra)
    if path is None:
        raise ImportError(
            "the GameBridge module could not be found. Set its path in the "
            "add-on preferences - it is the Mod/GameBridge folder inside your "
            "FreeCAD installation."
        )
    if path not in sys.path:
        sys.path.append(path)
    from gblink import LinkClient  # noqa: F401

    return LinkClient


def mesh_data_from_mirror(mirror):
    """Turn a :class:`gblink.client.SceneMirror` into Blender-shaped data.

    Blender's ``from_pydata`` wants vertices as tuples and faces as tuples of
    indices, and object transforms as a row-major 4x4.  Producing that here,
    away from bpy, is what lets the conversion be tested.
    """
    objects = []
    for key in mirror.order:
        node = mirror.nodes.get(key)
        if node is None:
            continue
        entry = {
            "key": key,
            "name": node.get("name") or key,
            "parent": node.get("parent"),
            "visible": node.get("visible", True),
            "matrix": node.get("transform"),
            "source": node.get("source"),
            "vertices": [],
            "faces": [],
        }
        mesh = mirror.meshes.get(node.get("mesh")) if node.get("mesh") else None
        if mesh:
            positions = mesh.get("positions", [])
            entry["vertices"] = [
                tuple(positions[i:i + 3]) for i in range(0, len(positions), 3)
            ]
            indices = mesh.get("indices", [])
            entry["faces"] = [
                tuple(indices[i:i + 3]) for i in range(0, len(indices), 3)
            ]
        objects.append(entry)
    return objects


# ---------------------------------------------------------------------------
# The Blender half.
# ---------------------------------------------------------------------------

try:  # pragma: no cover - only inside Blender
    import bpy
    from bpy.props import BoolProperty, IntProperty, StringProperty

    _pending = queue.Queue()
    _client = None
    _state = {"connected": False, "message": "not connected", "updates": 0}

    class GameBridgePreferences(bpy.types.AddonPreferences):
        bl_idname = __name__

        module_path: StringProperty(
            name="GameBridge module",
            description="The Mod/GameBridge folder inside your FreeCAD installation. "
            "Leave empty to search the usual places.",
            subtype="DIR_PATH",
            default="",
        )
        host: StringProperty(name="Host", default="127.0.0.1")
        port: IntProperty(name="Port", default=54321, min=1024, max=65535)
        token: StringProperty(name="Token", default="", subtype="PASSWORD")
        collection_name: StringProperty(name="Collection", default="FreeCAD live")

        def draw(self, context):
            layout = self.layout
            layout.prop(self, "module_path")
            row = layout.row()
            row.prop(self, "host")
            row.prop(self, "port")
            layout.prop(self, "token")
            layout.prop(self, "collection_name")

    def _preferences(context):
        return context.preferences.addons[__name__].preferences

    def _on_change(mirror, change):
        """Runs on the network thread: queue the work, touch nothing."""
        _pending.put(mesh_data_from_mirror(mirror))

    def _on_error(problem):
        _state["message"] = str(problem)

    def _drain():
        """Runs on the main thread, from Blender's timer."""
        applied = None
        while True:
            try:
                applied = _pending.get_nowait()
            except queue.Empty:
                break
        if applied is not None:
            # Only the newest state is worth building; the intermediate ones
            # were superseded before the timer got to them.
            _apply(applied)
            _state["updates"] += 1
            _state["message"] = "%d object(s), %d update(s)" % (
                len(applied), _state["updates"]
            )
        return POLL_INTERVAL

    def _apply(objects):
        preferences = bpy.context.preferences.addons[__name__].preferences
        name = preferences.collection_name
        collection = bpy.data.collections.get(name)
        if collection is None:
            collection = bpy.data.collections.new(name)
            bpy.context.scene.collection.children.link(collection)

        wanted = {entry["key"] for entry in objects}
        by_key = {}
        for existing in list(collection.objects):
            if existing.get("gamebridge_key") in wanted:
                by_key[existing["gamebridge_key"]] = existing
            else:
                bpy.data.objects.remove(existing, do_unlink=True)

        for entry in objects:
            obj = by_key.get(entry["key"])
            if obj is None:
                mesh = bpy.data.meshes.new(entry["name"])
                obj = bpy.data.objects.new(entry["name"], mesh)
                obj["gamebridge_key"] = entry["key"]
                collection.objects.link(obj)
            obj.name = entry["name"]
            if entry["source"]:
                obj["freecad_object"] = entry["source"]
            mesh = obj.data
            if mesh is not None and entry["vertices"]:
                mesh.clear_geometry()
                mesh.from_pydata(entry["vertices"], [], entry["faces"])
                mesh.update()
            matrix = entry["matrix"]
            if matrix:
                obj.matrix_local = [matrix[i * 4:i * 4 + 4] for i in range(4)]
            obj.hide_viewport = not entry["visible"]
            by_key[entry["key"]] = obj

        for entry in objects:
            parent = by_key.get(entry["parent"]) if entry["parent"] else None
            obj = by_key.get(entry["key"])
            if obj is not None and obj.parent is not parent:
                obj.parent = parent

    class GAMEBRIDGE_OT_connect(bpy.types.Operator):
        """Connect to a FreeCAD live link"""

        bl_idname = "gamebridge.connect"
        bl_label = "Connect to FreeCAD"

        def execute(self, context):
            global _client
            if _client is not None and _client.connected:
                self.report({"INFO"}, "already connected")
                return {"CANCELLED"}
            preferences = _preferences(context)
            try:
                LinkClient = import_link(preferences.module_path or None)
                _client = LinkClient(
                    host=preferences.host,
                    port=preferences.port,
                    name="blender",
                    engine="blender",
                    token=preferences.token or None,
                    on_change=_on_change,
                    on_error=_on_error,
                )
                _client.connect()
            except Exception as problem:
                _state["message"] = str(problem)
                self.report({"ERROR"}, str(problem))
                return {"CANCELLED"}
            _state["connected"] = True
            _state["message"] = "connected to %s:%d" % (preferences.host, preferences.port)
            if not bpy.app.timers.is_registered(_drain):
                bpy.app.timers.register(_drain, persistent=True)
            self.report({"INFO"}, _state["message"])
            return {"FINISHED"}

    class GAMEBRIDGE_OT_disconnect(bpy.types.Operator):
        """Disconnect from the FreeCAD live link"""

        bl_idname = "gamebridge.disconnect"
        bl_label = "Disconnect"

        def execute(self, context):
            global _client
            if _client is not None:
                _client.close()
                _client = None
            if bpy.app.timers.is_registered(_drain):
                bpy.app.timers.unregister(_drain)
            _state["connected"] = False
            _state["message"] = "not connected"
            return {"FINISHED"}

    class GAMEBRIDGE_OT_resync(bpy.types.Operator):
        """Ask FreeCAD to send the whole document again"""

        bl_idname = "gamebridge.resync"
        bl_label = "Resync"

        @classmethod
        def poll(cls, context):
            return _client is not None and _client.connected

        def execute(self, context):
            _client.request_resync()
            return {"FINISHED"}

    class GAMEBRIDGE_PT_panel(bpy.types.Panel):
        bl_label = "GameBridge"
        bl_idname = "GAMEBRIDGE_PT_panel"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "GameBridge"

        def draw(self, context):
            layout = self.layout
            connected = _client is not None and _client.connected
            layout.label(text=_state["message"])
            if connected:
                layout.operator(GAMEBRIDGE_OT_disconnect.bl_idname, icon="UNLINKED")
                layout.operator(GAMEBRIDGE_OT_resync.bl_idname, icon="FILE_REFRESH")
            else:
                layout.operator(GAMEBRIDGE_OT_connect.bl_idname, icon="LINKED")

    _CLASSES = (
        GameBridgePreferences,
        GAMEBRIDGE_OT_connect,
        GAMEBRIDGE_OT_disconnect,
        GAMEBRIDGE_OT_resync,
        GAMEBRIDGE_PT_panel,
    )

    def register():
        for cls in _CLASSES:
            bpy.utils.register_class(cls)

    def unregister():
        global _client
        if _client is not None:
            _client.close()
            _client = None
        if bpy.app.timers.is_registered(_drain):
            bpy.app.timers.unregister(_drain)
        for cls in reversed(_CLASSES):
            bpy.utils.unregister_class(cls)

except ImportError:  # not running inside Blender

    def register():
        raise RuntimeError("this add-on has to be registered from inside Blender")

    def unregister():
        register()
