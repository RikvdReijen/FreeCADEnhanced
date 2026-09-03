# SPDX-License-Identifier: LGPL-2.1-or-later

SET(GameBridge_Root_SRCS
    Init.py
    InitGui.py
    GameBridgeFileIO.py
    README.md
    CHANGELOG.md
)

FILE(GLOB GameBridge_Core_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} gbcore/*.py)
FILE(GLOB GameBridge_Format_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} gbformat/*.py)
FILE(GLOB GameBridge_Link_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} gblink/*.py)
FILE(GLOB GameBridge_Targets_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} gbtargets/*.py)
FILE(GLOB GameBridge_Tests_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} Tests/*.py)
FILE(GLOB GameBridge_Tools_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} tools/*.py)

# The engine-side clients are installed rather than built: the export copies
# them next to the files it writes, so an import needs no separate installation.
FILE(GLOB GameBridge_Blender_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} clients/blender/*.py)
FILE(GLOB GameBridge_Unreal_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} clients/unreal/*.py)
FILE(GLOB GameBridge_Unity_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR}
    clients/unity/package.json
    clients/unity/README.md
)
FILE(GLOB GameBridge_UnityEditor_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR}
    clients/unity/Editor/*.cs
    clients/unity/Editor/*.asmdef
)

FILE(GLOB GameBridge_Icons RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} Resources/icons/*.svg)

SET(GameBridge_Resources_SRCS
    Resources/GameBridgePreferences.ui
    ${GameBridge_Icons}
)

SET(all_files
    ${GameBridge_Root_SRCS}
    ${GameBridge_Core_SRCS}
    ${GameBridge_Format_SRCS}
    ${GameBridge_Link_SRCS}
    ${GameBridge_Targets_SRCS}
    ${GameBridge_Tests_SRCS}
    ${GameBridge_Tools_SRCS}
    ${GameBridge_Blender_SRCS}
    ${GameBridge_Unreal_SRCS}
    ${GameBridge_Unity_SRCS}
    ${GameBridge_UnityEditor_SRCS}
    ${GameBridge_Resources_SRCS}
)

SOURCE_GROUP("" FILES ${GameBridge_Root_SRCS})
SOURCE_GROUP("gbcore" FILES ${GameBridge_Core_SRCS})
SOURCE_GROUP("gbformat" FILES ${GameBridge_Format_SRCS})
SOURCE_GROUP("gblink" FILES ${GameBridge_Link_SRCS})
SOURCE_GROUP("gbtargets" FILES ${GameBridge_Targets_SRCS})
SOURCE_GROUP("Tests" FILES ${GameBridge_Tests_SRCS})
SOURCE_GROUP("tools" FILES ${GameBridge_Tools_SRCS})
SOURCE_GROUP("clients" FILES
    ${GameBridge_Blender_SRCS}
    ${GameBridge_Unreal_SRCS}
    ${GameBridge_Unity_SRCS}
    ${GameBridge_UnityEditor_SRCS}
)
SOURCE_GROUP("Resources" FILES ${GameBridge_Resources_SRCS})

ADD_CUSTOM_TARGET(GameBridge ALL SOURCES ${all_files})

fc_copy_sources(GameBridge "${CMAKE_BINARY_DIR}/Mod/GameBridge" ${all_files})

INSTALL(
    FILES ${GameBridge_Root_SRCS}
    DESTINATION Mod/GameBridge
)
INSTALL(FILES ${GameBridge_Core_SRCS} DESTINATION Mod/GameBridge/gbcore)
INSTALL(FILES ${GameBridge_Format_SRCS} DESTINATION Mod/GameBridge/gbformat)
INSTALL(FILES ${GameBridge_Link_SRCS} DESTINATION Mod/GameBridge/gblink)
INSTALL(FILES ${GameBridge_Targets_SRCS} DESTINATION Mod/GameBridge/gbtargets)
INSTALL(FILES ${GameBridge_Tests_SRCS} DESTINATION Mod/GameBridge/Tests)
INSTALL(FILES ${GameBridge_Tools_SRCS} DESTINATION Mod/GameBridge/tools)
INSTALL(FILES ${GameBridge_Blender_SRCS} DESTINATION Mod/GameBridge/clients/blender)
INSTALL(FILES ${GameBridge_Unreal_SRCS} DESTINATION Mod/GameBridge/clients/unreal)
INSTALL(FILES ${GameBridge_Unity_SRCS} DESTINATION Mod/GameBridge/clients/unity)
INSTALL(FILES ${GameBridge_UnityEditor_SRCS} DESTINATION Mod/GameBridge/clients/unity/Editor)
INSTALL(FILES Resources/GameBridgePreferences.ui DESTINATION Mod/GameBridge/Resources)
INSTALL(FILES ${GameBridge_Icons} DESTINATION Mod/GameBridge/Resources/icons)
