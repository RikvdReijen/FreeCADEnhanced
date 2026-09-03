# SPDX-License-Identifier: LGPL-2.1-or-later

SET(XR_Root_SRCS
    Init.py
    InitGui.py
    XRFileIO.py
    README.md
    CHANGELOG.md
    NOTICE.md
    LICENSE-upstream.txt
)

SET(XR_Core_SRCS
    xrcore/__init__.py
    xrcore/commands.py
    xrcore/commonXR.py
    xrcore/controllerXR.py
    xrcore/documentInteraction.py
    xrcore/environment_bridge.py
    xrcore/menuCoin.py
    xrcore/menu_ext.py
    xrcore/movementXR.py
    xrcore/paint_bridge.py
    xrcore/preferences.py
    xrcore/preferences_xr.py
    xrcore/previewCoin.py
    xrcore/qtWidgetRender.py
    xrcore/service.py
    xrcore/ui_dialogs.py
)

FILE(GLOB XR_Env_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} xrenv/*.py)
FILE(GLOB XR_EnvDefs_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} xrenv/environments/*.py)
FILE(GLOB XR_Paint_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} xrpaint/*.py)
FILE(GLOB XR_Sync_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} xrsync/*.py)
FILE(GLOB XR_Tests_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} Tests/*.py)
FILE(GLOB XR_Tools_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} tools/*.py)

FILE(GLOB XR_Icons RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} Resources/icons/*.svg)
FILE(GLOB XR_Controllers RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} Resources/controllers/*.iv)
FILE(GLOB XR_EnvSpecs RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} Resources/environments/*.json)
FILE(GLOB XR_Docs RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} Resources/doc/*.md)

SET(XR_Resources_SRCS
    Resources/XRPreferences.ui
    ${XR_Icons}
    ${XR_Controllers}
    ${XR_EnvSpecs}
    ${XR_Docs}
)

SET(all_files
    ${XR_Root_SRCS}
    ${XR_Core_SRCS}
    ${XR_Env_SRCS}
    ${XR_EnvDefs_SRCS}
    ${XR_Paint_SRCS}
    ${XR_Sync_SRCS}
    ${XR_Tests_SRCS}
    ${XR_Tools_SRCS}
    ${XR_Resources_SRCS}
)

SOURCE_GROUP("" FILES ${XR_Root_SRCS})
SOURCE_GROUP("xrcore" FILES ${XR_Core_SRCS})
SOURCE_GROUP("xrenv" FILES ${XR_Env_SRCS} ${XR_EnvDefs_SRCS})
SOURCE_GROUP("xrpaint" FILES ${XR_Paint_SRCS})
SOURCE_GROUP("xrsync" FILES ${XR_Sync_SRCS})
SOURCE_GROUP("Tests" FILES ${XR_Tests_SRCS})
SOURCE_GROUP("Resources" FILES ${XR_Resources_SRCS})

ADD_CUSTOM_TARGET(XR ALL SOURCES ${all_files})

fc_copy_sources(XR "${CMAKE_BINARY_DIR}/Mod/XR" ${all_files})

INSTALL(
    FILES ${XR_Root_SRCS}
    DESTINATION Mod/XR
)
INSTALL(FILES ${XR_Core_SRCS} DESTINATION Mod/XR/xrcore)
INSTALL(FILES ${XR_Env_SRCS} DESTINATION Mod/XR/xrenv)
INSTALL(FILES ${XR_EnvDefs_SRCS} DESTINATION Mod/XR/xrenv/environments)
INSTALL(FILES ${XR_Paint_SRCS} DESTINATION Mod/XR/xrpaint)
INSTALL(FILES ${XR_Sync_SRCS} DESTINATION Mod/XR/xrsync)
INSTALL(FILES ${XR_Tests_SRCS} DESTINATION Mod/XR/Tests)
INSTALL(FILES ${XR_Tools_SRCS} DESTINATION Mod/XR/tools)
INSTALL(FILES Resources/XRPreferences.ui DESTINATION Mod/XR/Resources)
INSTALL(FILES ${XR_Icons} DESTINATION Mod/XR/Resources/icons)
INSTALL(FILES ${XR_Controllers} DESTINATION Mod/XR/Resources/controllers)
INSTALL(FILES ${XR_EnvSpecs} DESTINATION Mod/XR/Resources/environments)
INSTALL(FILES ${XR_Docs} DESTINATION Mod/XR/Resources/doc)

# The Meta Quest application is an Android project built with Gradle; it is not
# part of the desktop build. See quest/README.md.
