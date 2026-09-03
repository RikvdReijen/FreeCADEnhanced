// SPDX-License-Identifier: LGPL-2.1-or-later
#include "environment.h"

#include <algorithm>

#include "log.h"

namespace fcxr {
namespace {

constexpr float kFadeSpeed = 3.5f;      // 1 / seconds, so ~0.29 s each way
constexpr float kNearClip = 0.02f;
constexpr float kMinFar = 20.0f;
constexpr float kMaxFar = 5000.0f;
// Beyond this ratio a 24 bit depth buffer starts z-fighting badly.
constexpr float kMaxDepthRatio = 20000.0f;

}  // namespace

EnvironmentManager::~EnvironmentManager() { shutdown(); }

bool EnvironmentManager::init(Assets* assets, Renderer* renderer) {
    assets_ = assets;
    renderer_ = renderer;
    available_.clear();
    if (!assets_) return false;

    for (const std::string& name : assets_->list("environments")) {
        if (name.size() < 6 || name.compare(name.size() - 5, 5, ".json") != 0) continue;
        EnvironmentEntry entry;
        entry.assetPath = "environments/" + name;
        entry.id = name.substr(0, name.size() - 5);
        entry.name = entry.id;

        // Peek at the header fields without tessellating anything: the specs
        // are up to half a megabyte, but the id/name/user_scale live at the
        // top level so a full parse is still cheap compared to the geometry.
        std::string text;
        if (assets_->readText(entry.assetPath, &text)) {
            json::ParseError err;
            const json::Value root = json::parse(text.data(), text.size(), &err);
            if (err.ok && root.isObject()) {
                if (root["id"].isString()) entry.id = root["id"].asString();
                entry.name = root["name"].asString();
                if (entry.name.empty()) entry.name = entry.id;
                entry.description = root["description"].asString();
                entry.userScale = root["user_scale"].asFloat(1.0f);
            } else if (!err.ok) {
                LOGW("environment %s is not valid JSON: %s", name.c_str(),
                     err.message.c_str());
                continue;
            }
        }
        available_.push_back(std::move(entry));
    }
    std::sort(available_.begin(), available_.end(),
              [](const EnvironmentEntry& a, const EnvironmentEntry& b) {
                  return a.name < b.name;
              });
    LOGI("%zu environments available", available_.size());
    return true;
}

void EnvironmentManager::shutdown() {
    if (loader_.joinable()) loader_.join();
    pending_.reset();
    releaseGpu();
    current_.reset();
    available_.clear();
}

std::string EnvironmentManager::currentId() const {
    return current_ ? current_->id : std::string();
}

void EnvironmentManager::releaseGpu() {
    if (!renderer_) return;
    for (int handle : meshHandles_) renderer_->destroyMesh(handle);
    for (int handle : materialHandles_) renderer_->destroyMaterial(handle);
    for (int handle : textureHandles_) renderer_->destroyTexture(handle);
    meshHandles_.clear();
    materialHandles_.clear();
    textureHandles_.clear();
    specInstances_.clear();
    specLights_.clear();
    instances_.clear();
    lights_.clear();
}

bool EnvironmentManager::switchTo(const std::string& id) {
    for (const EnvironmentEntry& entry : available_) {
        if (entry.id != id) continue;
        std::string text;
        if (!assets_ || !assets_->readText(entry.assetPath, &text)) {
            LOGE("cannot read %s", entry.assetPath.c_str());
            return false;
        }
        requestedId_ = id;
        requestedText_ = std::move(text);
        fadeDirection_ = 1;
        return true;
    }
    LOGW("unknown environment '%s'", id.c_str());
    return false;
}

bool EnvironmentManager::switchToSpec(const std::string& jsonText, const std::string& id) {
    if (jsonText.empty()) return false;
    requestedId_ = id;
    requestedText_ = jsonText;
    fadeDirection_ = 1;
    return true;
}

void EnvironmentManager::beginLoad(const std::string& id, std::string text) {
    if (loader_.joinable()) loader_.join();
    auto pending = std::make_shared<PendingLoad>();
    pending->id = id;
    pending->text = std::move(text);
    pending_ = pending;
    loading_.store(true);
    // Parsing and tessellating half a megabyte of spec takes long enough to
    // drop frames, so it happens off the render thread; only the GPU uploads
    // come back to update().
    loader_ = std::thread([pending]() {
        auto spec = std::unique_ptr<EnvSpec>(new EnvSpec());
        std::string error;
        if (envSpecLoad(pending->text.data(), pending->text.size(), spec.get(), &error)) {
            envSpecFlatten(*spec, &pending->items);
            pending->spec = std::move(spec);
            pending->ok = true;
        } else {
            pending->error = error;
        }
        pending->text.clear();
        pending->text.shrink_to_fit();
        pending->done.store(true);
    });
}

void EnvironmentManager::applyLoaded(PendingLoad& pending) {
    releaseGpu();
    current_ = std::move(pending.spec);
    if (!current_) return;

    for (const std::string& warning : current_->warnings)
        LOGW("environment %s: %s", current_->id.c_str(), warning.c_str());

    // Upload one GPU mesh per distinct shape; the draw list references them,
    // so 969 printer parts become 192 buffers and are drawn instanced.
    meshHandles_.reserve(current_->meshes.size());
    for (const MeshData& mesh : current_->meshes)
        meshHandles_.push_back(renderer_->createMesh(mesh));

    materialHandles_.reserve(current_->materials.size());
    for (const EnvMaterial& material : current_->materials) {
        RenderMaterial gpu;
        gpu.baseColor = material.baseColor;
        gpu.metallic = material.metallic;
        gpu.roughness = material.roughness;
        gpu.emissive = material.emissive;
        gpu.doubleSided = false;
        if (!material.texture.empty()) {
            const int texture = renderer_->createProceduralTexture(material.texture);
            if (texture >= 0) {
                textureHandles_.push_back(texture);
                gpu.texture = texture;
            }
        }
        materialHandles_.push_back(renderer_->createMaterial(gpu));
    }

    // A default material for nodes that never inherited one.
    RenderMaterial fallback;
    const int fallbackHandle = renderer_->createMaterial(fallback);
    materialHandles_.push_back(fallbackHandle);

    specInstances_.clear();
    specInstances_.reserve(pending.items.size());
    for (const EnvDrawItem& item : pending.items) {
        RenderInstance instance;
        if (item.mesh < 0 || size_t(item.mesh) >= meshHandles_.size()) continue;
        instance.mesh = meshHandles_[size_t(item.mesh)];
        if (instance.mesh < 0) continue;
        instance.material = (item.material >= 0 &&
                             size_t(item.material) < materialHandles_.size() - 1)
                                ? materialHandles_[size_t(item.material)]
                                : fallbackHandle;
        instance.transform = item.transform;  // spec space; refreshWorld() places it
        instance.worldBounds = item.bounds;
        specInstances_.push_back(instance);
    }

    specLights_.clear();
    for (const EnvLight& light : current_->lights) {
        RenderLight gpu;
        gpu.type = light.type == EnvLight::Type::Directional
                       ? 0
                       : (light.type == EnvLight::Type::Point ? 1 : 2);
        gpu.position = light.position;
        gpu.direction = light.direction;
        gpu.color = light.color;
        gpu.intensity = light.intensity;
        gpu.cutoffCos = std::cos(degToRad(light.cutoffDeg));
        gpu.range = light.range;
        specLights_.push_back(gpu);
    }
    ambient_ = current_->ambient;
    worldDirty_ = true;
    refreshWorld();

    LOGI("environment '%s' ready: %zu draws, %zu meshes, %zu triangles",
         current_->id.c_str(), instances_.size(), current_->meshes.size(),
         current_->triangleCount());
}

// Places the spec-space draw list into the world (miniaturisation).
void EnvironmentManager::refreshWorld() {
    if (!worldDirty_) return;
    worldDirty_ = false;
    const Mat4 world = worldTransform();
    const float scale = userScale();

    instances_.resize(specInstances_.size());
    for (size_t i = 0; i < specInstances_.size(); ++i) {
        instances_[i] = specInstances_[i];
        instances_[i].transform = world * specInstances_[i].transform;
        instances_[i].worldBounds = transformAabb(world, specInstances_[i].worldBounds);
    }

    lights_.resize(specLights_.size());
    for (size_t i = 0; i < specLights_.size(); ++i) {
        lights_[i] = specLights_[i];
        lights_[i].position = transformPoint(world, specLights_[i].position);
        // The world scale is uniform, so directions are unchanged, but a
        // light's reach grows with the world.
        lights_[i].range = specLights_[i].range * scale;
    }
}

void EnvironmentManager::update(float deltaSeconds) {
    // Pick up a finished background load.
    if (pending_ && pending_->done.load()) {
        if (loader_.joinable()) loader_.join();
        if (pending_->ok) {
            applyLoaded(*pending_);
        } else {
            LOGE("environment load failed: %s", pending_->error.c_str());
        }
        pending_.reset();
        loading_.store(false);
        fadeDirection_ = -1;  // fade back in
    }

    if (fadeDirection_ > 0) {
        fade_ = std::min(1.0f, fade_ + deltaSeconds * kFadeSpeed);
        if (fade_ >= 1.0f && !loading_.load() && !requestedText_.empty()) {
            beginLoad(requestedId_, std::move(requestedText_));
            requestedText_.clear();
        }
    } else if (fadeDirection_ < 0) {
        fade_ = std::max(0.0f, fade_ - deltaSeconds * kFadeSpeed);
        if (fade_ <= 0.0f) fadeDirection_ = 0;
    }

    refreshWorld();
}

void EnvironmentManager::setUserPosition(Vec3 feet) {
    if (lengthSq(feet - userFeet_) < 1e-8f) return;
    userFeet_ = feet;
    worldDirty_ = true;
}

float EnvironmentManager::userScale() const {
    if (userScaleOverride_ > 0.0f) return userScaleOverride_;
    return current_ ? current_->userScale : 1.0f;
}

void EnvironmentManager::setUserScaleOverride(float scale) {
    const float value = scale > 0.0f ? clampf(scale, 0.05f, 200.0f) : 0.0f;
    if (value == userScaleOverride_) return;
    userScaleOverride_ = value;
    worldDirty_ = true;
}

Mat4 EnvironmentManager::worldTransform() const {
    const float scale = userScale();
    const Vec3 spawn = current_ ? current_->spawn : Vec3(0, 0, 0);
    // translate(feet) * scale(s) * translate(-spawn)
    return mat4Translate(userFeet_) * mat4Scale(Vec3(scale, scale, scale)) *
           mat4Translate(-spawn);
}

bool EnvironmentManager::anchorTransform(const std::string& name, Mat4* out) const {
    if (!current_ || !out) return false;
    const EnvAnchor* anchor = current_->anchor(name);
    if (!anchor) return false;
    *out = worldTransform() * mat4TRS(anchor->position, anchor->rotation, Vec3(1, 1, 1));
    return true;
}

Mat4 EnvironmentManager::documentAnchor() const {
    Mat4 out;
    if (anchorTransform("build_plate", &out)) return out;
    if (current_ && !current_->anchors.empty()) {
        const EnvAnchor& anchor = current_->anchors.front();
        return worldTransform() * mat4TRS(anchor.position, anchor.rotation, Vec3(1, 1, 1));
    }
    // No anchor: put the document on the floor at the spawn point, lying in
    // the XZ plane with its local +Z pointing up (the anchor convention).
    const Vec3 spawn = current_ ? current_->spawn : Vec3(0, 0, 0);
    return worldTransform() *
           mat4TRS(spawn, quatAxisAngle(Vec3(1, 0, 0), -kPi * 0.5f), Vec3(1, 1, 1));
}

Vec2 EnvironmentManager::documentAnchorSize() const {
    if (current_) {
        if (const EnvAnchor* anchor = current_->anchor("build_plate")) return anchor->size;
        if (!current_->anchors.empty()) return current_->anchors.front().size;
    }
    return Vec2(0.3f, 0.3f);
}

void EnvironmentManager::clipPlanes(float* nearZ, float* farZ) const {
    const float scale = userScale();
    float radius = 8.0f;
    if (current_) {
        const Vec3 b = current_->bounds;
        // bounds = [w, d, h] with x in [-w/2, w/2], z in [-d/2, d/2], y in [0, h]
        radius = 0.5f * std::sqrt(b.x * b.x + b.z * b.z + 4.0f * b.y * b.y);
    }
    float far = clampf(radius * scale * 2.5f, kMinFar, kMaxFar);
    float near = kNearClip;
    // Keep the depth range usable: raising the near plane costs nothing when
    // the world is huge, and prevents z-fighting across a 300 m interior.
    if (far / near > kMaxDepthRatio) near = far / kMaxDepthRatio;
    if (nearZ) *nearZ = near;
    if (farZ) *farZ = far;
}

}  // namespace fcxr
