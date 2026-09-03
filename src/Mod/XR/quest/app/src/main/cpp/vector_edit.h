// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Vector mode: cubic Bezier paths drawn on a working plane, with node and
// handle editing, serialising to the §4 vector document that
// `POST /api/v1/vector` turns into Draft geometry.
//
// Points are held in plane-local metres. §4 stores them in document units, so
// export divides by `unit_scale` (0.001 -> millimetres) exactly once, at the
// boundary.
#pragma once

#include <string>
#include <vector>

#include "fcxr.h"
#include "renderer.h"

namespace fcxr {

class VectorEditor {
public:
    // The working plane: `origin` is a world point, `rotation` orients it so
    // local +Z is the plane normal (the same convention as an anchor).
    void setPlane(Vec3 origin, const Quat& rotation);
    Vec3 planeOrigin() const { return origin_; }
    Quat planeRotation() const { return rotation_; }
    Vec3 planeNormal() const { return rotate(rotation_, Vec3(0, 0, 1)); }

    // Plane <-> world.
    Vec3 toWorld(Vec2 local) const;
    Vec2 toPlane(Vec3 world) const;
    // Intersects a ray with the working plane. Returns false when it misses.
    bool rayToPlane(Vec3 rayOrigin, Vec3 direction, Vec2* local, Vec3* world) const;

    // ---- editing ---------------------------------------------------------
    int newPath();                       // returns the path index
    int activePath() const { return activePath_; }
    void setActivePath(int path) { activePath_ = path; }
    // Appends a node to the active path (creating one if needed).
    int appendNode(Vec2 point);
    void closeActivePath(bool closed = true);
    void finishPath();                   // deselect, keeping the geometry

    // Picks the nearest node within `radius` metres. Returns false if none.
    bool pickNode(Vec2 point, float radius, int* path, int* node) const;
    // Picks a handle (in or out) of a node within `radius`.
    bool pickHandle(Vec2 point, float radius, int* path, int* node, bool* isIn) const;

    void moveNode(int path, int node, Vec2 point);
    void moveHandle(int path, int node, bool isIn, Vec2 handleTip);
    void setNodeType(int path, int node, VectorNodeType type);
    void deleteNode(int path, int node);
    void deletePath(int path);
    void clear();

    size_t pathCount() const { return paths_.size(); }
    const std::vector<VectorPath>& paths() const { return paths_; }

    Vec4& strokeColor() { return strokeColor_; }
    float& strokeWidth() { return strokeWidth_; }
    std::string& target() { return target_; }

    // ---- display ---------------------------------------------------------
    // Draws the plane grid, the paths and (optionally) the node handles.
    void buildGeometry(OverlayBuffer& overlay, bool showHandles, int selectedPath,
                       int selectedNode, Vec3 eyePosition) const;

    // ---- serialisation ---------------------------------------------------
    VectorDoc toDocument(double unitScale = 0.001) const;
    void fromDocument(const VectorDoc& document);

private:
    // Flattens one path into world space points.
    void flattenPath(const VectorPath& path, std::vector<Vec3>* out) const;

    Vec3 origin_{0, 1.0f, 0};
    Quat rotation_;
    std::vector<VectorPath> paths_;
    int activePath_ = -1;
    Vec4 strokeColor_{0.1f, 0.7f, 1.0f, 1.0f};
    float strokeWidth_ = 0.5f;  // document units (mm), as §4 stores it
    std::string target_ = "draft";
    int nextId_ = 1;
};

}  // namespace fcxr
