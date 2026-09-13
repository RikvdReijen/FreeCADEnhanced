# SPDX-License-Identifier: LGPL-2.1-or-later

IF (BUILD_GUI)
    PYSIDE_WRAP_RC(
        RELATIVE RoboPrint_QRC_SRCS_REL
        ABSOLUTE RoboPrint_QRC_SRCS
        RESOURCES Resources/RoboPrint.qrc
    )
ENDIF (BUILD_GUI)

SET(RoboPrint_SRCS
    Init.py
    InitGui.py
    TestRoboPrintApp.py
    TestRoboPrintGui.py
)

SET(RoboPrintPackage_SRCS
    roboprint/__init__.py
    roboprint/slicing.py
    roboprint/toolpath.py
    roboprint/analysis.py
    roboprint/postprocessors.py
    roboprint/features.py
    roboprint/commands.py
)

SET(RoboPrintTests_SRCS
    RoboPrintTest/__init__.py
    RoboPrintTest/app/__init__.py
    RoboPrintTest/app/test_slicing.py
    RoboPrintTest/app/test_toolpath.py
    RoboPrintTest/app/test_analysis.py
    RoboPrintTest/app/test_postprocessors.py
    RoboPrintTest/app/test_features.py
)

SET(RoboPrintGuiIcon_SVG
    Resources/icons/RoboPrintWorkbench.svg
)

SOURCE_GROUP("" FILES ${RoboPrint_SRCS})
SOURCE_GROUP("roboprint" FILES ${RoboPrintPackage_SRCS})
SOURCE_GROUP("tests" FILES ${RoboPrintTests_SRCS})

SET(all_files ${RoboPrint_SRCS} ${RoboPrintPackage_SRCS} ${RoboPrintTests_SRCS})

ADD_CUSTOM_TARGET(RoboPrint ALL
    DEPENDS ${RoboPrint_QRC_SRCS}
    SOURCES ${all_files} ${RoboPrintGuiIcon_SVG}
)

fc_copy_sources(RoboPrint "${CMAKE_BINARY_DIR}/Mod/RoboPrint" ${all_files})

fc_copy_sources(RoboPrint "${CMAKE_BINARY_DIR}/${CMAKE_INSTALL_DATADIR}/Mod/RoboPrint" ${RoboPrintGuiIcon_SVG})

IF (BUILD_GUI)
    fc_target_copy_resource(RoboPrint
        "${CMAKE_CURRENT_BINARY_DIR}"
        "${CMAKE_BINARY_DIR}/Mod/RoboPrint"
        ${RoboPrint_QRC_SRCS_REL})
ENDIF (BUILD_GUI)

INSTALL(
    FILES
        ${RoboPrint_SRCS}
        ${RoboPrint_QRC_SRCS}
    DESTINATION
        Mod/RoboPrint
)

INSTALL(
    FILES
        ${RoboPrintPackage_SRCS}
    DESTINATION
        Mod/RoboPrint/roboprint
)

INSTALL(
    FILES
        ${RoboPrintGuiIcon_SVG}
    DESTINATION
        "${CMAKE_INSTALL_DATADIR}/Mod/RoboPrint/Resources/icons"
)

INSTALL(FILES RoboPrintTest/__init__.py DESTINATION Mod/RoboPrint/RoboPrintTest)
INSTALL(
    FILES
        RoboPrintTest/app/__init__.py
        RoboPrintTest/app/test_slicing.py
        RoboPrintTest/app/test_toolpath.py
        RoboPrintTest/app/test_analysis.py
        RoboPrintTest/app/test_postprocessors.py
        RoboPrintTest/app/test_features.py
    DESTINATION
        Mod/RoboPrint/RoboPrintTest/app
)
