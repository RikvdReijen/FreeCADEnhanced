# SPDX-License-Identifier: LGPL-2.1-or-later

IF (BUILD_GUI)
    PYSIDE_WRAP_RC(
        RELATIVE Freeform_QRC_SRCS_REL
        ABSOLUTE Freeform_QRC_SRCS
        RESOURCES Resources/Freeform.qrc
    )
ENDIF (BUILD_GUI)

SET(Freeform_SRCS
    Init.py
    InitGui.py
    TestFreeformApp.py
    TestFreeformGui.py
)

SET(FreeformPackage_SRCS
    freeform/__init__.py
    freeform/geometry.py
    freeform/features.py
    freeform/workplane.py
    freeform/tracker.py
    freeform/palette.py
    freeform/commands.py
)

SET(FreeformTests_SRCS
    FreeformTest/__init__.py
    FreeformTest/app/__init__.py
    FreeformTest/app/test_geometry.py
    FreeformTest/app/test_features.py
    FreeformTest/app/test_workplane.py
    FreeformTest/app/test_palette.py
)

SET(FreeformGuiIcon_SVG
    Resources/icons/FreeformWorkbench.svg
)

SOURCE_GROUP("" FILES ${Freeform_SRCS})
SOURCE_GROUP("freeform" FILES ${FreeformPackage_SRCS})
SOURCE_GROUP("tests" FILES ${FreeformTests_SRCS})

SET(all_files ${Freeform_SRCS} ${FreeformPackage_SRCS} ${FreeformTests_SRCS})

ADD_CUSTOM_TARGET(Freeform ALL
    DEPENDS ${Freeform_QRC_SRCS}
    SOURCES ${all_files} ${FreeformGuiIcon_SVG}
)

fc_copy_sources(Freeform "${CMAKE_BINARY_DIR}/Mod/Freeform" ${all_files})

fc_copy_sources(Freeform "${CMAKE_BINARY_DIR}/${CMAKE_INSTALL_DATADIR}/Mod/Freeform" ${FreeformGuiIcon_SVG})

IF (BUILD_GUI)
    fc_target_copy_resource(Freeform
        "${CMAKE_CURRENT_BINARY_DIR}"
        "${CMAKE_BINARY_DIR}/Mod/Freeform"
        ${Freeform_QRC_SRCS_REL})
ENDIF (BUILD_GUI)

INSTALL(
    FILES
        ${Freeform_SRCS}
        ${Freeform_QRC_SRCS}
    DESTINATION
        Mod/Freeform
)

INSTALL(
    FILES
        ${FreeformPackage_SRCS}
    DESTINATION
        Mod/Freeform/freeform
)

INSTALL(
    FILES
        ${FreeformGuiIcon_SVG}
    DESTINATION
        "${CMAKE_INSTALL_DATADIR}/Mod/Freeform/Resources/icons"
)

INSTALL(FILES FreeformTest/__init__.py DESTINATION Mod/Freeform/FreeformTest)
INSTALL(
    FILES
        FreeformTest/app/__init__.py
        FreeformTest/app/test_geometry.py
        FreeformTest/app/test_features.py
        FreeformTest/app/test_workplane.py
        FreeformTest/app/test_palette.py
    DESTINATION
        Mod/Freeform/FreeformTest/app
)
