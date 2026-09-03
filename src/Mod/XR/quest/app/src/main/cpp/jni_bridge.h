// SPDX-License-Identifier: LGPL-2.1-or-later
//
// The narrow surface between the native render thread and the Java side.
//
// Native -> Java calls go through a cached global reference to MainActivity
// and are always fire-and-forget: the answer comes back later through one of
// the JNI entry points in jni_bridge.cpp, which only ever enqueues a
// PendingAction for the render thread to pick up.
#pragma once

#include <jni.h>

#include <cstdint>
#include <string>

namespace fcxr {

// Stored by JNI_OnLoad / MainActivity.nativeOnCreate.
void jniSetVm(JavaVM* vm);
JavaVM* jniVm();
// Attaches the calling thread to the JVM (idempotent) and returns its JNIEnv.
JNIEnv* jniEnv();
void jniDetachThread();

// Global reference to the MainActivity instance, plus its class.
void jniSetActivity(jobject activity);
jobject jniActivity();

// Copies a Java string; returns "" for null.
std::string jniToString(JNIEnv* env, jstring value);

// Native -> Java. All are no-ops when Java is not ready yet.
void jniToast(const std::string& message);
void jniOpenFilePicker();
void jniDriveSignIn();
void jniDriveListFiles();
void jniDriveDownload(const std::string& name);
void jniDriveUpload(const std::string& name, const uint8_t* data, size_t size);

}  // namespace fcxr
