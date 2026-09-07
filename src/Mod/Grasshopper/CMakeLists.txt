# SPDX-License-Identifier: LGPL-2.1-or-later

SET(Grasshopper_SRCS
    Init.py
    InitGui.py
    importGrasshopper.py
    TestGrasshopperApp.py
    README.md
)
SOURCE_GROUP("" FILES ${Grasshopper_SRCS})

SET(GrasshopperMode_SRCS
    grasshoppermode/__init__.py
    grasshoppermode/canvas_qt.py
    grasshoppermode/commands.py
    grasshoppermode/document.py
    grasshoppermode/geometry.py
    grasshoppermode/graph.py
    grasshoppermode/layout.py
    grasshoppermode/markers.py
    grasshoppermode/nodes.py
    grasshoppermode/protocol.py
    grasshoppermode/qrcode_gen.py
    grasshoppermode/resources.py
    grasshoppermode/session.py
    grasshoppermode/xrserver.py
)

SET(GrasshopperTests_SRCS
    grasshoppermode/tests/__init__.py
    grasshoppermode/tests/test_freecad.py
    grasshoppermode/tests/test_graph.py
    grasshoppermode/tests/test_layout.py
    grasshoppermode/tests/test_markers.py
    grasshoppermode/tests/test_nodes.py
    grasshoppermode/tests/test_qrcode.py
    grasshoppermode/tests/test_session.py
    grasshoppermode/tests/test_xr_client.py
    grasshoppermode/tests/test_xrserver.py
)

SET(GrasshopperIcons_SRCS
    Resources/icons/GrasshopperWorkbench.svg
    Resources/icons/Grasshopper_NewDefinition.svg
    Resources/icons/Grasshopper_OpenCanvas.svg
    Resources/icons/Grasshopper_StartXR.svg
    Resources/icons/Grasshopper_PrintMarker.svg
    Resources/icons/Grasshopper_Bake.svg
)

SET(GrasshopperXR_SRCS
    Resources/xr/index.html
    Resources/xr/app.js
    Resources/xr/anchor.js
    Resources/xr/canvasview.js
    Resources/xr/gestures.js
    Resources/xr/input.js
    Resources/xr/math3d.js
    Resources/xr/net.js
    Resources/xr/renderer.js
    Resources/xr/sim.js
)

ADD_CUSTOM_TARGET(Grasshopper ALL
    SOURCES ${Grasshopper_SRCS} ${GrasshopperMode_SRCS} ${GrasshopperTests_SRCS} ${GrasshopperIcons_SRCS} ${GrasshopperXR_SRCS}
)

fc_copy_sources(Grasshopper "${CMAKE_BINARY_DIR}/Mod/Grasshopper" ${Grasshopper_SRCS} ${GrasshopperMode_SRCS} ${GrasshopperTests_SRCS})
fc_copy_sources(Grasshopper "${CMAKE_BINARY_DIR}/${CMAKE_INSTALL_DATADIR}/Mod/Grasshopper" ${GrasshopperIcons_SRCS} ${GrasshopperXR_SRCS})

INSTALL(FILES ${Grasshopper_SRCS} DESTINATION Mod/Grasshopper)
INSTALL(FILES ${GrasshopperMode_SRCS} DESTINATION Mod/Grasshopper/grasshoppermode)
INSTALL(FILES ${GrasshopperTests_SRCS} DESTINATION Mod/Grasshopper/grasshoppermode/tests)
INSTALL(FILES ${GrasshopperIcons_SRCS} DESTINATION "${CMAKE_INSTALL_DATADIR}/Mod/Grasshopper/Resources/icons")
INSTALL(FILES ${GrasshopperXR_SRCS} DESTINATION "${CMAKE_INSTALL_DATADIR}/Mod/Grasshopper/Resources/xr")
