# SPDX-License-Identifier: LGPL-2.1-or-later

SET(Collab_Root_SRCS
    Init.py
    README.md
)

FILE(GLOB Collab_Lib_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} collab/*.py)
FILE(GLOB Collab_Vcs_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} collab/vcs/*.py)
FILE(GLOB Collab_Tests_SRCS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} Tests/*.py)
FILE(GLOB Collab_Docs RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} Resources/doc/*.md)

SET(all_files
    ${Collab_Root_SRCS}
    ${Collab_Lib_SRCS}
    ${Collab_Vcs_SRCS}
    ${Collab_Tests_SRCS}
    ${Collab_Docs}
)

SOURCE_GROUP("" FILES ${Collab_Root_SRCS})
SOURCE_GROUP("collab" FILES ${Collab_Lib_SRCS})
SOURCE_GROUP("Tests" FILES ${Collab_Tests_SRCS})
SOURCE_GROUP("Resources" FILES ${Collab_Docs})

ADD_CUSTOM_TARGET(Collab ALL SOURCES ${all_files})

fc_copy_sources(Collab "${CMAKE_BINARY_DIR}/Mod/Collab" ${all_files})

INSTALL(FILES ${Collab_Root_SRCS} DESTINATION Mod/Collab)
INSTALL(FILES ${Collab_Lib_SRCS} DESTINATION Mod/Collab/collab)
INSTALL(FILES ${Collab_Vcs_SRCS} DESTINATION Mod/Collab/collab/vcs)
INSTALL(FILES ${Collab_Tests_SRCS} DESTINATION Mod/Collab/Tests)
INSTALL(FILES ${Collab_Docs} DESTINATION Mod/Collab/Resources/doc)
