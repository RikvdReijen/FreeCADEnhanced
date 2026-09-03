// SPDX-License-Identifier: LGPL-2.1-or-later
//
// OpenXR action sets for the Touch Plus controllers, with optional hand
// tracking.
//
// Bindings are declared for `/interaction_profiles/oculus/touch_controller`
// (which Touch Plus reports as) and for `/interaction_profiles/khr/simple_
// controller` so the app still has a trigger and a menu button on any runtime.
//
// When XR_EXT_hand_tracking is available and the controllers are not tracking,
// the same interface is fed from the hand joints: the ray is the index-finger
// pointing direction and "trigger" is the thumb/index pinch strength, so all
// the interaction code above this file is written once.
#pragma once

#include "xr_session.h"

namespace fcxr {

enum class Hand { Left = 0, Right = 1 };

struct HandState {
    bool active = false;         // pose is being tracked this frame
    bool fromHandTracking = false;

    Vec3 aimPosition{0, 0, 0};   // ray origin, in app space
    Quat aimOrientation;         // -Z is forward
    Vec3 gripPosition{0, 0, 0};
    Quat gripOrientation;

    float trigger = 0.0f;        // 0..1 (pinch strength when hand tracked)
    float squeeze = 0.0f;        // 0..1
    Vec2 thumbstick{0, 0};
    bool thumbstickClick = false;

    // A/X on the lower button, B/Y on the upper one.
    bool buttonLower = false;
    bool buttonUpper = false;
    bool menu = false;           // left controller only

    // Edge flags, valid for the frame in which the change happened.
    bool triggerPressed = false;
    bool triggerReleased = false;
    bool squeezePressed = false;
    bool squeezeReleased = false;
    bool lowerPressed = false;
    bool upperPressed = false;
    bool menuPressed = false;
    bool thumbstickClicked = false;

    Vec3 rayDirection() const { return rotate(aimOrientation, Vec3(0, 0, -1)); }
    Vec3 rayUp() const { return rotate(aimOrientation, Vec3(0, 1, 0)); }
};

class InputSystem {
public:
    // Creates the action set and suggests bindings. Call after the instance
    // exists but before the session is attached.
    bool createActions(XrInstance instance);
    // Attaches the action set to the session and creates the pose spaces.
    bool attach(XrSession session, XrSpace appSpace);
    void destroy();

    // Called once per frame, after xrWaitFrame, with the predicted time.
    void update(XrTime displayTime, bool focused);

    const HandState& hand(Hand h) const { return hands_[int(h)]; }
    HandState& mutableHand(Hand h) { return hands_[int(h)]; }

    // A short haptic pulse. `amplitude` 0..1, `seconds` is clamped to 1 s.
    void vibrate(Hand h, float amplitude, float seconds = 0.03f);

    // Hand tracking is created lazily and only if the extension is present.
    void enableHandTracking(XrInstance instance, XrSession session, bool available);
    bool handTrackingActive() const { return handTrackingActive_; }

private:
    bool suggestBindings(XrInstance instance, const char* profile, bool touchExtras);
    void updateHandTracking(XrTime displayTime);

    XrInstance instance_ = XR_NULL_HANDLE;
    XrSession session_ = XR_NULL_HANDLE;
    XrSpace appSpace_ = XR_NULL_HANDLE;

    XrActionSet actionSet_ = XR_NULL_HANDLE;
    XrPath handPaths_[2] = {XR_NULL_PATH, XR_NULL_PATH};

    XrAction aimPose_ = XR_NULL_HANDLE;
    XrAction gripPose_ = XR_NULL_HANDLE;
    XrAction trigger_ = XR_NULL_HANDLE;
    XrAction squeeze_ = XR_NULL_HANDLE;
    XrAction thumbstick_ = XR_NULL_HANDLE;
    XrAction thumbstickClick_ = XR_NULL_HANDLE;
    XrAction buttonLower_ = XR_NULL_HANDLE;
    XrAction buttonUpper_ = XR_NULL_HANDLE;
    XrAction menu_ = XR_NULL_HANDLE;
    XrAction haptic_ = XR_NULL_HANDLE;

    XrSpace aimSpace_[2] = {XR_NULL_HANDLE, XR_NULL_HANDLE};
    XrSpace gripSpace_[2] = {XR_NULL_HANDLE, XR_NULL_HANDLE};

    HandState hands_[2];
    bool previousTrigger_[2] = {false, false};
    bool previousSqueeze_[2] = {false, false};
    bool previousLower_[2] = {false, false};
    bool previousUpper_[2] = {false, false};
    bool previousMenu_[2] = {false, false};
    bool previousStickClick_[2] = {false, false};

    // hand tracking
    bool handTrackingActive_ = false;
    XrHandTrackerEXT handTracker_[2] = {XR_NULL_HANDLE, XR_NULL_HANDLE};
    PFN_xrCreateHandTrackerEXT xrCreateHandTrackerEXT_ = nullptr;
    PFN_xrDestroyHandTrackerEXT xrDestroyHandTrackerEXT_ = nullptr;
    PFN_xrLocateHandJointsEXT xrLocateHandJointsEXT_ = nullptr;
};

}  // namespace fcxr
