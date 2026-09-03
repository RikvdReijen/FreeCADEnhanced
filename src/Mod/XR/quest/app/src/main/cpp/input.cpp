// SPDX-License-Identifier: LGPL-2.1-or-later
#include "input.h"

#include <cstring>
#include <vector>

#include "log.h"

namespace fcxr {
namespace {

XrPath stringToPath(XrInstance instance, const char* text) {
    XrPath path = XR_NULL_PATH;
    if (XR_FAILED(xrStringToPath(instance, text, &path))) {
        LOGE("xrStringToPath failed for %s", text);
        return XR_NULL_PATH;
    }
    return path;
}

XrAction createAction(XrActionSet set, XrActionType type, const char* name,
                      const char* localised, uint32_t subactionCount,
                      const XrPath* subactions) {
    XrActionCreateInfo info{XR_TYPE_ACTION_CREATE_INFO};
    info.actionType = type;
    std::strncpy(info.actionName, name, XR_MAX_ACTION_NAME_SIZE - 1);
    std::strncpy(info.localizedActionName, localised, XR_MAX_LOCALIZED_ACTION_NAME_SIZE - 1);
    info.countSubactionPaths = subactionCount;
    info.subactionPaths = subactions;
    XrAction action = XR_NULL_HANDLE;
    if (XR_FAILED(xrCreateAction(set, &info, &action)))
        LOGE("xrCreateAction failed for %s", name);
    return action;
}

}  // namespace

bool InputSystem::createActions(XrInstance instance) {
    instance_ = instance;

    XrActionSetCreateInfo setInfo{XR_TYPE_ACTION_SET_CREATE_INFO};
    std::strncpy(setInfo.actionSetName, "gameplay", XR_MAX_ACTION_SET_NAME_SIZE - 1);
    std::strncpy(setInfo.localizedActionSetName, "FreeCAD XR",
                 XR_MAX_LOCALIZED_ACTION_SET_NAME_SIZE - 1);
    setInfo.priority = 0;
    if (!xrCheck(instance, xrCreateActionSet(instance, &setInfo, &actionSet_),
                 "xrCreateActionSet"))
        return false;

    handPaths_[0] = stringToPath(instance, "/user/hand/left");
    handPaths_[1] = stringToPath(instance, "/user/hand/right");

    aimPose_ = createAction(actionSet_, XR_ACTION_TYPE_POSE_INPUT, "aim_pose", "Pointer", 2,
                            handPaths_);
    gripPose_ = createAction(actionSet_, XR_ACTION_TYPE_POSE_INPUT, "grip_pose", "Grip", 2,
                             handPaths_);
    trigger_ = createAction(actionSet_, XR_ACTION_TYPE_FLOAT_INPUT, "trigger", "Trigger", 2,
                            handPaths_);
    squeeze_ = createAction(actionSet_, XR_ACTION_TYPE_FLOAT_INPUT, "squeeze", "Grip squeeze",
                            2, handPaths_);
    thumbstick_ = createAction(actionSet_, XR_ACTION_TYPE_VECTOR2F_INPUT, "thumbstick",
                               "Thumbstick", 2, handPaths_);
    thumbstickClick_ = createAction(actionSet_, XR_ACTION_TYPE_BOOLEAN_INPUT, "stick_click",
                                    "Thumbstick click", 2, handPaths_);
    buttonLower_ = createAction(actionSet_, XR_ACTION_TYPE_BOOLEAN_INPUT, "button_lower",
                                "A / X", 2, handPaths_);
    buttonUpper_ = createAction(actionSet_, XR_ACTION_TYPE_BOOLEAN_INPUT, "button_upper",
                                "B / Y", 2, handPaths_);
    menu_ = createAction(actionSet_, XR_ACTION_TYPE_BOOLEAN_INPUT, "menu", "Menu", 2,
                         handPaths_);
    haptic_ = createAction(actionSet_, XR_ACTION_TYPE_VIBRATION_OUTPUT, "haptic", "Haptics", 2,
                           handPaths_);

    const bool touch = suggestBindings(instance, "/interaction_profiles/oculus/touch_controller",
                                       true);
    const bool simple = suggestBindings(
        instance, "/interaction_profiles/khr/simple_controller", false);
    if (!touch && !simple) {
        LOGE("no interaction profile bindings were accepted");
        return false;
    }
    return true;
}

// `touchExtras` enables the bindings that only exist on Touch style
// controllers (thumbstick, A/B/X/Y, analog squeeze).
bool InputSystem::suggestBindings(XrInstance instance, const char* profile, bool touchExtras) {
    XrPath profilePath = stringToPath(instance, profile);
    if (profilePath == XR_NULL_PATH) return false;

    std::vector<XrActionSuggestedBinding> bindings;
    auto bind = [&](XrAction action, const char* path) {
        const XrPath p = stringToPath(instance, path);
        if (p != XR_NULL_PATH && action != XR_NULL_HANDLE) bindings.push_back({action, p});
    };

    bind(aimPose_, "/user/hand/left/input/aim/pose");
    bind(aimPose_, "/user/hand/right/input/aim/pose");
    bind(gripPose_, "/user/hand/left/input/grip/pose");
    bind(gripPose_, "/user/hand/right/input/grip/pose");
    bind(haptic_, "/user/hand/left/output/haptic");
    bind(haptic_, "/user/hand/right/output/haptic");

    if (touchExtras) {
        bind(trigger_, "/user/hand/left/input/trigger/value");
        bind(trigger_, "/user/hand/right/input/trigger/value");
        bind(squeeze_, "/user/hand/left/input/squeeze/value");
        bind(squeeze_, "/user/hand/right/input/squeeze/value");
        bind(thumbstick_, "/user/hand/left/input/thumbstick");
        bind(thumbstick_, "/user/hand/right/input/thumbstick");
        bind(thumbstickClick_, "/user/hand/left/input/thumbstick/click");
        bind(thumbstickClick_, "/user/hand/right/input/thumbstick/click");
        bind(buttonLower_, "/user/hand/left/input/x/click");
        bind(buttonUpper_, "/user/hand/left/input/y/click");
        bind(buttonLower_, "/user/hand/right/input/a/click");
        bind(buttonUpper_, "/user/hand/right/input/b/click");
        // Only the left controller has a menu button in this profile.
        bind(menu_, "/user/hand/left/input/menu/click");
    } else {
        // khr/simple_controller: a click, not an axis.
        bind(trigger_, "/user/hand/left/input/select/click");
        bind(trigger_, "/user/hand/right/input/select/click");
        bind(menu_, "/user/hand/left/input/menu/click");
        bind(menu_, "/user/hand/right/input/menu/click");
    }

    XrInteractionProfileSuggestedBinding suggestion{
        XR_TYPE_INTERACTION_PROFILE_SUGGESTED_BINDING};
    suggestion.interactionProfile = profilePath;
    suggestion.countSuggestedBindings = uint32_t(bindings.size());
    suggestion.suggestedBindings = bindings.data();
    const XrResult result = xrSuggestInteractionProfileBindings(instance, &suggestion);
    if (XR_FAILED(result)) {
        LOGW("bindings for %s were rejected: %s", profile,
             xrResultString(instance, result).c_str());
        return false;
    }
    LOGI("suggested %zu bindings for %s", bindings.size(), profile);
    return true;
}

bool InputSystem::attach(XrSession session, XrSpace appSpace) {
    session_ = session;
    appSpace_ = appSpace;

    XrSessionActionSetsAttachInfo attachInfo{XR_TYPE_SESSION_ACTION_SETS_ATTACH_INFO};
    attachInfo.countActionSets = 1;
    attachInfo.actionSets = &actionSet_;
    if (!xrCheck(instance_, xrAttachSessionActionSets(session, &attachInfo),
                 "xrAttachSessionActionSets"))
        return false;

    for (int i = 0; i < 2; ++i) {
        XrActionSpaceCreateInfo spaceInfo{XR_TYPE_ACTION_SPACE_CREATE_INFO};
        spaceInfo.action = aimPose_;
        spaceInfo.subactionPath = handPaths_[i];
        spaceInfo.poseInActionSpace.orientation.w = 1.0f;
        xrCheck(instance_, xrCreateActionSpace(session, &spaceInfo, &aimSpace_[i]),
                "xrCreateActionSpace(aim)");
        spaceInfo.action = gripPose_;
        xrCheck(instance_, xrCreateActionSpace(session, &spaceInfo, &gripSpace_[i]),
                "xrCreateActionSpace(grip)");
    }
    return true;
}

void InputSystem::destroy() {
    for (int i = 0; i < 2; ++i) {
        if (aimSpace_[i] != XR_NULL_HANDLE) xrDestroySpace(aimSpace_[i]);
        if (gripSpace_[i] != XR_NULL_HANDLE) xrDestroySpace(gripSpace_[i]);
        aimSpace_[i] = gripSpace_[i] = XR_NULL_HANDLE;
        if (handTracker_[i] != XR_NULL_HANDLE && xrDestroyHandTrackerEXT_)
            xrDestroyHandTrackerEXT_(handTracker_[i]);
        handTracker_[i] = XR_NULL_HANDLE;
    }
    if (actionSet_ != XR_NULL_HANDLE) xrDestroyActionSet(actionSet_);
    actionSet_ = XR_NULL_HANDLE;
    handTrackingActive_ = false;
}

void InputSystem::enableHandTracking(XrInstance instance, XrSession session, bool available) {
    if (!available) return;
    xrGetInstanceProcAddr(instance, "xrCreateHandTrackerEXT",
                          reinterpret_cast<PFN_xrVoidFunction*>(&xrCreateHandTrackerEXT_));
    xrGetInstanceProcAddr(instance, "xrDestroyHandTrackerEXT",
                          reinterpret_cast<PFN_xrVoidFunction*>(&xrDestroyHandTrackerEXT_));
    xrGetInstanceProcAddr(instance, "xrLocateHandJointsEXT",
                          reinterpret_cast<PFN_xrVoidFunction*>(&xrLocateHandJointsEXT_));
    if (!xrCreateHandTrackerEXT_ || !xrLocateHandJointsEXT_) {
        LOGW("hand tracking entry points missing");
        return;
    }
    for (int i = 0; i < 2; ++i) {
        XrHandTrackerCreateInfoEXT info{XR_TYPE_HAND_TRACKER_CREATE_INFO_EXT};
        info.hand = i == 0 ? XR_HAND_LEFT_EXT : XR_HAND_RIGHT_EXT;
        info.handJointSet = XR_HAND_JOINT_SET_DEFAULT_EXT;
        if (XR_FAILED(xrCreateHandTrackerEXT_(session, &info, &handTracker_[i]))) {
            LOGW("xrCreateHandTrackerEXT failed for hand %d", i);
            handTracker_[i] = XR_NULL_HANDLE;
        }
    }
    handTrackingActive_ = handTracker_[0] != XR_NULL_HANDLE || handTracker_[1] != XR_NULL_HANDLE;
    LOGI("hand tracking %s", handTrackingActive_ ? "enabled" : "unavailable");
}

void InputSystem::update(XrTime displayTime, bool focused) {
    for (int i = 0; i < 2; ++i) {
        HandState& state = hands_[i];
        const bool wasTrigger = state.trigger > 0.5f;
        const bool wasSqueeze = state.squeeze > 0.5f;
        state = HandState();
        previousTrigger_[i] = wasTrigger;
        previousSqueeze_[i] = wasSqueeze;
    }
    if (!focused || session_ == XR_NULL_HANDLE) {
        // Not focused: report nothing pressed so nothing fires behind the
        // system menu, but keep the hand tracking poses for the hand model.
        if (handTrackingActive_) updateHandTracking(displayTime);
        return;
    }

    XrActiveActionSet active{actionSet_, XR_NULL_PATH};
    XrActionsSyncInfo syncInfo{XR_TYPE_ACTIONS_SYNC_INFO};
    syncInfo.countActiveActionSets = 1;
    syncInfo.activeActionSets = &active;
    if (XR_FAILED(xrSyncActions(session_, &syncInfo))) return;

    for (int i = 0; i < 2; ++i) {
        HandState& state = hands_[i];
        XrActionStateGetInfo get{XR_TYPE_ACTION_STATE_GET_INFO};
        get.subactionPath = handPaths_[i];

        // poses
        get.action = aimPose_;
        XrActionStatePose poseState{XR_TYPE_ACTION_STATE_POSE};
        xrGetActionStatePose(session_, &get, &poseState);
        if (poseState.isActive) {
            XrSpaceLocation location{XR_TYPE_SPACE_LOCATION};
            if (XR_SUCCEEDED(xrLocateSpace(aimSpace_[i], appSpace_, displayTime, &location)) &&
                (location.locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT) &&
                (location.locationFlags & XR_SPACE_LOCATION_ORIENTATION_VALID_BIT)) {
                state.active = true;
                state.aimPosition = xrToVec3(location.pose.position);
                state.aimOrientation = xrToQuat(location.pose.orientation);
            }
            XrSpaceLocation grip{XR_TYPE_SPACE_LOCATION};
            if (XR_SUCCEEDED(xrLocateSpace(gripSpace_[i], appSpace_, displayTime, &grip)) &&
                (grip.locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT)) {
                state.gripPosition = xrToVec3(grip.pose.position);
                state.gripOrientation = xrToQuat(grip.pose.orientation);
            }
        }

        XrActionStateFloat floatState{XR_TYPE_ACTION_STATE_FLOAT};
        get.action = trigger_;
        if (XR_SUCCEEDED(xrGetActionStateFloat(session_, &get, &floatState)) &&
            floatState.isActive)
            state.trigger = floatState.currentState;
        get.action = squeeze_;
        if (XR_SUCCEEDED(xrGetActionStateFloat(session_, &get, &floatState)) &&
            floatState.isActive)
            state.squeeze = floatState.currentState;

        XrActionStateVector2f stick{XR_TYPE_ACTION_STATE_VECTOR2F};
        get.action = thumbstick_;
        if (XR_SUCCEEDED(xrGetActionStateVector2f(session_, &get, &stick)) && stick.isActive)
            state.thumbstick = Vec2(stick.currentState.x, stick.currentState.y);

        XrActionStateBoolean boolState{XR_TYPE_ACTION_STATE_BOOLEAN};
        auto readBool = [&](XrAction action, bool* target) {
            get.action = action;
            if (action != XR_NULL_HANDLE &&
                XR_SUCCEEDED(xrGetActionStateBoolean(session_, &get, &boolState)) &&
                boolState.isActive)
                *target = boolState.currentState == XR_TRUE;
        };
        readBool(thumbstickClick_, &state.thumbstickClick);
        readBool(buttonLower_, &state.buttonLower);
        readBool(buttonUpper_, &state.buttonUpper);
        readBool(menu_, &state.menu);

        // Edges. The simple controller profile binds the trigger to a click,
        // which arrives as 0 or 1, so the same threshold works for both.
        const bool triggerDown = state.trigger > 0.5f;
        state.triggerPressed = triggerDown && !previousTrigger_[i];
        state.triggerReleased = !triggerDown && previousTrigger_[i];
        const bool squeezeDown = state.squeeze > 0.5f;
        state.squeezePressed = squeezeDown && !previousSqueeze_[i];
        state.squeezeReleased = !squeezeDown && previousSqueeze_[i];
        state.lowerPressed = state.buttonLower && !previousLower_[i];
        state.upperPressed = state.buttonUpper && !previousUpper_[i];
        state.menuPressed = state.menu && !previousMenu_[i];
        state.thumbstickClicked = state.thumbstickClick && !previousStickClick_[i];
        previousLower_[i] = state.buttonLower;
        previousUpper_[i] = state.buttonUpper;
        previousMenu_[i] = state.menu;
        previousStickClick_[i] = state.thumbstickClick;
    }

    if (handTrackingActive_) updateHandTracking(displayTime);
}

// Fills in a hand whose controller is not tracking from the joint poses:
// the ray runs along the index finger and the trigger is the pinch strength.
void InputSystem::updateHandTracking(XrTime displayTime) {
    for (int i = 0; i < 2; ++i) {
        if (handTracker_[i] == XR_NULL_HANDLE) continue;
        HandState& state = hands_[i];
        if (state.active) continue;  // the controller wins when it is held

        XrHandJointLocationEXT joints[XR_HAND_JOINT_COUNT_EXT];
        XrHandJointLocationsEXT locations{XR_TYPE_HAND_JOINT_LOCATIONS_EXT};
        locations.jointCount = XR_HAND_JOINT_COUNT_EXT;
        locations.jointLocations = joints;
        XrHandJointsLocateInfoEXT locateInfo{XR_TYPE_HAND_JOINTS_LOCATE_INFO_EXT};
        locateInfo.baseSpace = appSpace_;
        locateInfo.time = displayTime;
        if (XR_FAILED(xrLocateHandJointsEXT_(handTracker_[i], &locateInfo, &locations)))
            continue;
        if (!locations.isActive) continue;

        const XrHandJointLocationEXT& indexTip = joints[XR_HAND_JOINT_INDEX_TIP_EXT];
        const XrHandJointLocationEXT& indexProximal = joints[XR_HAND_JOINT_INDEX_PROXIMAL_EXT];
        const XrHandJointLocationEXT& thumbTip = joints[XR_HAND_JOINT_THUMB_TIP_EXT];
        const XrHandJointLocationEXT& palm = joints[XR_HAND_JOINT_PALM_EXT];
        const XrSpaceLocationFlags need =
            XR_SPACE_LOCATION_POSITION_VALID_BIT | XR_SPACE_LOCATION_ORIENTATION_VALID_BIT;
        if ((indexTip.locationFlags & need) != need ||
            (indexProximal.locationFlags & need) != need)
            continue;

        const Vec3 tip = xrToVec3(indexTip.pose.position);
        const Vec3 base = xrToVec3(indexProximal.pose.position);
        const Vec3 direction = normalize(tip - base);

        state.active = true;
        state.fromHandTracking = true;
        state.aimPosition = tip;
        // Build an orientation whose -Z is the pointing direction.
        state.aimOrientation = quatFromTo(Vec3(0, 0, -1), direction);
        state.gripPosition = (palm.locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT)
                                 ? xrToVec3(palm.pose.position)
                                 : tip;
        state.gripOrientation = state.aimOrientation;

        // Pinch: 2 cm apart is released, 1.5 cm is fully pressed.
        if ((thumbTip.locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT)) {
            const float distance = length(tip - xrToVec3(thumbTip.pose.position));
            state.trigger = saturate((0.035f - distance) / 0.020f);
        }
        const bool triggerDown = state.trigger > 0.5f;
        state.triggerPressed = triggerDown && !previousTrigger_[i];
        state.triggerReleased = !triggerDown && previousTrigger_[i];
    }
}

void InputSystem::vibrate(Hand h, float amplitude, float seconds) {
    if (session_ == XR_NULL_HANDLE || haptic_ == XR_NULL_HANDLE) return;
    XrHapticVibration vibration{XR_TYPE_HAPTIC_VIBRATION};
    vibration.amplitude = saturate(amplitude);
    vibration.duration = XrDuration(clampf(seconds, 0.0f, 1.0f) * 1e9f);
    vibration.frequency = XR_FREQUENCY_UNSPECIFIED;
    XrHapticActionInfo info{XR_TYPE_HAPTIC_ACTION_INFO};
    info.action = haptic_;
    info.subactionPath = handPaths_[int(h)];
    xrApplyHapticFeedback(session_, &info,
                          reinterpret_cast<const XrHapticBaseHeader*>(&vibration));
}

}  // namespace fcxr
