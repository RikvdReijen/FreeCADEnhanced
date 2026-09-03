// SPDX-License-Identifier: LGPL-2.1-or-later
#include "jni_bridge.h"

#include <vector>

#include "app.h"
#include "log.h"

namespace fcxr {
namespace {

JavaVM* gVm = nullptr;
jobject gActivity = nullptr;
jclass gActivityClass = nullptr;

// One JNIEnv per thread; the render thread attaches once and stays attached
// until it exits.
thread_local JNIEnv* tEnv = nullptr;
thread_local bool tAttached = false;

jmethodID activityMethod(JNIEnv* env, const char* name, const char* signature) {
    if (!env || !gActivityClass) return nullptr;
    jmethodID method = env->GetMethodID(gActivityClass, name, signature);
    if (!method) {
        env->ExceptionClear();
        LOGW("MainActivity.%s%s not found", name, signature);
    }
    return method;
}

}  // namespace

std::string jniToString(JNIEnv* env, jstring value) {
    if (!env || !value) return std::string();
    const char* chars = env->GetStringUTFChars(value, nullptr);
    std::string out = chars ? chars : "";
    if (chars) env->ReleaseStringUTFChars(value, chars);
    return out;
}

void jniSetVm(JavaVM* vm) { gVm = vm; }
JavaVM* jniVm() { return gVm; }

JNIEnv* jniEnv() {
    if (tEnv) return tEnv;
    if (!gVm) return nullptr;
    void* existing = nullptr;
    if (gVm->GetEnv(&existing, JNI_VERSION_1_6) == JNI_OK) {
        tEnv = static_cast<JNIEnv*>(existing);
        return tEnv;
    }
    // A writable buffer: the NDK declares `name` as const char*, the desktop
    // JDK as char*, and this satisfies both.
    char threadName[] = "FreeCADXR";
    JavaVMAttachArgs args;
    args.version = JNI_VERSION_1_6;
    args.name = threadName;
    args.group = nullptr;
    // Android's JNIEnv wrapper declares AttachCurrentThread(JNIEnv**, void*)
    // while the desktop JDK declares (void**, void*). The ifdef keeps this
    // file compilable on the host for the offline syntax checks.
#if defined(__ANDROID__)
    if (gVm->AttachCurrentThread(&tEnv, &args) != JNI_OK) {
#else
    void* attached = nullptr;
    if (gVm->AttachCurrentThread(&attached, &args) != JNI_OK) {
#endif
        LOGE("AttachCurrentThread failed");
        tEnv = nullptr;
        return nullptr;
    }
#if !defined(__ANDROID__)
    tEnv = static_cast<JNIEnv*>(attached);
#endif
    tAttached = true;
    return tEnv;
}

void jniDetachThread() {
    if (tAttached && gVm) gVm->DetachCurrentThread();
    tAttached = false;
    tEnv = nullptr;
}

void jniSetActivity(jobject activity) {
    JNIEnv* env = jniEnv();
    if (!env) return;
    if (gActivity) {
        env->DeleteGlobalRef(gActivity);
        gActivity = nullptr;
    }
    if (gActivityClass) {
        env->DeleteGlobalRef(gActivityClass);
        gActivityClass = nullptr;
    }
    if (!activity) return;
    gActivity = env->NewGlobalRef(activity);
    jclass local = env->GetObjectClass(activity);
    gActivityClass = static_cast<jclass>(env->NewGlobalRef(local));
    env->DeleteLocalRef(local);
}

jobject jniActivity() { return gActivity; }

// -------------------------------------------------------- native -> Java

void jniToast(const std::string& message) {
    JNIEnv* env = jniEnv();
    if (!env || !gActivity) return;
    jmethodID method = activityMethod(env, "showToast", "(Ljava/lang/String;)V");
    if (!method) return;
    jstring text = env->NewStringUTF(message.c_str());
    env->CallVoidMethod(gActivity, method, text);
    env->DeleteLocalRef(text);
}

void jniOpenFilePicker() {
    JNIEnv* env = jniEnv();
    if (!env || !gActivity) return;
    jmethodID method = activityMethod(env, "openFilePicker", "()V");
    if (method) env->CallVoidMethod(gActivity, method);
}

void jniDriveSignIn() {
    JNIEnv* env = jniEnv();
    if (!env || !gActivity) return;
    jmethodID method = activityMethod(env, "driveSignIn", "()V");
    if (method) env->CallVoidMethod(gActivity, method);
}

void jniDriveListFiles() {
    JNIEnv* env = jniEnv();
    if (!env || !gActivity) return;
    jmethodID method = activityMethod(env, "driveListFiles", "()V");
    if (method) env->CallVoidMethod(gActivity, method);
}

void jniDriveDownload(const std::string& name) {
    JNIEnv* env = jniEnv();
    if (!env || !gActivity) return;
    jmethodID method = activityMethod(env, "driveDownload", "(Ljava/lang/String;)V");
    if (!method) return;
    jstring text = env->NewStringUTF(name.c_str());
    env->CallVoidMethod(gActivity, method, text);
    env->DeleteLocalRef(text);
}

void jniDriveUpload(const std::string& name, const uint8_t* data, size_t size) {
    JNIEnv* env = jniEnv();
    if (!env || !gActivity || !data || !size) return;
    jmethodID method = activityMethod(env, "driveUpload", "(Ljava/lang/String;[B)V");
    if (!method) return;
    jstring text = env->NewStringUTF(name.c_str());
    jbyteArray bytes = env->NewByteArray(jsize(size));
    env->SetByteArrayRegion(bytes, 0, jsize(size),
                            reinterpret_cast<const jbyte*>(data));
    env->CallVoidMethod(gActivity, method, text, bytes);
    env->DeleteLocalRef(bytes);
    env->DeleteLocalRef(text);
}

}  // namespace fcxr

// -------------------------------------------------------- Java -> native
//
// Every entry point here is called on the Android main thread and must not
// touch GL or OpenXR: it only enqueues work for the render thread.

extern "C" {

JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, void*) {
    fcxr::jniSetVm(vm);
    return JNI_VERSION_1_6;
}

JNIEXPORT void JNICALL Java_org_freecad_xr_JniBridge_nativeFileImported(
    JNIEnv* env, jclass, jstring name, jbyteArray data) {
    fcxr::App* app = fcxr::appInstance();
    if (!app || !data) return;
    fcxr::PendingAction action;
    action.kind = fcxr::PendingAction::Kind::LoadFile;
    action.text = fcxr::jniToString(env, name);
    const jsize size = env->GetArrayLength(data);
    action.data.resize(size_t(size));
    if (size) env->GetByteArrayRegion(data, 0, size,
                                      reinterpret_cast<jbyte*>(action.data.data()));
    app->postAction(std::move(action));
}

JNIEXPORT void JNICALL Java_org_freecad_xr_JniBridge_nativeDriveFiles(JNIEnv* env, jclass,
                                                                     jstring json) {
    fcxr::App* app = fcxr::appInstance();
    if (!app) return;
    fcxr::PendingAction action;
    action.kind = fcxr::PendingAction::Kind::DriveFiles;
    action.text = fcxr::jniToString(env, json);
    app->postAction(std::move(action));
}

JNIEXPORT void JNICALL Java_org_freecad_xr_JniBridge_nativeDriveAuthState(
    JNIEnv* env, jclass, jstring status, jstring userCode) {
    fcxr::App* app = fcxr::appInstance();
    if (!app) return;
    fcxr::PendingAction action;
    action.kind = fcxr::PendingAction::Kind::DriveAuth;
    action.text = fcxr::jniToString(env, status);
    action.secondary = fcxr::jniToString(env, userCode);
    app->postAction(std::move(action));
}

JNIEXPORT void JNICALL Java_org_freecad_xr_JniBridge_nativeDriveDownloaded(
    JNIEnv* env, jclass, jstring name, jbyteArray data) {
    fcxr::App* app = fcxr::appInstance();
    if (!app || !data) return;
    fcxr::PendingAction action;
    action.kind = fcxr::PendingAction::Kind::DriveDownloaded;
    action.text = fcxr::jniToString(env, name);
    const jsize size = env->GetArrayLength(data);
    action.data.resize(size_t(size));
    if (size) env->GetByteArrayRegion(data, 0, size,
                                      reinterpret_cast<jbyte*>(action.data.data()));
    app->postAction(std::move(action));
}

JNIEXPORT void JNICALL Java_org_freecad_xr_JniBridge_nativeToast(JNIEnv* env, jclass,
                                                                jstring message) {
    fcxr::App* app = fcxr::appInstance();
    if (!app) return;
    fcxr::PendingAction action;
    action.kind = fcxr::PendingAction::Kind::Toast;
    action.text = fcxr::jniToString(env, message);
    app->postAction(std::move(action));
}

}  // extern "C"
