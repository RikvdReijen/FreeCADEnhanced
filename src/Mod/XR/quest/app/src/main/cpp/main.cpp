// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Lifecycle glue and the render thread.
//
// ARCHITECTURE.md §5 asks for a plain Activity rather than a NativeActivity,
// so this file is the app's own small replacement for
// `android_native_app_glue`: MainActivity calls the four entry points below,
// they drive a std::thread that owns EGL, OpenXR and GL for its whole life,
// and a command flag (not a queue) carries the lifecycle across.
//
// OpenXR owns the display, so there is no window, no surface and nothing to
// resize: the thread only needs to know whether the activity is resumed. The
// session state machine inside XrSessionManager does the rest, and
// xrWaitFrame blocks the thread when the runtime is not asking for frames.
#include <android/asset_manager.h>
#include <android/asset_manager_jni.h>
#include <jni.h>

#include <atomic>
#include <chrono>
#include <string>
#include <thread>

#include "app.h"
#include "jni_bridge.h"
#include "log.h"

namespace {

std::thread gRenderThread;
std::atomic<bool> gQuit{false};
std::atomic<bool> gResumed{false};
std::atomic<bool> gRunning{false};

struct StartupPaths {
    std::string filesDir;
    std::string cacheDir;
};

void renderThreadMain(JavaVM* vm, jobject activity, AAssetManager* assetManager,
                      StartupPaths paths) {
    (void)vm;
    fcxr::jniEnv();  // attach this thread for the JNI calls the app makes
    LOGI("render thread starting");

    fcxr::App app;
    if (!app.init(fcxr::jniVm(), activity, assetManager, paths.filesDir, paths.cacheDir)) {
        LOGE("initialisation failed; the render thread is exiting");
        gRunning.store(false);
        fcxr::jniDetachThread();
        return;
    }

    while (!gQuit.load()) {
        if (!gResumed.load()) {
            // Paused: OpenXR will have taken the session out of the running
            // state, so there is nothing to draw. Keep pumping events at a low
            // rate so a resume (or an exit request) is still noticed promptly.
            if (!app.frame()) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            continue;
        }
        if (!app.frame()) break;
    }

    LOGI("render thread stopping");
    app.shutdown();
    gRunning.store(false);
    fcxr::jniDetachThread();
}

}  // namespace

extern "C" {

JNIEXPORT void JNICALL Java_org_freecad_xr_MainActivity_nativeOnCreate(
    JNIEnv* env, jobject activity, jobject assetManager, jstring filesDir,
    jstring cacheDir) {
    if (gRunning.load()) {
        LOGW("nativeOnCreate called twice");
        return;
    }
    JavaVM* vm = nullptr;
    env->GetJavaVM(&vm);
    fcxr::jniSetVm(vm);
    fcxr::jniSetActivity(activity);

    StartupPaths paths;
    paths.filesDir = fcxr::jniToString(env, filesDir);
    paths.cacheDir = fcxr::jniToString(env, cacheDir);

    // The asset manager reference has to outlive this call, so take a global
    // one; AAssetManager_fromJava's pointer is only valid while it lives.
    jobject globalAssets = env->NewGlobalRef(assetManager);
    AAssetManager* assets = AAssetManager_fromJava(env, globalAssets);
    jobject globalActivity = fcxr::jniActivity();

    gQuit.store(false);
    gRunning.store(true);
    gRenderThread = std::thread(renderThreadMain, vm, globalActivity, assets, paths);
}

JNIEXPORT void JNICALL Java_org_freecad_xr_MainActivity_nativeOnResume(JNIEnv*, jobject) {
    LOGI("resumed");
    gResumed.store(true);
}

JNIEXPORT void JNICALL Java_org_freecad_xr_MainActivity_nativeOnPause(JNIEnv*, jobject) {
    LOGI("paused");
    gResumed.store(false);
}

JNIEXPORT void JNICALL Java_org_freecad_xr_MainActivity_nativeOnDestroy(JNIEnv*, jobject) {
    LOGI("destroying");
    gQuit.store(true);
    gResumed.store(false);
    if (gRenderThread.joinable()) gRenderThread.join();
    fcxr::jniSetActivity(nullptr);
}

JNIEXPORT jboolean JNICALL Java_org_freecad_xr_MainActivity_nativeIsRunning(JNIEnv*, jobject) {
    return gRunning.load() ? JNI_TRUE : JNI_FALSE;
}

}  // extern "C"
