// SPDX-License-Identifier: LGPL-2.1-or-later
//
// The application: owns every subsystem and runs one frame at a time.
//
// Threading model
//   * the render thread (started by MainActivity) does OpenXR, GL and all of
//     the state below;
//   * the sync client owns one worker thread for HTTP;
//   * the environment loader parses and tessellates a spec on a scratch thread
//     and hands the meshes back for upload;
//   * Java calls in through jni_bridge.cpp, which only ever enqueues work.
#pragma once

#include <mutex>
#include <string>
#include <vector>

#include "assets.h"
#include "document.h"
#include "environment.h"
#include "input.h"
#include "paint.h"
#include "storage.h"
#include "sync_client.h"
#include "ui.h"
#include "vector_edit.h"
#include "xr_session.h"

struct AAssetManager;

namespace fcxr {

enum class Tool { Navigate, PaintTexture, PaintRibbon, VectorDraw, VectorEdit, Measure };
enum class Screen { None, Tools, Environments, Library, Sync, Drive, Layers, Colour, About };

// Something the app was asked to do from another thread (JNI, mostly).
struct PendingAction {
    enum class Kind { LoadFile, DriveFiles, DriveAuth, DriveDownloaded, Toast };
    Kind kind = Kind::Toast;
    std::string text;       // file name / message / JSON
    std::string secondary;  // verification URL
    std::vector<uint8_t> data;
};

class App {
public:
    // Called once on the render thread. `activity` is a global reference to
    // the Java MainActivity; the caller keeps ownership of it.
    bool init(JavaVM* vm, jobject activity, AAssetManager* assetManager,
              const std::string& filesDir, const std::string& cacheDir);
    void shutdown();
    // Returns false when the app should exit.
    bool frame();
    bool running() const { return running_; }
    void requestExit() { running_ = false; }

    // Called from the JNI bridge (any thread).
    void postAction(PendingAction action);
    Storage& storage() { return storage_; }

private:
    void drainActions();
    void update(float deltaSeconds);
    void render(const FrameInfo& info);
    void buildUi(float deltaSeconds);
    void buildWristPanel();
    void buildMainPanel();
    void handleLocomotion(float deltaSeconds);
    void handleTools();
    void pollSync();

    void loadDocumentFromMemory(const std::vector<uint8_t>& data, const std::string& name);
    void loadDocumentFromLibrary(const std::string& path);
    void uploadPaint();
    void uploadPaintToDrive();
    void uploadVector();
    void refreshLibrary();
    void setStatus(const std::string& message, bool warning = false);

    // Head pose helpers.
    Vec3 headPosition() const { return headPosition_; }
    Vec3 headForward() const;

    bool running_ = true;
    bool initialised_ = false;

    EglContext egl_;
    XrSessionManager xr_;
    Renderer renderer_;
    InputSystem input_;
    Assets assets_;
    Storage storage_;
    EnvironmentManager environment_;
    DocumentScene document_;
    PaintSystem paint_;
    VectorEditor vector_;
    SyncClient sync_;
    Ui ui_;
    OverlayBuffer overlay_;
    EyeTarget eyeTargets_[2];

    // frame state
    double lastFrameTime_ = 0.0;
    Vec3 headPosition_{0, 1.6f, 0};
    Quat headOrientation_;
    Vec3 playerOffset_{0, 0, 0};  // locomotion, in app space
    float playerYaw_ = 0.0f;
    float snapCooldown_ = 0.0f;

    // interaction
    Tool tool_ = Tool::Navigate;
    Screen screen_ = Screen::Tools;
    Hand pointerHand_ = Hand::Right;
    DocumentHit lastHit_;
    int selectedVectorPath_ = -1;
    int selectedVectorNode_ = -1;
    bool draggingHandle_ = false;
    bool draggingHandleIn_ = false;
    int activeTarget_ = -1;
    int activeLayer_ = 0;

    // library / sync
    std::vector<LibraryEntry> library_;
    std::vector<std::string> remoteDocuments_;
    std::string currentDocumentName_;
    std::string statusText_ = "FreeCAD XR";
    bool statusWarning_ = false;
    float statusTimer_ = 0.0f;
    int selectedLibrary_ = -1;
    int selectedRemote_ = -1;
    std::vector<ServerInfo> discovered_;
    int selectedServer_ = -1;
    std::string pairingCode_ = "000000";
    int pairingDigit_ = 0;

    // Google Drive, driven by the Java side.
    std::string driveUserCode_;
    std::string driveVerificationUrl_;
    std::string driveStatus_ = "not signed in";
    std::vector<std::string> driveFiles_;
    int selectedDriveFile_ = -1;

    std::mutex actionMutex_;
    std::vector<PendingAction> actions_;
};

// The single app instance, for the JNI bridge.
App* appInstance();
void setAppInstance(App* app);

}  // namespace fcxr
