// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Environment runtime: which machine interior the user is standing in, the
// cross-fade between them, and the miniaturisation that makes a 350 mm printer
// chamber feel like a room.
//
// Miniaturisation
// ---------------
// The specs are authored at real size (ARCHITECTURE.md §2: `bounds` is the
// interior size in metres). `user_scale` says how much smaller than reality
// the user is, so the world is drawn `user_scale` times larger about the
// user's feet:
//
//     world = translate(feet) * scale(user_scale) * translate(-spawn)
//
// The headset's IPD is physical and must not be touched: stereo disparity of a
// world scaled by S is exactly the disparity a person 1/S tall would see, which
// is what sells the effect. What does have to move is the far clip plane, which
// grows with S, and with it the near plane if the depth buffer ratio would
// otherwise collapse.
#pragma once

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "assets.h"
#include "env_spec.h"
#include "renderer.h"

namespace fcxr {

struct EnvironmentEntry {
    std::string id;
    std::string name;
    std::string description;
    std::string assetPath;
    float userScale = 1.0f;
};

class EnvironmentManager {
public:
    ~EnvironmentManager();

    // Scans assets/environments/ for specs. Only the id/name of each is read
    // at this point; geometry is built when one is selected.
    bool init(Assets* assets, Renderer* renderer);
    void shutdown();

    const std::vector<EnvironmentEntry>& available() const { return available_; }
    const EnvSpec* current() const { return current_.get(); }
    std::string currentId() const;
    bool loading() const { return loading_.load(); }

    // Starts a fade-to-black; the new environment is swapped in at the
    // midpoint. Returns false if `id` is unknown.
    bool switchTo(const std::string& id);
    // Loads a spec that arrived over the network instead of from assets.
    bool switchToSpec(const std::string& jsonText, const std::string& id);

    // Advances the fade and picks up a finished background load. Must be
    // called on the render thread: it uploads meshes to the GPU.
    void update(float deltaSeconds);

    // 0 = clear, 1 = fully black.
    float fadeAmount() const { return fade_; }

    // Draw data for the current environment, in world space.
    const std::vector<RenderInstance>& instances() const { return instances_; }
    const std::vector<RenderLight>& lights() const { return lights_; }
    Vec3 ambient() const { return ambient_; }

    // ---- placement -------------------------------------------------------
    // The user's feet in app space (x/z from the head, y = floor).
    void setUserPosition(Vec3 feet);
    Vec3 userPosition() const { return userFeet_; }
    float userScale() const;
    void setUserScaleOverride(float scale);  // 0 restores the spec's value

    // Spec space -> world space.
    Mat4 worldTransform() const;
    // World transform of a named anchor (its +Z is the surface normal and its
    // `size` spans local X and Y, matching FreeCAD's placement convention).
    bool anchorTransform(const std::string& name, Mat4* out) const;
    // The anchor a loaded document should sit on: `build_plate` when the spec
    // has one, else the first anchor, else the spawn point.
    Mat4 documentAnchor() const;
    Vec2 documentAnchorSize() const;

    // Near/far planes for the current scale, in metres.
    void clipPlanes(float* nearZ, float* farZ) const;

private:
    struct PendingLoad {
        std::string id;
        std::string text;
        std::unique_ptr<EnvSpec> spec;
        std::vector<EnvDrawItem> items;
        std::atomic<bool> done{false};
        bool ok = false;
        std::string error;
    };

    void beginLoad(const std::string& id, std::string text);
    void applyLoaded(PendingLoad& pending);
    void releaseGpu();

    Assets* assets_ = nullptr;
    Renderer* renderer_ = nullptr;
    std::vector<EnvironmentEntry> available_;

    void refreshWorld();

    std::unique_ptr<EnvSpec> current_;
    std::vector<int> meshHandles_;
    std::vector<int> materialHandles_;
    std::vector<int> textureHandles_;
    // Instances and lights are kept twice: once as the spec authored them and
    // once transformed into world space. The world copy is rebuilt only when
    // the user's position or the scale changes, not every frame.
    std::vector<RenderInstance> specInstances_;
    std::vector<RenderLight> specLights_;
    std::vector<RenderInstance> instances_;
    std::vector<RenderLight> lights_;
    bool worldDirty_ = true;
    Vec3 ambient_{0.05f, 0.05f, 0.06f};

    Vec3 userFeet_{0, 0, 0};
    float userScaleOverride_ = 0.0f;

    // fade state machine
    float fade_ = 0.0f;
    int fadeDirection_ = 0;  // -1 fading in, +1 fading out, 0 idle
    std::string requestedId_;
    std::string requestedText_;

    std::shared_ptr<PendingLoad> pending_;
    std::thread loader_;
    std::atomic<bool> loading_{false};
};

}  // namespace fcxr
