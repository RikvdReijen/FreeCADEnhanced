// SPDX-License-Identifier: LGPL-2.1-or-later
#include "app.h"

#include <android/asset_manager.h>
#include <time.h>

#include <algorithm>
#include <cstdio>

#include "jni_bridge.h"
#include "log.h"

namespace fcxr {
namespace {

App* gApp = nullptr;

double nowSeconds() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return double(ts.tv_sec) + double(ts.tv_nsec) * 1e-9;
}

}  // namespace

App* appInstance() { return gApp; }
void setAppInstance(App* app) { gApp = app; }

// ------------------------------------------------------------------- setup

bool App::init(JavaVM* vm, jobject activity, AAssetManager* assetManager,
               const std::string& filesDir, const std::string& cacheDir) {
    setAppInstance(this);

    if (!egl_.create()) return false;
    if (!xr_.createInstance(vm, activity)) return false;
    if (!xr_.createSession(egl_)) return false;
    if (!input_.createActions(xr_.instance())) return false;
    if (!input_.attach(xr_.session(), xr_.appSpace())) return false;
    input_.enableHandTracking(xr_.instance(), xr_.session(), xr_.handTrackingAvailable());

    assets_.init(assetManager);
    std::string pbrVert, pbrFrag, unlitVert, unlitFrag;
    if (!assets_.readText("shaders/pbr.vert", &pbrVert) ||
        !assets_.readText("shaders/pbr.frag", &pbrFrag) ||
        !assets_.readText("shaders/unlit.vert", &unlitVert) ||
        !assets_.readText("shaders/unlit.frag", &unlitFrag)) {
        LOGE("shader assets are missing from the APK");
        return false;
    }
    if (!renderer_.init(pbrVert, pbrFrag, unlitVert, unlitFrag)) return false;

    // 4x MSAA is the sweet spot on Quest 3 for this triangle budget.
    for (int eye = 0; eye < 2; ++eye)
        eyeTargets_[eye].create(xr_.eyeWidth(), xr_.eyeHeight(), 4);

    storage_.init(filesDir, cacheDir);
    environment_.init(&assets_, &renderer_);
    paint_.init(&renderer_);
    sync_.start();

    const Settings& settings = storage_.settings();
    if (!settings.serverHost.empty()) {
        sync_.setServer(settings.serverHost, settings.serverPort);
        sync_.setToken(settings.serverToken);
    }
    xr_.setPassthrough(settings.passthrough);

    // Start in the environment the user was last in, or the smallest one.
    std::string startEnvironment = settings.environment;
    if (startEnvironment.empty() && !environment_.available().empty()) {
        startEnvironment = environment_.available().front().id;
        for (const EnvironmentEntry& entry : environment_.available()) {
            if (entry.id == "void") startEnvironment = entry.id;
        }
    }
    if (!startEnvironment.empty()) environment_.switchTo(startEnvironment);

    refreshLibrary();
    if (!settings.lastDocument.empty() && fileExists(settings.lastDocument))
        loadDocumentFromLibrary(settings.lastDocument);

    vector_.setPlane(Vec3(0, 1.0f, -0.6f), quatAxisAngle(Vec3(1, 0, 0), 0.0f));
    lastFrameTime_ = nowSeconds();
    initialised_ = true;
    LOGI("app initialised");
    return true;
}

void App::shutdown() {
    sync_.stop();
    paint_.clear();
    document_.unload(&renderer_);
    environment_.shutdown();
    for (EyeTarget& target : eyeTargets_) target.destroy();
    renderer_.shutdown();
    input_.destroy();
    xr_.destroy();
    egl_.destroy();
    setAppInstance(nullptr);
    initialised_ = false;
}

// -------------------------------------------------------------- main frame

bool App::frame() {
    if (!initialised_) return false;
    bool exitRequested = false;
    if (!xr_.pollEvents(&exitRequested) || exitRequested) {
        running_ = false;
        return false;
    }
    if (!xr_.sessionRunning()) return true;

    FrameInfo info;
    if (!xr_.beginFrame(&info)) return true;

    const double now = nowSeconds();
    float delta = float(now - lastFrameTime_);
    lastFrameTime_ = now;
    delta = clampf(delta, 0.0f, 0.1f);

    if (info.shouldRender && info.viewCount > 0) {
        // Head pose: the midpoint between the eyes.
        headPosition_ = (info.views[0].position + info.views[1].position) * 0.5f;
        headOrientation_ = info.views[0].orientation;
    }

    input_.update(info.predictedDisplayTime, xr_.sessionFocused());
    drainActions();
    pollSync();
    update(delta);

    float nearZ = 0.02f, farZ = 100.0f;
    environment_.clipPlanes(&nearZ, &farZ);
    xr_.setClipPlanes(nearZ, farZ);

    if (info.shouldRender) render(info);
    xr_.endFrame(info);
    return true;
}

void App::update(float deltaSeconds) {
    environment_.update(deltaSeconds);
    // The environment scales the world about the user's feet, which sit under
    // the head on the floor plane of the app space.
    environment_.setUserPosition(Vec3(headPosition_.x, 0.0f, headPosition_.z) + playerOffset_);

    handleLocomotion(deltaSeconds);

    // Order matters: pick first, then build the UI (which decides whether the
    // ray is over a panel), then act on the tools. The other way round makes
    // every widget lag a frame and lets the first press on a button also land
    // on the model behind it.
    const HandState& pointer = input_.hand(pointerHand_);
    lastHit_ = DocumentHit();
    if (document_.loaded() && pointer.active)
        document_.raycast(pointer.aimPosition, pointer.rayDirection(), 50.0f, &lastHit_);

    overlay_.clear();
    buildUi(deltaSeconds);
    handleTools();
    paint_.buildRibbonGeometry(overlay_);
    if (tool_ == Tool::VectorDraw || tool_ == Tool::VectorEdit) {
        vector_.buildGeometry(overlay_, tool_ == Tool::VectorEdit, selectedVectorPath_,
                              selectedVectorNode_, headPosition_);
    }
    paint_.flush();

    if (statusTimer_ > 0.0f) {
        statusTimer_ -= deltaSeconds;
        if (statusTimer_ <= 0.0f) {
            statusText_ = currentDocumentName_.empty() ? "FreeCAD XR" : currentDocumentName_;
            statusWarning_ = false;
        }
    }
}

void App::render(const FrameInfo& info) {
    // Environment geometry is skipped in passthrough: the room the user is
    // really in replaces it, and only the document, paint and UI are drawn.
    const bool passthrough = xr_.passthroughEnabled();
    const Vec3 clearColor = passthrough ? Vec3(0, 0, 0) : Vec3(0.01f, 0.012f, 0.015f);
    const float clearAlpha = passthrough ? 0.0f : 1.0f;

    std::vector<RenderInstance> instances;
    if (!passthrough) instances = environment_.instances();
    const std::vector<RenderInstance>& documentInstances = document_.instances();
    instances.insert(instances.end(), documentInstances.begin(), documentInstances.end());

    std::vector<RenderLight> lights = environment_.lights();
    if (passthrough || lights.empty()) {
        // A key light from over the user's shoulder so the document still
        // reads when the environment is not being drawn.
        RenderLight key;
        key.type = 0;
        key.direction = normalize(Vec3(-0.4f, -0.8f, -0.45f));
        key.color = Vec3(1.0f, 0.98f, 0.95f);
        key.intensity = 2.4f;
        lights.push_back(key);
    }
    const Vec3 ambient = passthrough ? Vec3(0.25f, 0.25f, 0.27f) : environment_.ambient();

    for (int eye = 0; eye < info.viewCount && eye < 2; ++eye) {
        GLuint texture = 0;
        int width = 0, height = 0;
        if (!xr_.acquireEye(eye, &texture, &width, &height)) continue;
        renderer_.beginEye(eyeTargets_[eye], texture, clearColor, clearAlpha);
        renderer_.drawScene(info.views[eye].view, info.views[eye].projection,
                            info.views[eye].position, instances, lights, ambient, 1.0f);
        renderer_.drawOverlay(info.views[eye].view, info.views[eye].projection, overlay_);
        if (environment_.fadeAmount() > 0.001f)
            renderer_.drawFade(environment_.fadeAmount(), Vec3(0, 0, 0));
        renderer_.endEye(eyeTargets_[eye], texture);
        xr_.releaseEye(eye);
    }
}

// ------------------------------------------------------------- locomotion

Vec3 App::headForward() const {
    Vec3 forward = rotate(headOrientation_, Vec3(0, 0, -1));
    forward.y = 0.0f;
    if (lengthSq(forward) < 1e-6f) return Vec3(0, 0, -1);
    return normalize(forward);
}

void App::handleLocomotion(float deltaSeconds) {
    const HandState& left = input_.hand(Hand::Left);
    const HandState& right = input_.hand(Hand::Right);

    // Smooth slide on the left stick, in the direction the head is facing.
    // Speed is in *world* metres, so walking inside a miniaturised machine
    // moves the user at a sensible apparent pace.
    const float deadZone = 0.15f;
    if (std::fabs(left.thumbstick.x) > deadZone || std::fabs(left.thumbstick.y) > deadZone) {
        const Vec3 forward = headForward();
        const Vec3 rightAxis = normalize(cross(forward, Vec3(0, 1, 0)));
        const float speed = 1.4f;
        Vec3 move = forward * left.thumbstick.y + rightAxis * left.thumbstick.x;
        if (lengthSq(move) > 1.0f) move = normalize(move);
        playerOffset_ -= move * (speed * deltaSeconds);
    }

    // Snap turn on the right stick. Turning rotates the world about the head.
    snapCooldown_ = std::max(0.0f, snapCooldown_ - deltaSeconds);
    if (std::fabs(right.thumbstick.x) > 0.7f && snapCooldown_ <= 0.0f) {
        const float step = degToRad(right.thumbstick.x > 0.0f ? -30.0f : 30.0f);
        playerYaw_ += step;
        snapCooldown_ = 0.28f;
        input_.vibrate(Hand::Right, 0.4f, 0.02f);
    }
}

// ------------------------------------------------------------------ tools

void App::handleTools() {
    const HandState& pointer = input_.hand(pointerHand_);
    const HandState& other = input_.hand(pointerHand_ == Hand::Right ? Hand::Left : Hand::Right);

    // The menu button (or left Y) cycles the open panel.
    if (other.menuPressed || other.upperPressed) {
        screen_ = screen_ == Screen::None ? Screen::Tools : Screen::None;
    }
    // B/Y on the pointer hand toggles passthrough.
    if (pointer.upperPressed && xr_.passthroughSupported()) {
        xr_.setPassthrough(!xr_.passthroughEnabled());
        storage_.mutableSettings().passthrough = xr_.passthroughEnabled();
        storage_.saveSettings();
        setStatus(xr_.passthroughEnabled() ? "passthrough on" : "passthrough off");
    }
    // A/X undoes.
    if (pointer.lowerPressed) {
        if (tool_ == Tool::PaintRibbon) paint_.undoRibbon();
        else if (tool_ == Tool::PaintTexture) paint_.undo();
        else if (tool_ == Tool::VectorDraw || tool_ == Tool::VectorEdit) {
            if (selectedVectorPath_ >= 0) {
                vector_.deletePath(selectedVectorPath_);
                selectedVectorPath_ = -1;
            }
        }
    }

    // The UI gets first refusal on the trigger.
    if (ui_.consumedPointer()) {
        if (paint_.strokeActive()) paint_.endStroke();
        if (paint_.ribbonActive()) paint_.endRibbon();
        return;
    }

    const Vec3 rayOrigin = pointer.aimPosition;
    const Vec3 rayDirection = pointer.rayDirection();

    switch (tool_) {
        case Tool::PaintTexture: {
            if (!pointer.active) break;
            if (pointer.triggerPressed && lastHit_.hit) {
                paint_.beginStroke(document_, lastHit_);
                activeTarget_ = paint_.targetForPrimitive(lastHit_.primitive);
                input_.vibrate(pointerHand_, 0.25f, 0.015f);
            } else if (pointer.trigger > 0.35f && paint_.strokeActive() && lastHit_.hit) {
                paint_.continueStroke(document_, lastHit_);
            } else if (pointer.triggerReleased) {
                paint_.endStroke();
            }
            break;
        }
        case Tool::PaintRibbon: {
            if (!pointer.active) break;
            // Ribbons are drawn in the air at the controller tip.
            const Vec3 tip = pointer.aimPosition + rayDirection * 0.05f;
            const Vec3 normal = pointer.rayUp();
            const float radius = paint_.brush().radius;
            if (pointer.triggerPressed) {
                paint_.beginRibbon(tip, normal, radius);
                input_.vibrate(pointerHand_, 0.3f, 0.02f);
            } else if (pointer.trigger > 0.2f && paint_.ribbonActive()) {
                paint_.extendRibbon(tip, normal, radius * (0.4f + 0.6f * pointer.trigger),
                                    0.0f);
            } else if (pointer.triggerReleased) {
                paint_.endRibbon();
            }
            break;
        }
        case Tool::VectorDraw: {
            Vec2 local;
            Vec3 world;
            if (pointer.triggerPressed &&
                vector_.rayToPlane(rayOrigin, rayDirection, &local, &world)) {
                if (vector_.activePath() < 0) selectedVectorPath_ = vector_.newPath();
                vector_.appendNode(local);
                selectedVectorPath_ = vector_.activePath();
                input_.vibrate(pointerHand_, 0.3f, 0.015f);
            }
            if (pointer.squeezePressed) {
                vector_.closeActivePath(true);
                vector_.finishPath();
                setStatus("path closed");
            }
            break;
        }
        case Tool::VectorEdit: {
            Vec2 local;
            Vec3 world;
            if (!vector_.rayToPlane(rayOrigin, rayDirection, &local, &world)) break;
            if (pointer.triggerPressed) {
                int path = -1, node = -1;
                bool isIn = false;
                if (vector_.pickHandle(local, 0.03f, &path, &node, &isIn)) {
                    selectedVectorPath_ = path;
                    selectedVectorNode_ = node;
                    draggingHandle_ = true;
                    draggingHandleIn_ = isIn;
                } else if (vector_.pickNode(local, 0.03f, &path, &node)) {
                    selectedVectorPath_ = path;
                    selectedVectorNode_ = node;
                    draggingHandle_ = false;
                } else {
                    selectedVectorPath_ = -1;
                    selectedVectorNode_ = -1;
                }
                if (selectedVectorNode_ >= 0) input_.vibrate(pointerHand_, 0.25f, 0.012f);
            } else if (pointer.trigger > 0.4f && selectedVectorNode_ >= 0) {
                if (draggingHandle_)
                    vector_.moveHandle(selectedVectorPath_, selectedVectorNode_,
                                       draggingHandleIn_, local);
                else
                    vector_.moveNode(selectedVectorPath_, selectedVectorNode_, local);
            } else if (pointer.triggerReleased) {
                draggingHandle_ = false;
            }
            break;
        }
        case Tool::Measure: {
            if (lastHit_.hit) {
                char buffer[96];
                const Vec3 local = lastHit_.position;
                std::snprintf(buffer, sizeof(buffer), "%.1f %.1f %.1f mm", local.x * 1000.0f,
                              local.y * 1000.0f, local.z * 1000.0f);
                setStatus(buffer);
            }
            break;
        }
        default:
            break;
    }
}

// ---------------------------------------------------------------- plumbing

void App::setStatus(const std::string& message, bool warning) {
    statusText_ = message;
    statusWarning_ = warning;
    statusTimer_ = 4.0f;
    LOGI("status: %s", message.c_str());
}

void App::refreshLibrary() {
    library_ = storage_.list();
    selectedLibrary_ = library_.empty() ? -1 : 0;
}

void App::loadDocumentFromLibrary(const std::string& path) {
    std::vector<uint8_t> data;
    if (!storage_.load(path, &data)) {
        setStatus("cannot read that file", true);
        return;
    }
    const size_t slash = path.find_last_of('/');
    loadDocumentFromMemory(data, slash == std::string::npos ? path : path.substr(slash + 1));
    storage_.noteRecent(path);
}

void App::loadDocumentFromMemory(const std::vector<uint8_t>& data, const std::string& name) {
    // A .FCStd is a FreeCAD document, not a scene: opening one needs the OCC
    // kernel, which does not exist on the headset. Say so plainly instead of
    // reporting a corrupt FCXR file.
    if (name.size() > 6 && name.compare(name.size() - 6, 6, ".FCStd") == 0) {
        setStatus("export .fcxr from FreeCAD first", true);
        return;
    }
    Document parsed;
    std::string error;
    if (!fcxrRead(data.data(), data.size(), &parsed, &error)) {
        setStatus("bad FCXR: " + error, true);
        return;
    }
    paint_.clear();
    document_.unload(&renderer_);
    if (!document_.load(parsed, &renderer_, &error)) {
        setStatus("cannot load: " + error, true);
        return;
    }
    document_.place(environment_.documentAnchor(), environment_.documentAnchorSize(), true);
    paint_.loadFromDocument(parsed, document_);
    if (parsed.vector.present) vector_.fromDocument(parsed.vector);
    currentDocumentName_ = parsed.asset.sourceDocument.empty() ? name
                                                              : parsed.asset.sourceDocument;
    // Follow the environment the desktop had open, when it named one.
    if (!parsed.scene.environment.empty() &&
        parsed.scene.environment != environment_.currentId())
        environment_.switchTo(parsed.scene.environment);
    setStatus("loaded " + currentDocumentName_);
}

void App::uploadPaint() {
    Document upload;
    if (!paint_.buildUpload(currentDocumentName_, &upload)) {
        setStatus("nothing painted yet", true);
        return;
    }
    std::vector<uint8_t> bytes;
    std::string error;
    if (!fcxrWrite(upload, &bytes, &error)) {
        setStatus("cannot package paint: " + error, true);
        return;
    }
    sync_.uploadPaint(std::move(bytes));
    setStatus("sending paint to the desktop");
}

// Packages the paint the same way as the LAN upload, but hands the bytes to
// the Java side to store on Drive instead of posting them to the desktop.
void App::uploadPaintToDrive() {
    Document upload;
    if (!paint_.buildUpload(currentDocumentName_, &upload)) {
        setStatus("nothing painted yet", true);
        return;
    }
    std::vector<uint8_t> bytes;
    std::string error;
    if (!fcxrWrite(upload, &bytes, &error)) {
        setStatus("cannot package paint: " + error, true);
        return;
    }
    std::string name = currentDocumentName_.empty() ? "paint" : currentDocumentName_;
    const size_t dot = name.find_last_of('.');
    if (dot != std::string::npos) name = name.substr(0, dot);
    jniDriveUpload(name + "_paint.fcxr", bytes.data(), bytes.size());
    setStatus("uploading paint to Drive");
}

void App::uploadVector() {
    if (vector_.pathCount() == 0) {
        setStatus("no paths to send", true);
        return;
    }
    sync_.uploadVector(vectorToJson(vector_.toDocument()).dump());
    setStatus("sending paths to the desktop");
}

void App::pollSync() {
    SyncResult result;
    while (sync_.poll(&result)) {
        switch (result.kind) {
            case SyncJobKind::Discover:
                discovered_ = result.servers;
                selectedServer_ = discovered_.empty() ? -1 : 0;
                setStatus(discovered_.empty() ? "no desktop found"
                                              : (std::to_string(discovered_.size()) +
                                                 " desktop(s) found"),
                          discovered_.empty());
                break;
            case SyncJobKind::Hello:
                if (result.ok) setStatus("desktop: " + result.body["name"].asString());
                else setStatus(result.error, true);
                break;
            case SyncJobKind::Pair:
                if (result.ok) {
                    storage_.mutableSettings().serverToken = sync_.token();
                    storage_.saveSettings();
                    setStatus("paired");
                    sync_.requestDocuments();
                } else {
                    setStatus(result.error, true);
                }
                break;
            case SyncJobKind::Documents: {
                remoteDocuments_.clear();
                const json::Value& documents = result.body["documents"];
                for (size_t i = 0; i < documents.size(); ++i)
                    remoteDocuments_.push_back(documents[i]["name"].asString());
                selectedRemote_ = remoteDocuments_.empty() ? -1 : 0;
                if (!result.ok) setStatus(result.error, true);
                break;
            }
            case SyncJobKind::Scene:
                if (result.ok) {
                    std::string path;
                    storage_.save(result.key, result.data.data(), result.data.size(), &path);
                    loadDocumentFromMemory(result.data, result.key);
                    if (!path.empty()) storage_.noteRecent(path);
                    refreshLibrary();
                } else {
                    setStatus(result.error, true);
                }
                break;
            case SyncJobKind::State:
                if (result.ok) {
                    const std::string id = result.body["environment"].asString();
                    if (!id.empty() && id != environment_.currentId()) {
                        if (!environment_.switchTo(id)) sync_.requestEnvironment(id);
                    }
                    const float scale = result.body["scale"].asFloat(0.0f);
                    if (scale > 0.0f) environment_.setUserScaleOverride(scale);
                }
                break;
            case SyncJobKind::Environment:
                if (result.ok)
                    environment_.switchToSpec(result.body.dump(), result.key);
                break;
            case SyncJobKind::Events: {
                const json::Value& events = result.body["events"];
                for (size_t i = 0; i < events.size(); ++i) {
                    const std::string type = events[i]["type"].asString();
                    if (type == "doc_changed" && !currentDocumentName_.empty()) {
                        sync_.requestScene(events[i]["doc"].asString(), 2);
                        setStatus("desktop changed, refetching");
                    } else if (type == "environment_changed") {
                        sync_.requestState();
                    }
                }
                break;
            }
            case SyncJobKind::UploadPaint:
            case SyncJobKind::UploadVector:
                setStatus(result.ok ? "the desktop applied the edit"
                                    : ("upload failed: " + result.error),
                          !result.ok);
                break;
            default:
                break;
        }
    }
}

void App::postAction(PendingAction action) {
    std::lock_guard<std::mutex> lock(actionMutex_);
    actions_.push_back(std::move(action));
}

void App::drainActions() {
    std::vector<PendingAction> pending;
    {
        std::lock_guard<std::mutex> lock(actionMutex_);
        pending.swap(actions_);
    }
    for (PendingAction& action : pending) {
        switch (action.kind) {
            case PendingAction::Kind::LoadFile:
            case PendingAction::Kind::DriveDownloaded: {
                std::string path;
                if (storage_.save(action.text, action.data.data(), action.data.size(), &path)) {
                    loadDocumentFromMemory(action.data, action.text);
                    storage_.noteRecent(path);
                    refreshLibrary();
                } else {
                    setStatus("cannot save the imported file", true);
                }
                break;
            }
            case PendingAction::Kind::DriveFiles: {
                driveFiles_.clear();
                json::ParseError error;
                const json::Value files = json::parse(action.text, &error);
                for (size_t i = 0; i < files.size(); ++i)
                    driveFiles_.push_back(files[i]["name"].asString());
                selectedDriveFile_ = driveFiles_.empty() ? -1 : 0;
                driveStatus_ = std::to_string(driveFiles_.size()) + " file(s) on Drive";
                break;
            }
            case PendingAction::Kind::DriveAuth:
                driveStatus_ = action.text;
                driveUserCode_ = action.secondary;
                driveVerificationUrl_ = action.text.find("http") != std::string::npos
                                            ? action.text
                                            : driveVerificationUrl_;
                break;
            case PendingAction::Kind::Toast:
                setStatus(action.text);
                break;
        }
    }
}

}  // namespace fcxr
