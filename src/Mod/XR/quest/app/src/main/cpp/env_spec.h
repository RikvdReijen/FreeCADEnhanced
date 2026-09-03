// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Loader for the declarative environment specs of ARCHITECTURE.md §2.
//
// A spec is pure data: lights, materials, anchors and a node tree whose leaves
// carry one of the twelve shape primitives. This file turns that into flat
// arrays the renderer can draw, deduplicating identical shapes so repeated
// parts (bolts, extrusions, grid cells) become instanced draws.
#pragma once

#include <map>
#include <string>
#include <vector>

#include "json.h"
#include "mesh_data.h"

namespace fcxr {

struct EnvLight {
    enum class Type { Directional, Point, Spot };
    Type type = Type::Directional;
    Vec3 direction{0, -1, 0};
    Vec3 position{0, 0, 0};
    Vec3 color{1, 1, 1};
    float intensity = 1.0f;
    float cutoffDeg = 45.0f;
    float range = 4.0f;
};

struct EnvMaterial {
    std::string name;
    Vec4 baseColor{0.8f, 0.8f, 0.8f, 1.0f};  // linear
    float metallic = 0.0f;
    float roughness = 0.6f;
    Vec3 emissive{0, 0, 0};
    std::string texture;  // procedural texture id, "" for none
};

struct EnvAnchor {
    std::string name;
    Vec3 position{0, 0, 0};
    Quat rotation;
    Vec2 size{0, 0};
};

struct EnvNode {
    std::string name;
    int mesh = -1;      // index into EnvSpec::meshes, -1 for a pure transform
    int material = -1;  // index into EnvSpec::materials
    Vec3 translation{0, 0, 0};
    Quat rotation;
    Vec3 scale{1, 1, 1};
    std::vector<int> children;
    Mat4 localMatrix() const { return mat4TRS(translation, rotation, scale); }
};

struct EnvSpec {
    std::string id;
    std::string name;
    std::string description;
    int version = 1;
    float userScale = 1.0f;
    Vec3 bounds{4, 4, 3};
    Vec3 spawn{0, 0, 0};
    Vec3 ambient{0.05f, 0.05f, 0.06f};
    std::vector<EnvLight> lights;
    std::vector<EnvMaterial> materials;
    std::vector<EnvAnchor> anchors;
    std::vector<EnvNode> nodes;
    std::vector<int> roots;
    std::vector<MeshData> meshes;

    // Warnings collected while loading (unknown shapes, bad indices). The
    // loader is deliberately forgiving: a spec with one broken node still
    // renders the rest.
    std::vector<std::string> warnings;

    const EnvAnchor* anchor(const std::string& anchorName) const;
    size_t triangleCount() const;
};

// One flattened, world-space placement of a mesh.
struct EnvDrawItem {
    int mesh = -1;
    int material = -1;
    Mat4 transform;
    Aabb bounds;  // world space
};

// Parses a spec from a JSON value or from raw text.
bool envSpecParse(const json::Value& root, EnvSpec* out, std::string* error = nullptr);
bool envSpecLoad(const char* text, size_t length, EnvSpec* out, std::string* error = nullptr);

// Walks the node tree and produces the world space draw list.
void envSpecFlatten(const EnvSpec& spec, std::vector<EnvDrawItem>* out);

// Overall extents of the tessellated environment, useful for the near/far
// planes and for the environment picker's preview.
Aabb envSpecBounds(const EnvSpec& spec);

}  // namespace fcxr
