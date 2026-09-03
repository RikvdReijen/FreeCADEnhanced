// SPDX-License-Identifier: LGPL-2.1-or-later
#include "xr_session.h"

#include <cstring>

#include "log.h"

namespace fcxr {
namespace {

constexpr XrViewConfigurationType kViewConfig = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;

XrPosef identityPose() {
    XrPosef p{};
    p.orientation.w = 1.0f;
    return p;
}

bool extensionPresent(const std::vector<XrExtensionProperties>& list, const char* name) {
    for (const XrExtensionProperties& e : list) {
        if (!std::strcmp(e.extensionName, name)) return true;
    }
    return false;
}

}  // namespace

Vec3 xrToVec3(const XrVector3f& v) { return Vec3(v.x, v.y, v.z); }
Quat xrToQuat(const XrQuaternionf& q) { return Quat(q.x, q.y, q.z, q.w); }

Mat4 xrPoseToMatrix(const XrPosef& pose) {
    return mat4TRS(xrToVec3(pose.position), xrToQuat(pose.orientation), Vec3(1, 1, 1));
}

std::string xrResultString(XrInstance instance, XrResult result) {
    char buffer[XR_MAX_RESULT_STRING_SIZE] = {0};
    if (instance != XR_NULL_HANDLE && XR_SUCCEEDED(xrResultToString(instance, result, buffer)))
        return buffer;
    return std::to_string(int(result));
}

bool xrCheck(XrInstance instance, XrResult result, const char* what) {
    if (XR_SUCCEEDED(result)) return true;
    LOGE("%s failed: %s", what, xrResultString(instance, result).c_str());
    return false;
}

// --------------------------------------------------------------- instance

bool XrSessionManager::createInstance(JavaVM* vm, jobject activity) {
    // The Android loader has to be initialised before anything else so it can
    // find the runtime's broker service.
    PFN_xrInitializeLoaderKHR initializeLoader = nullptr;
    if (XR_SUCCEEDED(xrGetInstanceProcAddr(XR_NULL_HANDLE, "xrInitializeLoaderKHR",
                                           reinterpret_cast<PFN_xrVoidFunction*>(
                                               &initializeLoader))) &&
        initializeLoader) {
        XrLoaderInitInfoAndroidKHR loaderInfo{XR_TYPE_LOADER_INIT_INFO_ANDROID_KHR};
        loaderInfo.applicationVM = vm;
        loaderInfo.applicationContext = activity;
        initializeLoader(reinterpret_cast<const XrLoaderInitInfoBaseHeaderKHR*>(&loaderInfo));
    } else {
        LOGW("xrInitializeLoaderKHR unavailable; continuing without loader init");
    }

    uint32_t extensionCount = 0;
    if (!xrCheck(XR_NULL_HANDLE,
                 xrEnumerateInstanceExtensionProperties(nullptr, 0, &extensionCount, nullptr),
                 "xrEnumerateInstanceExtensionProperties"))
        return false;
    std::vector<XrExtensionProperties> available(extensionCount,
                                                 {XR_TYPE_EXTENSION_PROPERTIES});
    if (extensionCount &&
        !xrCheck(XR_NULL_HANDLE,
                 xrEnumerateInstanceExtensionProperties(nullptr, extensionCount,
                                                        &extensionCount, available.data()),
                 "xrEnumerateInstanceExtensionProperties"))
        return false;

    std::vector<const char*> enabled;
    if (!extensionPresent(available, XR_KHR_OPENGL_ES_ENABLE_EXTENSION_NAME)) {
        LOGE("runtime does not support " XR_KHR_OPENGL_ES_ENABLE_EXTENSION_NAME);
        return false;
    }
    enabled.push_back(XR_KHR_OPENGL_ES_ENABLE_EXTENSION_NAME);

    auto enableIfPresent = [&](const char* name) {
        if (extensionPresent(available, name)) {
            enabled.push_back(name);
            return true;
        }
        LOGI("optional extension %s not available", name);
        return false;
    };
    const bool haveAndroidCreate =
        enableIfPresent(XR_KHR_ANDROID_CREATE_INSTANCE_EXTENSION_NAME);
    const bool haveHandTracking = enableIfPresent(XR_EXT_HAND_TRACKING_EXTENSION_NAME);
    const bool havePassthrough = enableIfPresent(XR_FB_PASSTHROUGH_EXTENSION_NAME);
    const bool haveRefreshRate = enableIfPresent(XR_FB_DISPLAY_REFRESH_RATE_EXTENSION_NAME);
    const bool haveColorSpace = enableIfPresent(XR_FB_COLOR_SPACE_EXTENSION_NAME);

    XrInstanceCreateInfoAndroidKHR androidInfo{XR_TYPE_INSTANCE_CREATE_INFO_ANDROID_KHR};
    androidInfo.applicationVM = vm;
    androidInfo.applicationActivity = activity;

    XrInstanceCreateInfo createInfo{XR_TYPE_INSTANCE_CREATE_INFO};
    createInfo.next = haveAndroidCreate ? &androidInfo : nullptr;
    std::strncpy(createInfo.applicationInfo.applicationName, "FreeCAD XR",
                 XR_MAX_APPLICATION_NAME_SIZE - 1);
    createInfo.applicationInfo.applicationVersion = 1;
    std::strncpy(createInfo.applicationInfo.engineName, "FreeCAD XR quest",
                 XR_MAX_ENGINE_NAME_SIZE - 1);
    createInfo.applicationInfo.engineVersion = 1;
    createInfo.applicationInfo.apiVersion = XR_CURRENT_API_VERSION;
    createInfo.enabledExtensionCount = uint32_t(enabled.size());
    createInfo.enabledExtensionNames = enabled.data();

    if (!xrCheck(XR_NULL_HANDLE, xrCreateInstance(&createInfo, &instance_), "xrCreateInstance"))
        return false;

    XrInstanceProperties props{XR_TYPE_INSTANCE_PROPERTIES};
    if (XR_SUCCEEDED(xrGetInstanceProperties(instance_, &props)))
        LOGI("OpenXR runtime: %s %d.%d.%d", props.runtimeName,
             XR_VERSION_MAJOR(props.runtimeVersion), XR_VERSION_MINOR(props.runtimeVersion),
             XR_VERSION_PATCH(props.runtimeVersion));

    if (!selectSystem()) return false;
    handTrackingAvailable_ = haveHandTracking && handTrackingAvailable_;
    passthroughSupported_ = havePassthrough && passthroughSupported_;

    if (havePassthrough) {
        xrGetInstanceProcAddr(instance_, "xrCreatePassthroughFB",
                              reinterpret_cast<PFN_xrVoidFunction*>(&xrCreatePassthroughFB_));
        xrGetInstanceProcAddr(instance_, "xrDestroyPassthroughFB",
                              reinterpret_cast<PFN_xrVoidFunction*>(&xrDestroyPassthroughFB_));
        xrGetInstanceProcAddr(instance_, "xrPassthroughStartFB",
                              reinterpret_cast<PFN_xrVoidFunction*>(&xrPassthroughStartFB_));
        xrGetInstanceProcAddr(instance_, "xrPassthroughPauseFB",
                              reinterpret_cast<PFN_xrVoidFunction*>(&xrPassthroughPauseFB_));
        xrGetInstanceProcAddr(
            instance_, "xrCreatePassthroughLayerFB",
            reinterpret_cast<PFN_xrVoidFunction*>(&xrCreatePassthroughLayerFB_));
        xrGetInstanceProcAddr(
            instance_, "xrDestroyPassthroughLayerFB",
            reinterpret_cast<PFN_xrVoidFunction*>(&xrDestroyPassthroughLayerFB_));
        xrGetInstanceProcAddr(
            instance_, "xrPassthroughLayerResumeFB",
            reinterpret_cast<PFN_xrVoidFunction*>(&xrPassthroughLayerResumeFB_));
        xrGetInstanceProcAddr(
            instance_, "xrPassthroughLayerPauseFB",
            reinterpret_cast<PFN_xrVoidFunction*>(&xrPassthroughLayerPauseFB_));
        if (!xrCreatePassthroughFB_ || !xrCreatePassthroughLayerFB_) {
            LOGW("passthrough entry points missing; disabling passthrough");
            passthroughSupported_ = false;
        }
    }
    // Remembered for createSession().
    haveRefreshRateExt_ = haveRefreshRate;
    haveColorSpaceExt_ = haveColorSpace;
    return true;
}

bool XrSessionManager::selectSystem() {
    XrSystemGetInfo systemInfo{XR_TYPE_SYSTEM_GET_INFO};
    systemInfo.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
    if (!xrCheck(instance_, xrGetSystem(instance_, &systemInfo, &systemId_), "xrGetSystem"))
        return false;

    XrSystemHandTrackingPropertiesEXT handProps{XR_TYPE_SYSTEM_HAND_TRACKING_PROPERTIES_EXT};
    XrSystemPassthroughProperties2FB passthroughProps{
        XR_TYPE_SYSTEM_PASSTHROUGH_PROPERTIES2_FB};
    passthroughProps.next = &handProps;
    XrSystemProperties systemProps{XR_TYPE_SYSTEM_PROPERTIES};
    systemProps.next = &passthroughProps;
    if (XR_SUCCEEDED(xrGetSystemProperties(instance_, systemId_, &systemProps))) {
        LOGI("system: %s (max layers %u, %ux%u per eye)", systemProps.systemName,
             systemProps.graphicsProperties.maxLayerCount,
             systemProps.graphicsProperties.maxSwapchainImageWidth,
             systemProps.graphicsProperties.maxSwapchainImageHeight);
        handTrackingAvailable_ = handProps.supportsHandTracking == XR_TRUE;
        passthroughSupported_ =
            (passthroughProps.capabilities & XR_PASSTHROUGH_CAPABILITY_BIT_FB) != 0;
    }
    return true;
}

// ---------------------------------------------------------------- session

bool XrSessionManager::createSession(EglContext& egl) {
    PFN_xrGetOpenGLESGraphicsRequirementsKHR getRequirements = nullptr;
    if (XR_FAILED(xrGetInstanceProcAddr(
            instance_, "xrGetOpenGLESGraphicsRequirementsKHR",
            reinterpret_cast<PFN_xrVoidFunction*>(&getRequirements))) ||
        !getRequirements) {
        LOGE("xrGetOpenGLESGraphicsRequirementsKHR is missing");
        return false;
    }
    XrGraphicsRequirementsOpenGLESKHR requirements{
        XR_TYPE_GRAPHICS_REQUIREMENTS_OPENGL_ES_KHR};
    // Calling this is mandatory even though we do not gate on the result.
    if (!xrCheck(instance_, getRequirements(instance_, systemId_, &requirements),
                 "xrGetOpenGLESGraphicsRequirements"))
        return false;
    LOGI("required GLES %d.%d .. %d.%d",
         XR_VERSION_MAJOR(requirements.minApiVersionSupported),
         XR_VERSION_MINOR(requirements.minApiVersionSupported),
         XR_VERSION_MAJOR(requirements.maxApiVersionSupported),
         XR_VERSION_MINOR(requirements.maxApiVersionSupported));

    XrGraphicsBindingOpenGLESAndroidKHR binding{
        XR_TYPE_GRAPHICS_BINDING_OPENGL_ES_ANDROID_KHR};
    binding.display = egl.display();
    binding.config = egl.config();
    binding.context = egl.context();

    XrSessionCreateInfo createInfo{XR_TYPE_SESSION_CREATE_INFO};
    createInfo.next = &binding;
    createInfo.systemId = systemId_;
    if (!xrCheck(instance_, xrCreateSession(instance_, &createInfo, &session_),
                 "xrCreateSession"))
        return false;

    // Reference spaces: STAGE gives a floor-level origin that matches the
    // guardian, which is what the environments assume (y = 0 is the floor).
    uint32_t spaceCount = 0;
    xrEnumerateReferenceSpaces(session_, 0, &spaceCount, nullptr);
    std::vector<XrReferenceSpaceType> spaces(spaceCount);
    if (spaceCount)
        xrEnumerateReferenceSpaces(session_, spaceCount, &spaceCount, spaces.data());
    usingStageSpace_ = false;
    for (XrReferenceSpaceType t : spaces) {
        if (t == XR_REFERENCE_SPACE_TYPE_STAGE) usingStageSpace_ = true;
    }

    XrReferenceSpaceCreateInfo spaceInfo{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
    spaceInfo.poseInReferenceSpace = identityPose();
    spaceInfo.referenceSpaceType =
        usingStageSpace_ ? XR_REFERENCE_SPACE_TYPE_STAGE : XR_REFERENCE_SPACE_TYPE_LOCAL;
    if (!xrCheck(instance_, xrCreateReferenceSpace(session_, &spaceInfo, &appSpace_),
                 "xrCreateReferenceSpace(app)"))
        return false;
    LOGI("app space: %s", usingStageSpace_ ? "STAGE" : "LOCAL");

    spaceInfo.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_VIEW;
    xrCheck(instance_, xrCreateReferenceSpace(session_, &spaceInfo, &viewSpace_),
            "xrCreateReferenceSpace(view)");

    if (!createSwapchains()) return false;

    if (haveColorSpaceExt_) {
        PFN_xrSetColorSpaceFB setColorSpace = nullptr;
        xrGetInstanceProcAddr(instance_, "xrSetColorSpaceFB",
                              reinterpret_cast<PFN_xrVoidFunction*>(&setColorSpace));
        // Rec.709 matches the sRGB primaries our textures and materials assume.
        if (setColorSpace) setColorSpace(session_, XR_COLOR_SPACE_REC709_FB);
    }
    if (haveRefreshRateExt_) {
        PFN_xrEnumerateDisplayRefreshRatesFB enumerateRates = nullptr;
        PFN_xrRequestDisplayRefreshRateFB requestRate = nullptr;
        xrGetInstanceProcAddr(instance_, "xrEnumerateDisplayRefreshRatesFB",
                              reinterpret_cast<PFN_xrVoidFunction*>(&enumerateRates));
        xrGetInstanceProcAddr(instance_, "xrRequestDisplayRefreshRateFB",
                              reinterpret_cast<PFN_xrVoidFunction*>(&requestRate));
        if (enumerateRates && requestRate) {
            uint32_t count = 0;
            enumerateRates(session_, 0, &count, nullptr);
            std::vector<float> rates(count);
            if (count) enumerateRates(session_, count, &count, rates.data());
            // Prefer 90 Hz: it is the highest rate the renderer's budget is
            // designed for. Fall back to the fastest offered below that.
            float best = 0.0f;
            for (float r : rates) {
                if (r <= 90.5f && r > best) best = r;
            }
            if (best > 0.0f && XR_SUCCEEDED(requestRate(session_, best))) {
                displayRefreshRate_ = best;
                LOGI("display refresh rate set to %.1f Hz", best);
            }
        }
    }

    if (passthroughSupported_) {
        XrPassthroughCreateInfoFB passthroughInfo{XR_TYPE_PASSTHROUGH_CREATE_INFO_FB};
        if (XR_SUCCEEDED(xrCreatePassthroughFB_(session_, &passthroughInfo, &passthrough_))) {
            XrPassthroughLayerCreateInfoFB layerInfo{XR_TYPE_PASSTHROUGH_LAYER_CREATE_INFO_FB};
            layerInfo.passthrough = passthrough_;
            layerInfo.purpose = XR_PASSTHROUGH_LAYER_PURPOSE_RECONSTRUCTION_FB;
            if (XR_FAILED(xrCreatePassthroughLayerFB_(session_, &layerInfo,
                                                      &passthroughLayer_))) {
                LOGW("xrCreatePassthroughLayerFB failed; passthrough disabled");
                passthroughSupported_ = false;
            }
        } else {
            LOGW("xrCreatePassthroughFB failed; passthrough disabled");
            passthroughSupported_ = false;
        }
    }
    return true;
}

bool XrSessionManager::createSwapchains() {
    uint32_t viewCount = 0;
    if (!xrCheck(instance_,
                 xrEnumerateViewConfigurationViews(instance_, systemId_, kViewConfig, 0,
                                                   &viewCount, nullptr),
                 "xrEnumerateViewConfigurationViews"))
        return false;
    if (viewCount != 2) {
        LOGE("expected a stereo view configuration, got %u views", viewCount);
        return false;
    }
    std::vector<XrViewConfigurationView> configViews(viewCount,
                                                     {XR_TYPE_VIEW_CONFIGURATION_VIEW});
    xrEnumerateViewConfigurationViews(instance_, systemId_, kViewConfig, viewCount, &viewCount,
                                      configViews.data());
    eyeWidth_ = int(configViews[0].recommendedImageRectWidth);
    eyeHeight_ = int(configViews[0].recommendedImageRectHeight);
    views_.assign(viewCount, {XR_TYPE_VIEW});

    uint32_t formatCount = 0;
    xrEnumerateSwapchainFormats(session_, 0, &formatCount, nullptr);
    std::vector<int64_t> formats(formatCount);
    if (formatCount)
        xrEnumerateSwapchainFormats(session_, formatCount, &formatCount, formats.data());
    // An sRGB swapchain lets the hardware encode our linear output for free.
    swapchainFormat_ = formats.empty() ? int64_t(GL_SRGB8_ALPHA8) : formats[0];
    for (int64_t f : formats) {
        if (f == int64_t(GL_SRGB8_ALPHA8)) {
            swapchainFormat_ = f;
            break;
        }
    }

    for (int eye = 0; eye < 2; ++eye) {
        XrSwapchainCreateInfo info{XR_TYPE_SWAPCHAIN_CREATE_INFO};
        info.usageFlags =
            XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT | XR_SWAPCHAIN_USAGE_SAMPLED_BIT;
        info.format = swapchainFormat_;
        // Multisampling is done by us into the swapchain texture, so the
        // swapchain itself is single sampled.
        info.sampleCount = 1;
        info.width = uint32_t(eyeWidth_);
        info.height = uint32_t(eyeHeight_);
        info.faceCount = 1;
        info.arraySize = 1;
        info.mipCount = 1;
        if (!xrCheck(instance_, xrCreateSwapchain(session_, &info, &swapchains_[eye].handle),
                     "xrCreateSwapchain"))
            return false;

        uint32_t imageCount = 0;
        xrEnumerateSwapchainImages(swapchains_[eye].handle, 0, &imageCount, nullptr);
        swapchains_[eye].images.assign(imageCount, {XR_TYPE_SWAPCHAIN_IMAGE_OPENGL_ES_KHR});
        xrEnumerateSwapchainImages(
            swapchains_[eye].handle, imageCount, &imageCount,
            reinterpret_cast<XrSwapchainImageBaseHeader*>(swapchains_[eye].images.data()));
    }
    LOGI("swapchains: 2 x %dx%d, format 0x%llx", eyeWidth_, eyeHeight_,
         static_cast<unsigned long long>(swapchainFormat_));
    return true;
}

void XrSessionManager::destroySwapchains() {
    for (Swapchain& s : swapchains_) {
        if (s.handle != XR_NULL_HANDLE) xrDestroySwapchain(s.handle);
        s.handle = XR_NULL_HANDLE;
        s.images.clear();
    }
}

void XrSessionManager::destroy() {
    if (passthroughLayer_ != XR_NULL_HANDLE && xrDestroyPassthroughLayerFB_)
        xrDestroyPassthroughLayerFB_(passthroughLayer_);
    if (passthrough_ != XR_NULL_HANDLE && xrDestroyPassthroughFB_)
        xrDestroyPassthroughFB_(passthrough_);
    passthroughLayer_ = XR_NULL_HANDLE;
    passthrough_ = XR_NULL_HANDLE;

    destroySwapchains();
    if (viewSpace_ != XR_NULL_HANDLE) xrDestroySpace(viewSpace_);
    if (appSpace_ != XR_NULL_HANDLE) xrDestroySpace(appSpace_);
    if (session_ != XR_NULL_HANDLE) xrDestroySession(session_);
    if (instance_ != XR_NULL_HANDLE) xrDestroyInstance(instance_);
    viewSpace_ = appSpace_ = XR_NULL_HANDLE;
    session_ = XR_NULL_HANDLE;
    instance_ = XR_NULL_HANDLE;
    sessionRunning_ = false;
    sessionState_ = XR_SESSION_STATE_UNKNOWN;
}

// ----------------------------------------------------------------- events

void XrSessionManager::handleStateChange(const XrEventDataSessionStateChanged& event) {
    sessionState_ = event.state;
    LOGI("session state -> %d", int(sessionState_));
    switch (sessionState_) {
        case XR_SESSION_STATE_READY: {
            XrSessionBeginInfo beginInfo{XR_TYPE_SESSION_BEGIN_INFO};
            beginInfo.primaryViewConfigurationType = kViewConfig;
            if (xrCheck(instance_, xrBeginSession(session_, &beginInfo), "xrBeginSession"))
                sessionRunning_ = true;
            break;
        }
        case XR_SESSION_STATE_STOPPING:
            sessionRunning_ = false;
            xrCheck(instance_, xrEndSession(session_), "xrEndSession");
            break;
        default:
            break;
    }
}

bool XrSessionManager::pollEvents(bool* exitRequested) {
    XrEventDataBuffer event{XR_TYPE_EVENT_DATA_BUFFER};
    for (;;) {
        event = XrEventDataBuffer{XR_TYPE_EVENT_DATA_BUFFER};
        const XrResult result = xrPollEvent(instance_, &event);
        if (result == XR_EVENT_UNAVAILABLE) return true;
        if (XR_FAILED(result)) {
            LOGE("xrPollEvent failed: %s", xrResultString(instance_, result).c_str());
            return false;
        }
        switch (event.type) {
            case XR_TYPE_EVENT_DATA_INSTANCE_LOSS_PENDING:
                LOGW("instance loss pending");
                if (exitRequested) *exitRequested = true;
                return false;
            case XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED: {
                const auto& changed =
                    *reinterpret_cast<const XrEventDataSessionStateChanged*>(&event);
                handleStateChange(changed);
                if (changed.state == XR_SESSION_STATE_EXITING ||
                    changed.state == XR_SESSION_STATE_LOSS_PENDING) {
                    if (exitRequested) *exitRequested = true;
                }
                break;
            }
            case XR_TYPE_EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING:
                LOGI("reference space change pending (guardian recentre)");
                break;
            case XR_TYPE_EVENT_DATA_INTERACTION_PROFILE_CHANGED:
                LOGI("interaction profile changed");
                break;
            default:
                break;
        }
    }
}

void XrSessionManager::recenter() {
    if (usingStageSpace_) {
        // STAGE space is defined by the guardian; the runtime owns recentring.
        LOGI("recentre ignored: running in STAGE space");
        return;
    }
    XrSpaceLocation location{XR_TYPE_SPACE_LOCATION};
    if (XR_FAILED(xrLocateSpace(viewSpace_, appSpace_, frameState_.predictedDisplayTime,
                                &location)))
        return;
    if (!(location.locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT)) return;

    // Keep only the yaw so the horizon stays level.
    const Quat q = xrToQuat(location.pose.orientation);
    const Vec3 forward = rotate(q, Vec3(0, 0, -1));
    const float yaw = std::atan2(forward.x, -forward.z);

    XrReferenceSpaceCreateInfo info{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
    info.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL;
    const Quat yawQuat = quatAxisAngle(Vec3(0, 1, 0), yaw);
    info.poseInReferenceSpace.position = location.pose.position;
    info.poseInReferenceSpace.orientation = {yawQuat.x, yawQuat.y, yawQuat.z, yawQuat.w};

    XrSpace newSpace = XR_NULL_HANDLE;
    if (!xrCheck(instance_, xrCreateReferenceSpace(session_, &info, &newSpace),
                 "xrCreateReferenceSpace(recentre)"))
        return;
    XrSpace old = appSpace_;
    appSpace_ = newSpace;
    if (old != XR_NULL_HANDLE) xrDestroySpace(old);
    LOGI("recentred LOCAL space");
}

// ------------------------------------------------------------------ frame

bool XrSessionManager::beginFrame(FrameInfo* info) {
    if (!info || !sessionRunning_) return false;
    *info = FrameInfo();

    XrFrameWaitInfo waitInfo{XR_TYPE_FRAME_WAIT_INFO};
    frameState_ = XrFrameState{XR_TYPE_FRAME_STATE};
    if (!xrCheck(instance_, xrWaitFrame(session_, &waitInfo, &frameState_), "xrWaitFrame"))
        return false;

    XrFrameBeginInfo beginInfo{XR_TYPE_FRAME_BEGIN_INFO};
    if (!xrCheck(instance_, xrBeginFrame(session_, &beginInfo), "xrBeginFrame")) return false;

    info->predictedDisplayTime = frameState_.predictedDisplayTime;
    info->predictedDisplayPeriod = frameState_.predictedDisplayPeriod;
    info->shouldRender = frameState_.shouldRender == XR_TRUE;
    if (!info->shouldRender) return true;

    XrViewLocateInfo locateInfo{XR_TYPE_VIEW_LOCATE_INFO};
    locateInfo.viewConfigurationType = kViewConfig;
    locateInfo.displayTime = frameState_.predictedDisplayTime;
    locateInfo.space = appSpace_;
    XrViewState viewState{XR_TYPE_VIEW_STATE};
    uint32_t viewCount = 0;
    if (XR_FAILED(xrLocateViews(session_, &locateInfo, &viewState, uint32_t(views_.size()),
                                &viewCount, views_.data()))) {
        info->shouldRender = false;
        return true;
    }
    if (!(viewState.viewStateFlags & XR_VIEW_STATE_POSITION_VALID_BIT) ||
        !(viewState.viewStateFlags & XR_VIEW_STATE_ORIENTATION_VALID_BIT)) {
        // Tracking is not ready yet; still submit an (empty) frame so the
        // runtime keeps its cadence.
        info->shouldRender = false;
        return true;
    }

    info->viewCount = int(viewCount < 2 ? viewCount : 2);
    for (int i = 0; i < info->viewCount; ++i) {
        const XrView& v = views_[size_t(i)];
        EyeView& eye = info->views[i];
        eye.position = xrToVec3(v.pose.position);
        eye.orientation = xrToQuat(v.pose.orientation);
        eye.fov = v.fov;
        eye.view = mat4InverseRigid(xrPoseToMatrix(v.pose));
        eye.projection = projectionFromFov(v.fov.angleLeft, v.fov.angleRight, v.fov.angleUp,
                                           v.fov.angleDown, nearZ_, farZ_);
    }
    return true;
}

bool XrSessionManager::acquireEye(int eye, GLuint* colorTexture, int* width, int* height) {
    if (eye < 0 || eye > 1 || swapchains_[eye].handle == XR_NULL_HANDLE) return false;
    Swapchain& s = swapchains_[eye];
    XrSwapchainImageAcquireInfo acquireInfo{XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};
    if (!xrCheck(instance_, xrAcquireSwapchainImage(s.handle, &acquireInfo, &s.acquiredIndex),
                 "xrAcquireSwapchainImage"))
        return false;
    XrSwapchainImageWaitInfo waitInfo{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};
    waitInfo.timeout = XR_INFINITE_DURATION;
    if (!xrCheck(instance_, xrWaitSwapchainImage(s.handle, &waitInfo),
                 "xrWaitSwapchainImage"))
        return false;
    if (s.acquiredIndex >= s.images.size()) return false;
    if (colorTexture) *colorTexture = s.images[s.acquiredIndex].image;
    if (width) *width = eyeWidth_;
    if (height) *height = eyeHeight_;
    return true;
}

void XrSessionManager::releaseEye(int eye) {
    if (eye < 0 || eye > 1 || swapchains_[eye].handle == XR_NULL_HANDLE) return;
    XrSwapchainImageReleaseInfo releaseInfo{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};
    xrReleaseSwapchainImage(swapchains_[eye].handle, &releaseInfo);
}

void XrSessionManager::setPassthrough(bool enabled) {
    if (!passthroughSupported_ || passthrough_ == XR_NULL_HANDLE) return;
    if (enabled == passthroughEnabled_) return;
    if (enabled) {
        if (xrPassthroughStartFB_) xrPassthroughStartFB_(passthrough_);
        if (xrPassthroughLayerResumeFB_) xrPassthroughLayerResumeFB_(passthroughLayer_);
    } else {
        if (xrPassthroughLayerPauseFB_) xrPassthroughLayerPauseFB_(passthroughLayer_);
        if (xrPassthroughPauseFB_) xrPassthroughPauseFB_(passthrough_);
    }
    passthroughEnabled_ = enabled;
    LOGI("passthrough %s", enabled ? "on" : "off");
}

void XrSessionManager::endFrame(const FrameInfo& info) {
    const XrCompositionLayerBaseHeader* layers[2] = {nullptr, nullptr};
    uint32_t layerCount = 0;

    XrCompositionLayerPassthroughFB passthroughLayerInfo{
        XR_TYPE_COMPOSITION_LAYER_PASSTHROUGH_FB};
    XrCompositionLayerProjection projectionLayer{XR_TYPE_COMPOSITION_LAYER_PROJECTION};

    if (info.shouldRender && info.viewCount > 0) {
        if (passthroughEnabled_ && passthroughLayer_ != XR_NULL_HANDLE) {
            // The passthrough layer goes underneath, and the projection layer
            // composites on top using the alpha we rendered.
            passthroughLayerInfo.layerHandle = passthroughLayer_;
            passthroughLayerInfo.flags = XR_COMPOSITION_LAYER_BLEND_TEXTURE_SOURCE_ALPHA_BIT;
            passthroughLayerInfo.space = XR_NULL_HANDLE;
            layers[layerCount++] =
                reinterpret_cast<const XrCompositionLayerBaseHeader*>(&passthroughLayerInfo);
        }
        for (int i = 0; i < info.viewCount; ++i) {
            projectionViews_[i] = XrCompositionLayerProjectionView{
                XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW};
            projectionViews_[i].pose = views_[size_t(i)].pose;
            projectionViews_[i].fov = views_[size_t(i)].fov;
            projectionViews_[i].subImage.swapchain = swapchains_[i].handle;
            projectionViews_[i].subImage.imageRect.offset = {0, 0};
            projectionViews_[i].subImage.imageRect.extent = {eyeWidth_, eyeHeight_};
            projectionViews_[i].subImage.imageArrayIndex = 0;
        }
        projectionLayer.space = appSpace_;
        projectionLayer.viewCount = uint32_t(info.viewCount);
        projectionLayer.views = projectionViews_;
        projectionLayer.layerFlags =
            passthroughEnabled_ ? XR_COMPOSITION_LAYER_BLEND_TEXTURE_SOURCE_ALPHA_BIT : 0;
        layers[layerCount++] =
            reinterpret_cast<const XrCompositionLayerBaseHeader*>(&projectionLayer);
    }

    XrFrameEndInfo endInfo{XR_TYPE_FRAME_END_INFO};
    endInfo.displayTime = frameState_.predictedDisplayTime;
    endInfo.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
    endInfo.layerCount = layerCount;
    endInfo.layers = layerCount ? layers : nullptr;
    xrCheck(instance_, xrEndFrame(session_, &endInfo), "xrEndFrame");
}

}  // namespace fcxr
