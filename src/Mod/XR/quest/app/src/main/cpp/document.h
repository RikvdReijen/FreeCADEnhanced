// SPDX-License-Identifier: LGPL-2.1-or-later
//
// A loaded FCXR document: GPU meshes for drawing, CPU meshes for picking and
// painting, and the placement that sits it on the environment's anchor.
//
// Placement convention (ARCHITECTURE.md §2 anchors): an anchor's local +Z is
// its surface normal and its `size` spans local X and Y, which is FreeCAD's
// own placement convention. Documents are Y-up (the exporter inserts a
// synthetic root rotated -90 degrees about X), so the document is rotated +90
// degrees about X to stand on the anchor plane, centred on it, and optionally
// scaled down to fit.
#pragma once

#include <string>
#include <vector>

#include "fcxr.h"
#include "renderer.h"

namespace fcxr {

struct DocumentPrimitive {
    int meshHandle = -1;
    int materialHandle = -1;
    int nodeIndex = -1;
    std::string fcName;
    Mat4 nodeTransform;   // document space
    MeshData cpu;         // kept for ray picking and paint UV lookup
    Aabb localBounds;
    int paintTexture = -1;  // texture handle of the painted layer stack, or -1
};

struct DocumentHit {
    bool hit = false;
    int primitive = -1;
    float distance = 0.0f;
    Vec3 position{0, 0, 0};   // world space
    Vec3 normal{0, 1, 0};     // world space
    Vec2 uv{0, 0};
    uint32_t triangle = 0;
};

class DocumentScene {
public:
    // Uploads the document's geometry. `renderer` keeps ownership of the GPU
    // objects; call unload() before loading another document.
    bool load(const Document& document, Renderer* renderer, std::string* error = nullptr);
    void unload(Renderer* renderer);
    bool loaded() const { return !primitives_.empty(); }

    // Places the document on an anchor. `fit` scales it down (never up) so its
    // footprint fits `anchorSize`, which is what makes a 400 mm part sit
    // sensibly on a 256 mm build plate.
    void place(const Mat4& anchorWorld, Vec2 anchorSize, bool fit);
    // Extra user scale applied on top of the fit, from the UI.
    void setUserScale(float scale);
    float userScale() const { return userScale_; }
    Mat4 placement() const { return placement_; }

    const std::vector<RenderInstance>& instances() const { return instances_; }
    std::vector<DocumentPrimitive>& primitives() { return primitives_; }
    const std::vector<DocumentPrimitive>& primitives() const { return primitives_; }
    Aabb bounds() const { return bounds_; }
    const std::string& title() const { return title_; }

    // Closest triangle hit by a world space ray. O(triangles) with an AABB
    // reject per primitive: documents that reach the headset are decimated by
    // the desktop exporter's LOD, so this stays under a millisecond for one
    // ray per hand per frame.
    bool raycast(Vec3 origin, Vec3 direction, float maxDistance, DocumentHit* out) const;

    // Assigns the painted texture of a primitive (paint.cpp owns the pixels).
    void setPaintTexture(int primitive, int textureHandle, Renderer* renderer);

private:
    void rebuildInstances();

    std::vector<DocumentPrimitive> primitives_;
    std::vector<RenderInstance> instances_;
    std::vector<int> materialHandles_;
    std::vector<int> textureHandles_;
    Aabb bounds_;
    Mat4 anchor_;
    Mat4 placement_;
    Vec2 anchorSize_{0.3f, 0.3f};
    float fitScale_ = 1.0f;
    float userScale_ = 1.0f;
    std::string title_;
};

}  // namespace fcxr
