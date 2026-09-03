// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Logging shim. Everything logs under the "FreeCADXR" tag so `adb logcat -s
// FreeCADXR` shows the whole app.
#pragma once

#include <android/log.h>

#define FCXR_LOG_TAG "FreeCADXR"
#define LOGV(...) __android_log_print(ANDROID_LOG_VERBOSE, FCXR_LOG_TAG, __VA_ARGS__)
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, FCXR_LOG_TAG, __VA_ARGS__)
#define LOGW(...) __android_log_print(ANDROID_LOG_WARN, FCXR_LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, FCXR_LOG_TAG, __VA_ARGS__)
