// SPDX-License-Identifier: LGPL-2.1-or-later
//
// OpenXR bring-up: instance, system, GLES graphics binding, swapchains,
// reference spaces, the frame loop and the session state machine.
//
// Everything Quest-specific is optional and probed at runtime, so the same
// binary still runs on another OpenXR runtime with the extensions missing:
//   XR_KHR_opengl_es_enable        required
//   XR_KHR_android_create_instance required on Android
//   XR_FB_passthrough              optional, drives the passthrough toggle
//   XR_EXT_hand_tracking           optional, see input.h
//   XR_FB_display_refresh_rate     optional, requests 90 Hz when available
//   XR_FB_color_space              optional, asks for Rec.709
#pragma once

#define XR_USE_PLATFORM_ANDROID
#define XR_USE_GRAPHICS_API_OPENGL_ES

#include <jni.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>

#include <string>
#include <vector>

#include "egl_context.h"
#include "gl_util.h"
#include "math3d.h"

namespace fcxr {

// One eye for one frame.
struct EyeView {
    Mat4 view;         // world -> eye
    Mat4 projection;   // eye -> clip
    Vec3 position{0, 0, 0};
    Quat orientation;
    XrFovf fov{};
};

struct FrameInfo {
    bool shouldRender = false;
    XrTime predictedDisplayTime = 0;
    XrDuration predictedDisplayPeriod = 0;
    int viewCount = 0;
    EyeView views[2];
};

class XrSessionManager {
public:
    // `activity` is a global reference to the MainActivity object; the caller
    // keeps ownership.
    bool createInstance(JavaVM* vm, jobject activity);
    bool createSession(EglContext& egl);
    void destroy();

    // Drains the event queue. Sets `*exitRequested` when the runtime wants the
    // app to quit. Returns false if the instance was lost.
    bool pollEvents(bool* exitRequested);

    bool sessionRunning() const { return sessionRunning_; }
    bool sessionFocused() const { return sessionState_ == XR_SESSION_STATE_FOCUSED; }

    // Blocks until the runtime wants the next frame. Fills `info` and returns
    // true; the caller must always pair this with endFrame().
    bool beginFrame(FrameInfo* info);
    // Acquires the swapchain image for `eye` and returns its GL texture.
    bool acquireEye(int eye, GLuint* colorTexture, int* width, int* height);
    void releaseEye(int eye);
    // Composites the projection layer (plus the passthrough underlay when it
    // is on) and submits the frame.
    void endFrame(const FrameInfo& info);

    // ---- passthrough -----------------------------------------------------
    bool passthroughSupported() const { return passthroughSupported_; }
    bool passthroughEnabled() const { return passthroughEnabled_; }
    void setPassthrough(bool enabled);

    // ---- accessors used by input.cpp / the app ---------------------------
    XrInstance instance() const { return instance_; }
    XrSession session() const { return session_; }
    XrSystemId systemId() const { return systemId_; }
    // The space the app renders in: STAGE when the runtime offers it (so the
    // floor is at y = 0 and matches the guardian), otherwise LOCAL.
    XrSpace appSpace() const { return appSpace_; }
    XrSpace viewSpace() const { return viewSpace_; }
    bool usingStageSpace() const { return usingStageSpace_; }
    bool handTrackingAvailable() const { return handTrackingAvailable_; }
    float displayRefreshRate() const { return displayRefreshRate_; }

    // Clip planes, updated by environment.cpp when the world scale changes.
    void setClipPlanes(float nearZ, float farZ) { nearZ_ = nearZ; farZ_ = farZ; }
    int eyeWidth() const { return eyeWidth_; }
    int eyeHeight() const { return eyeHeight_; }

    // Recentre LOCAL space on the current head pose (the menu offers this when
    // STAGE space is unavailable).
    void recenter();

private:
    struct Swapchain {
        XrSwapchain handle = XR_NULL_HANDLE;
        std::vector<XrSwapchainImageOpenGLESKHR> images;
        uint32_t acquiredIndex = 0;
    };

    bool selectSystem();
    bool createSwapchains();
    void handleStateChange(const XrEventDataSessionStateChanged& event);
    void destroySwapchains();

    XrInstance instance_ = XR_NULL_HANDLE;
    XrSystemId systemId_ = XR_NULL_SYSTEM_ID;
    XrSession session_ = XR_NULL_HANDLE;
    XrSpace appSpace_ = XR_NULL_HANDLE;
    XrSpace viewSpace_ = XR_NULL_HANDLE;
    XrSessionState sessionState_ = XR_SESSION_STATE_UNKNOWN;
    bool sessionRunning_ = false;
    bool usingStageSpace_ = false;

    Swapchain swapchains_[2];
    std::vector<XrView> views_;
    XrFrameState frameState_{};
    XrCompositionLayerProjectionView projectionViews_[2]{};
    int eyeWidth_ = 0, eyeHeight_ = 0;
    int64_t swapchainFormat_ = 0;

    float nearZ_ = 0.02f;
    float farZ_ = 200.0f;
    float displayRefreshRate_ = 0.0f;

    // extensions
    bool passthroughSupported_ = false;
    bool passthroughEnabled_ = false;
    bool handTrackingAvailable_ = false;
    XrPassthroughFB passthrough_ = XR_NULL_HANDLE;
    XrPassthroughLayerFB passthroughLayer_ = XR_NULL_HANDLE;
    PFN_xrCreatePassthroughFB xrCreatePassthroughFB_ = nullptr;
    PFN_xrDestroyPassthroughFB xrDestroyPassthroughFB_ = nullptr;
    PFN_xrPassthroughStartFB xrPassthroughStartFB_ = nullptr;
    PFN_xrPassthroughPauseFB xrPassthroughPauseFB_ = nullptr;
    PFN_xrCreatePassthroughLayerFB xrCreatePassthroughLayerFB_ = nullptr;
    PFN_xrDestroyPassthroughLayerFB xrDestroyPassthroughLayerFB_ = nullptr;
    PFN_xrPassthroughLayerResumeFB xrPassthroughLayerResumeFB_ = nullptr;
    PFN_xrPassthroughLayerPauseFB xrPassthroughLayerPauseFB_ = nullptr;
};

// Converts an OpenXR pose to a matrix and back.
Mat4 xrPoseToMatrix(const XrPosef& pose);
Vec3 xrToVec3(const XrVector3f& v);
Quat xrToQuat(const XrQuaternionf& q);

// Human readable OpenXR result, for logging.
std::string xrResultString(XrInstance instance, XrResult result);
// Logs and returns false when `result` is a failure.
bool xrCheck(XrInstance instance, XrResult result, const char* what);

}  // namespace fcxr
