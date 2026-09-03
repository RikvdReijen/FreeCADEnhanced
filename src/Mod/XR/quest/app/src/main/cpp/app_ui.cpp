// SPDX-License-Identifier: LGPL-2.1-or-later
//
// The in-VR panels. Split out of app.cpp so both files stay readable: this one
// only describes what the user sees, app.cpp owns the state it reads.
#include <cstdio>

#include "app.h"
#include "jni_bridge.h"
#include "log.h"

namespace fcxr {
namespace {

const char* toolLabel(Tool tool) {
    switch (tool) {
        case Tool::PaintTexture: return "Paint";
        case Tool::PaintRibbon: return "Ribbon";
        case Tool::VectorDraw: return "Draw";
        case Tool::VectorEdit: return "Edit nodes";
        case Tool::Measure: return "Measure";
        default: return "Navigate";
    }
}

}  // namespace

// --------------------------------------------------------------------- ui

void App::buildUi(float deltaSeconds) {
    const HandState& pointer = input_.hand(pointerHand_);
    ui_.beginFrame(pointer, headPosition_, deltaSeconds);
    // The pointer dot sits on whatever the ray hit in the world, unless a
    // panel is closer (Ui::endFrame prefers the panel distance).
    ui_.setWorldHit(lastHit_.hit, lastHit_.position);
    buildWristPanel();
    if (screen_ != Screen::None) buildMainPanel();
    ui_.endFrame(overlay_);
}

// A small always-visible panel strapped to the non-pointing wrist.
void App::buildWristPanel() {
    const Hand wrist = pointerHand_ == Hand::Right ? Hand::Left : Hand::Right;
    const HandState& hand = input_.hand(wrist);
    if (!hand.active) return;

    // Sit the panel just above the back of the hand, facing the user.
    const Vec3 up = rotate(hand.gripOrientation, Vec3(0, 1, 0));
    const Vec3 forward = rotate(hand.gripOrientation, Vec3(0, 0, -1));
    const Vec3 origin = hand.gripPosition + up * 0.045f + forward * 0.02f;
    const Vec3 toEye = normalize(headPosition_ - origin);
    Vec3 right = cross(Vec3(0, 1, 0), toEye);
    if (lengthSq(right) < 1e-5f) right = Vec3(1, 0, 0);
    right = normalize(right);
    const Vec3 panelUp = normalize(cross(toEye, right));
    Mat4 transform;
    for (int r = 0; r < 3; ++r) {
        transform.at(r, 0) = right[r];
        transform.at(r, 1) = panelUp[r];
        transform.at(r, 2) = toEye[r];
        transform.at(r, 3) = origin[r];
    }

    ui_.beginPanel("", transform, Vec2(0.15f, 0.10f));
    ui_.text(toolLabel(tool_));
    ui_.text(statusText_, statusWarning_);
    if (sync_.state() == SyncState::Busy) ui_.progressBar(sync_.progress(), ui_.theme().accent);
    ui_.beginRow(2);
    if (ui_.button("Menu")) screen_ = screen_ == Screen::None ? Screen::Tools : Screen::None;
    if (ui_.button("Undo")) {
        if (tool_ == Tool::PaintRibbon) paint_.undoRibbon();
        else paint_.undo();
    }
    ui_.endRow();
    ui_.endPanel();
}

void App::buildMainPanel() {
    // The main panel floats in front of the user, a comfortable arm away.
    const Vec3 forward = headForward();
    const Vec3 origin = headPosition_ + forward * 0.75f - Vec3(0, 0.12f, 0);
    const Vec3 toEye = normalize(headPosition_ - origin);
    Vec3 right = cross(Vec3(0, 1, 0), toEye);
    if (lengthSq(right) < 1e-5f) right = Vec3(1, 0, 0);
    right = normalize(right);
    const Vec3 up = normalize(cross(toEye, right));
    Mat4 transform;
    for (int r = 0; r < 3; ++r) {
        transform.at(r, 0) = right[r];
        transform.at(r, 1) = up[r];
        transform.at(r, 2) = toEye[r];
        transform.at(r, 3) = origin[r];
    }

    const Vec2 size(0.42f, 0.52f);
    switch (screen_) {
        case Screen::Tools: {
            ui_.beginPanel("FreeCAD XR", transform, size);
            ui_.text(currentDocumentName_.empty() ? "no document loaded"
                                                  : currentDocumentName_,
                     currentDocumentName_.empty());
            ui_.separator();
            ui_.heading("Tool");
            ui_.beginRow(3);
            if (ui_.button("Navigate")) tool_ = Tool::Navigate;
            if (ui_.button("Paint")) tool_ = Tool::PaintTexture;
            if (ui_.button("Ribbon")) tool_ = Tool::PaintRibbon;
            ui_.endRow();
            ui_.beginRow(3);
            if (ui_.button("Draw")) tool_ = Tool::VectorDraw;
            if (ui_.button("Nodes")) tool_ = Tool::VectorEdit;
            if (ui_.button("Measure")) tool_ = Tool::Measure;
            ui_.endRow();
            ui_.separator();
            ui_.beginRow(2);
            if (ui_.button("Colour")) screen_ = Screen::Colour;
            if (ui_.button("Layers")) screen_ = Screen::Layers;
            ui_.endRow();
            ui_.beginRow(2);
            if (ui_.button("Library")) {
                refreshLibrary();
                screen_ = Screen::Library;
            }
            if (ui_.button("Rooms")) screen_ = Screen::Environments;
            ui_.endRow();
            ui_.beginRow(2);
            if (ui_.button("Desktop")) screen_ = Screen::Sync;
            if (ui_.button("Drive")) {
                jniDriveListFiles();
                screen_ = Screen::Drive;
            }
            ui_.endRow();
            ui_.separator();
            ui_.beginRow(2);
            if (ui_.button("Send paint")) uploadPaint();
            if (ui_.button("Send paths")) uploadVector();
            ui_.endRow();
            ui_.separator();
            bool passthrough = xr_.passthroughEnabled();
            if (ui_.toggle("Passthrough", &passthrough) && xr_.passthroughSupported()) {
                xr_.setPassthrough(passthrough);
                storage_.mutableSettings().passthrough = passthrough;
                storage_.saveSettings();
            }
            float scale = environment_.userScale();
            if (ui_.slider("World scale", &scale, 1.0f, 40.0f))
                environment_.setUserScaleOverride(scale);
            if (ui_.button("Reset scale")) environment_.setUserScaleOverride(0.0f);
            if (ui_.button("Recentre")) {
                xr_.recenter();
                playerOffset_ = Vec3(0, 0, 0);
            }
            ui_.endPanel();
            break;
        }
        case Screen::Colour: {
            ui_.beginPanel("Colour and brush", transform, size);
            Vec4 color = paint_.brush().color;
            if (ui_.colorWheel(&color, 0.20f)) paint_.brush().color = color;
            ui_.slider("Radius (mm)", &paint_.brush().radius, 0.001f, 0.08f);
            ui_.slider("Hardness", &paint_.brush().hardness, 0.0f, 0.99f);
            ui_.slider("Flow", &paint_.brush().flow, 0.05f, 1.0f);
            ui_.slider("Alpha", &paint_.brush().color.w, 0.05f, 1.0f);
            ui_.beginRow(4);
            if (ui_.button("Normal")) paint_.brush().blend = BlendMode::Normal;
            if (ui_.button("Mult")) paint_.brush().blend = BlendMode::Multiply;
            if (ui_.button("Add")) paint_.brush().blend = BlendMode::Add;
            if (ui_.button("Erase")) paint_.brush().blend = BlendMode::Erase;
            ui_.endRow();
            if (ui_.button("Back")) screen_ = Screen::Tools;
            ui_.endPanel();
            break;
        }
        case Screen::Layers: {
            ui_.beginPanel("Layers", transform, size);
            if (activeTarget_ < 0 || size_t(activeTarget_) >= paint_.targets().size()) {
                ui_.text("paint something first", true);
            } else {
                PaintTargetData& target = paint_.targets()[size_t(activeTarget_)];
                ui_.text(target.fcName, true);
                for (size_t i = 0; i < target.layers.size(); ++i) {
                    PaintLayerData& layer = target.layers[i];
                    char detail[48];
                    std::snprintf(detail, sizeof(detail), "%s %.0f%%",
                                  blendModeName(layer.blend), layer.opacity * 100.0f);
                    if (ui_.listItem(layer.name, int(i) == target.activeLayer, detail))
                        paint_.setActiveLayer(activeTarget_, int(i));
                }
                ui_.separator();
                if (target.activeLayer >= 0 &&
                    size_t(target.activeLayer) < target.layers.size()) {
                    PaintLayerData& layer = target.layers[size_t(target.activeLayer)];
                    float opacity = layer.opacity;
                    if (ui_.slider("Opacity", &opacity, 0.0f, 1.0f))
                        paint_.setLayerOpacity(activeTarget_, target.activeLayer, opacity);
                    bool visible = layer.visible;
                    if (ui_.toggle("Visible", &visible))
                        paint_.setLayerVisible(activeTarget_, target.activeLayer, visible);
                }
                ui_.beginRow(2);
                if (ui_.button("Add layer")) paint_.addLayer(activeTarget_, "");
                if (ui_.button("Delete"))
                    paint_.removeLayer(activeTarget_,
                                       paint_.targets()[size_t(activeTarget_)].activeLayer);
                ui_.endRow();
            }
            ui_.separator();
            ui_.beginRow(2);
            if (ui_.button("Send to desktop")) uploadPaint();
            if (ui_.button("Back")) screen_ = Screen::Tools;
            ui_.endRow();
            ui_.endPanel();
            break;
        }
        case Screen::Environments: {
            ui_.beginPanel("Rooms", transform, size);
            const std::string current = environment_.currentId();
            for (const EnvironmentEntry& entry : environment_.available()) {
                char detail[32];
                std::snprintf(detail, sizeof(detail), "1:%.0f", double(entry.userScale));
                if (ui_.listItem(entry.name, entry.id == current, detail)) {
                    if (environment_.switchTo(entry.id)) {
                        storage_.mutableSettings().environment = entry.id;
                        storage_.saveSettings();
                        setStatus("loading " + entry.name);
                    }
                }
            }
            if (environment_.loading()) ui_.progressBar(0.5f, ui_.theme().accent);
            ui_.separator();
            if (ui_.button("Back")) screen_ = Screen::Tools;
            ui_.endPanel();
            break;
        }
        case Screen::Library: {
            ui_.beginPanel("Library", transform, size);
            if (library_.empty()) ui_.text("no .fcxr files on the headset", true);
            for (size_t i = 0; i < library_.size() && i < 10; ++i) {
                char detail[32];
                std::snprintf(detail, sizeof(detail), "%.1f MB",
                              double(library_[i].bytes) / (1024.0 * 1024.0));
                if (ui_.listItem(library_[i].title, int(i) == selectedLibrary_, detail))
                    selectedLibrary_ = int(i);
            }
            ui_.separator();
            ui_.beginRow(2);
            if (ui_.button("Open", selectedLibrary_ >= 0) && selectedLibrary_ >= 0)
                loadDocumentFromLibrary(library_[size_t(selectedLibrary_)].path);
            if (ui_.button("Import file")) jniOpenFilePicker();
            ui_.endRow();
            ui_.beginRow(2);
            if (ui_.button("Delete", selectedLibrary_ >= 0) && selectedLibrary_ >= 0) {
                storage_.remove(library_[size_t(selectedLibrary_)].path);
                selectedLibrary_ = -1;
                refreshLibrary();
            }
            if (ui_.button("Back")) screen_ = Screen::Tools;
            ui_.endRow();
            ui_.endPanel();
            break;
        }
        case Screen::Sync: {
            ui_.beginPanel("Desktop", transform, size);
            ui_.text(sync_.statusText(), sync_.state() == SyncState::Failed);
            const std::string host = sync_.serverAddress();
            ui_.text(host.empty() ? "no server" : (host + ":" + std::to_string(sync_.serverPort())),
                     true);
            ui_.separator();
            if (ui_.button("Search the network")) sync_.discover(900);
            for (size_t i = 0; i < discovered_.size(); ++i) {
                if (ui_.listItem(discovered_[i].name.empty() ? discovered_[i].address
                                                             : discovered_[i].name,
                                 int(i) == selectedServer_, discovered_[i].address)) {
                    selectedServer_ = int(i);
                    sync_.setServer(discovered_[i].address, discovered_[i].port);
                    storage_.mutableSettings().serverHost = discovered_[i].address;
                    storage_.mutableSettings().serverPort = discovered_[i].port;
                    storage_.mutableSettings().serverName = discovered_[i].name;
                    storage_.saveSettings();
                    sync_.hello();
                }
            }
            ui_.separator();
            ui_.heading("Pairing code " + pairingCode_);
            ui_.beginRow(4);
            if (ui_.button("<")) pairingDigit_ = (pairingDigit_ + 5) % 6;
            if (ui_.button("-")) {
                char& digit = pairingCode_[size_t(pairingDigit_)];
                digit = digit == '0' ? '9' : char(digit - 1);
            }
            if (ui_.button("+")) {
                char& digit = pairingCode_[size_t(pairingDigit_)];
                digit = digit == '9' ? '0' : char(digit + 1);
            }
            if (ui_.button(">")) pairingDigit_ = (pairingDigit_ + 1) % 6;
            ui_.endRow();
            if (ui_.button("Pair")) sync_.pair(pairingCode_, "Quest 3");
            ui_.separator();
            ui_.beginRow(2);
            if (ui_.button("Documents")) sync_.requestDocuments();
            if (ui_.button("Follow desktop")) {
                sync_.requestState();
                sync_.startEvents(sync_.lastEventSeq());
            }
            ui_.endRow();
            for (size_t i = 0; i < remoteDocuments_.size() && i < 6; ++i) {
                if (ui_.listItem(remoteDocuments_[i], int(i) == selectedRemote_))
                    selectedRemote_ = int(i);
            }
            ui_.beginRow(2);
            if (ui_.button("Fetch", selectedRemote_ >= 0) && selectedRemote_ >= 0) {
                sync_.requestScene(remoteDocuments_[size_t(selectedRemote_)], 2);
                setStatus("fetching scene");
            }
            if (ui_.button("Back")) screen_ = Screen::Tools;
            ui_.endRow();
            ui_.endPanel();
            break;
        }
        case Screen::Drive: {
            ui_.beginPanel("Google Drive", transform, size);
            ui_.text(driveStatus_, driveUserCode_.empty());
            if (!driveUserCode_.empty()) {
                ui_.heading(driveUserCode_);
                ui_.text(driveVerificationUrl_, true);
                ui_.text("enter the code on your phone", true);
            }
            ui_.separator();
            for (size_t i = 0; i < driveFiles_.size() && i < 8; ++i) {
                if (ui_.listItem(driveFiles_[i], int(i) == selectedDriveFile_))
                    selectedDriveFile_ = int(i);
            }
            ui_.separator();
            ui_.beginRow(2);
            if (ui_.button("Sign in")) jniDriveSignIn();
            if (ui_.button("Refresh")) jniDriveListFiles();
            ui_.endRow();
            ui_.beginRow(2);
            if (ui_.button("Download", selectedDriveFile_ >= 0) && selectedDriveFile_ >= 0)
                jniDriveDownload(driveFiles_[size_t(selectedDriveFile_)]);
            if (ui_.button("Upload paint")) uploadPaintToDrive();
            ui_.endRow();
            if (ui_.button("Back")) screen_ = Screen::Tools;
            ui_.endPanel();
            break;
        }
        default:
            break;
    }
}

}  // namespace fcxr
