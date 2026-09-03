# SPDX-License-Identifier: LGPL-2.1-or-later
#
# The whole app is one shared library, libfreecadxr.so, loaded by MainActivity.
#
# Dependencies, deliberately few (ARCHITECTURE.md §5):
#   * the OpenXR loader, from either the Meta OpenXR Mobile SDK
#     (-DUSE_META_XR_SDK=ON -DMETA_XR_SDK_DIR=...) or the Khronos
#     openxr_loader_for_android AAR that Gradle resolves through prefab
#   * GLESv3 and EGL from the NDK
#   * zlib from the NDK, for PNG deflate/inflate
# There is no other third party code: the JSON parser, PNG codec, HTTP client
# and font all live in this directory.
cmake_minimum_required(VERSION 3.22.1)
project(freecadxr LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

option(USE_META_XR_SDK "Take the OpenXR loader from the Meta OpenXR Mobile SDK" OFF)
set(META_XR_SDK_DIR "" CACHE STRING "Path to the unpacked Meta OpenXR Mobile SDK")

add_library(freecadxr SHARED
    main.cpp
    app.cpp
    app_ui.cpp
    jni_bridge.cpp
    assets.cpp
    egl_context.cpp
    xr_session.cpp
    input.cpp
    renderer.cpp
    gl_util.cpp
    mesh_data.cpp
    tessellate.cpp
    text_font.cpp
    env_spec.cpp
    environment.cpp
    document.cpp
    paint.cpp
    vector_edit.cpp
    ui.cpp
    fcxr.cpp
    json.cpp
    png.cpp
    math3d.cpp
    storage.cpp
    sync_client.cpp
)

target_include_directories(freecadxr PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

# ---------------------------------------------------------------- OpenXR
if(USE_META_XR_SDK AND NOT META_XR_SDK_DIR STREQUAL "")
    set(OPENXR_INCLUDE "${META_XR_SDK_DIR}/OpenXR/Include")
    set(OPENXR_LIB
        "${META_XR_SDK_DIR}/OpenXR/Libs/Android/${ANDROID_ABI}/Release/libopenxr_loader.so")
    if(NOT EXISTS "${OPENXR_INCLUDE}/openxr/openxr.h")
        message(FATAL_ERROR
            "OpenXR headers not found under ${OPENXR_INCLUDE}. "
            "Check metaXrSdkDir in local.properties.")
    endif()
    if(NOT EXISTS "${OPENXR_LIB}")
        message(FATAL_ERROR "libopenxr_loader.so not found at ${OPENXR_LIB}")
    endif()
    add_library(openxr_loader SHARED IMPORTED)
    set_target_properties(openxr_loader PROPERTIES IMPORTED_LOCATION "${OPENXR_LIB}")
    target_include_directories(freecadxr PRIVATE "${OPENXR_INCLUDE}")
    target_link_libraries(freecadxr PRIVATE openxr_loader)
    message(STATUS "OpenXR loader: Meta SDK at ${META_XR_SDK_DIR}")
else()
    # Provided by the openxr_loader_for_android AAR through prefab.
    find_package(openxr_loader REQUIRED CONFIG)
    target_link_libraries(freecadxr PRIVATE OpenXR::openxr_loader)
    message(STATUS "OpenXR loader: openxr_loader_for_android AAR (prefab)")
endif()

# ----------------------------------------------------------------- system
find_library(ANDROID_LIB android)
find_library(LOG_LIB log)
find_library(EGL_LIB EGL)
find_library(GLES_LIB GLESv3)
find_library(Z_LIB z)

target_link_libraries(freecadxr PRIVATE
    ${ANDROID_LIB}
    ${LOG_LIB}
    ${EGL_LIB}
    ${GLES_LIB}
    ${Z_LIB}
)

target_compile_definitions(freecadxr PRIVATE
    XR_USE_PLATFORM_ANDROID
    XR_USE_GRAPHICS_API_OPENGL_ES
)

# Release builds get the usual size/speed treatment; the app is CPU bound on
# tessellation and paint compositing, both of which vectorise well.
target_compile_options(freecadxr PRIVATE
    $<$<CONFIG:Release>:-O3>
    $<$<CONFIG:Release>:-ffast-math>
    -fvisibility=hidden
)
