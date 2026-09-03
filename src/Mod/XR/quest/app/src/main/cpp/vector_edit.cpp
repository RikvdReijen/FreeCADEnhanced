// SPDX-License-Identifier: LGPL-2.1-or-later
#include "vector_edit.h"

#include <algorithm>

#include "log.h"

namespace fcxr {
namespace {

constexpr int kBezierSegments = 16;
constexpr float kHandleSize = 0.012f;

Vec2 cubicPoint(Vec2 p0, Vec2 c0, Vec2 c1, Vec2 p1, float t) {
    const float u = 1.0f - t;
    const float a = u * u * u;
    const float b = 3.0f * u * u * t;
    const float c = 3.0f * u * t * t;
    const float d = t * t * t;
    return Vec2(a * p0.x + b * c0.x + c * c1.x + d * p1.x,
                a * p0.y + b * c0.y + c * c1.y + d * p1.y);
}

}  // namespace

void VectorEditor::setPlane(Vec3 origin, const Quat& rotation) {
    origin_ = origin;
    rotation_ = normalize(rotation);
}

Vec3 VectorEditor::toWorld(Vec2 local) const {
    return origin_ + rotate(rotation_, Vec3(local.x, local.y, 0.0f));
}

Vec2 VectorEditor::toPlane(Vec3 world) const {
    const Vec3 local = rotate(conjugate(rotation_), world - origin_);
    return Vec2(local.x, local.y);
}

bool VectorEditor::rayToPlane(Vec3 rayOrigin, Vec3 direction, Vec2* local, Vec3* world) const {
    float t = 0.0f;
    if (!rayPlane(rayOrigin, normalize(direction), origin_, planeNormal(), &t)) return false;
    const Vec3 hit = rayOrigin + normalize(direction) * t;
    if (world) *world = hit;
    if (local) *local = toPlane(hit);
    return true;
}

int VectorEditor::newPath() {
    VectorPath path;
    path.id = "p" + std::to_string(nextId_++);
    path.strokeColor = strokeColor_;
    path.strokeWidth = strokeWidth_;
    path.target = target_;
    paths_.push_back(std::move(path));
    activePath_ = int(paths_.size()) - 1;
    return activePath_;
}

int VectorEditor::appendNode(Vec2 point) {
    if (activePath_ < 0 || size_t(activePath_) >= paths_.size()) newPath();
    VectorPath& path = paths_[size_t(activePath_)];
    if (path.nodes.size() >= 512) return -1;
    VectorNode node;
    node.point = point;
    node.type = VectorNodeType::Corner;
    // Give the new node handles along the incoming direction so the path is
    // immediately editable as a curve rather than a polyline.
    if (!path.nodes.empty()) {
        const Vec2 previous = path.nodes.back().point;
        const Vec2 delta = point - previous;
        const float distance = length(delta);
        if (distance > 1e-5f) {
            const Vec2 direction = delta * (1.0f / distance);
            const float handle = distance * 0.25f;
            node.hasIn = true;
            node.in = direction * -handle;
            VectorNode& last = path.nodes.back();
            last.hasOut = true;
            last.out = direction * handle;
        }
    }
    path.nodes.push_back(node);
    return int(path.nodes.size()) - 1;
}

void VectorEditor::closeActivePath(bool closed) {
    if (activePath_ < 0 || size_t(activePath_) >= paths_.size()) return;
    paths_[size_t(activePath_)].closed = closed;
}

void VectorEditor::finishPath() {
    if (activePath_ >= 0 && size_t(activePath_) < paths_.size() &&
        paths_[size_t(activePath_)].nodes.size() < 2)
        paths_.erase(paths_.begin() + activePath_);
    activePath_ = -1;
}

bool VectorEditor::pickNode(Vec2 point, float radius, int* path, int* node) const {
    float best = radius;
    bool found = false;
    for (size_t p = 0; p < paths_.size(); ++p) {
        for (size_t n = 0; n < paths_[p].nodes.size(); ++n) {
            const float distance = length(paths_[p].nodes[n].point - point);
            if (distance > best) continue;
            best = distance;
            found = true;
            if (path) *path = int(p);
            if (node) *node = int(n);
        }
    }
    return found;
}

bool VectorEditor::pickHandle(Vec2 point, float radius, int* path, int* node,
                              bool* isIn) const {
    float best = radius;
    bool found = false;
    for (size_t p = 0; p < paths_.size(); ++p) {
        for (size_t n = 0; n < paths_[p].nodes.size(); ++n) {
            const VectorNode& item = paths_[p].nodes[n];
            if (item.hasIn) {
                const float distance = length(item.point + item.in - point);
                if (distance < best) {
                    best = distance;
                    found = true;
                    if (path) *path = int(p);
                    if (node) *node = int(n);
                    if (isIn) *isIn = true;
                }
            }
            if (item.hasOut) {
                const float distance = length(item.point + item.out - point);
                if (distance < best) {
                    best = distance;
                    found = true;
                    if (path) *path = int(p);
                    if (node) *node = int(n);
                    if (isIn) *isIn = false;
                }
            }
        }
    }
    return found;
}

void VectorEditor::moveNode(int path, int node, Vec2 point) {
    if (path < 0 || size_t(path) >= paths_.size()) return;
    VectorPath& p = paths_[size_t(path)];
    if (node < 0 || size_t(node) >= p.nodes.size()) return;
    p.nodes[size_t(node)].point = point;  // handles are relative, so they follow
}

void VectorEditor::moveHandle(int path, int node, bool isIn, Vec2 handleTip) {
    if (path < 0 || size_t(path) >= paths_.size()) return;
    VectorPath& p = paths_[size_t(path)];
    if (node < 0 || size_t(node) >= p.nodes.size()) return;
    VectorNode& item = p.nodes[size_t(node)];
    const Vec2 relative = handleTip - item.point;
    if (isIn) {
        item.hasIn = true;
        item.in = relative;
    } else {
        item.hasOut = true;
        item.out = relative;
    }
    // Mirror the opposite handle for smooth and symmetric nodes.
    if (item.type == VectorNodeType::Symmetric) {
        if (isIn) {
            item.hasOut = true;
            item.out = relative * -1.0f;
        } else {
            item.hasIn = true;
            item.in = relative * -1.0f;
        }
    } else if (item.type == VectorNodeType::Smooth) {
        const float distance = length(relative);
        if (distance > 1e-6f) {
            const Vec2 direction = relative * (1.0f / distance);
            if (isIn && item.hasOut) {
                item.out = direction * -length(item.out);
            } else if (!isIn && item.hasIn) {
                item.in = direction * -length(item.in);
            }
        }
    }
}

void VectorEditor::setNodeType(int path, int node, VectorNodeType type) {
    if (path < 0 || size_t(path) >= paths_.size()) return;
    VectorPath& p = paths_[size_t(path)];
    if (node < 0 || size_t(node) >= p.nodes.size()) return;
    p.nodes[size_t(node)].type = type;
    if (type == VectorNodeType::Symmetric && p.nodes[size_t(node)].hasOut) {
        p.nodes[size_t(node)].hasIn = true;
        p.nodes[size_t(node)].in = p.nodes[size_t(node)].out * -1.0f;
    }
}

void VectorEditor::deleteNode(int path, int node) {
    if (path < 0 || size_t(path) >= paths_.size()) return;
    VectorPath& p = paths_[size_t(path)];
    if (node < 0 || size_t(node) >= p.nodes.size()) return;
    p.nodes.erase(p.nodes.begin() + node);
    if (p.nodes.size() < 2) deletePath(path);
}

void VectorEditor::deletePath(int path) {
    if (path < 0 || size_t(path) >= paths_.size()) return;
    paths_.erase(paths_.begin() + path);
    if (activePath_ == path) activePath_ = -1;
    else if (activePath_ > path) --activePath_;
}

void VectorEditor::clear() {
    paths_.clear();
    activePath_ = -1;
}

void VectorEditor::flattenPath(const VectorPath& path, std::vector<Vec3>* out) const {
    out->clear();
    if (path.nodes.size() < 2) {
        if (path.nodes.size() == 1) out->push_back(toWorld(path.nodes[0].point));
        return;
    }
    const size_t count = path.nodes.size();
    const size_t segments = path.closed ? count : count - 1;
    for (size_t i = 0; i < segments; ++i) {
        const VectorNode& a = path.nodes[i];
        const VectorNode& b = path.nodes[(i + 1) % count];
        const Vec2 p0 = a.point;
        const Vec2 p1 = b.point;
        const Vec2 c0 = a.hasOut ? p0 + a.out : p0;
        const Vec2 c1 = b.hasIn ? p1 + b.in : p1;
        const bool straight = !a.hasOut && !b.hasIn;
        const int steps = straight ? 1 : kBezierSegments;
        for (int s = 0; s < steps; ++s) {
            const float t = float(s) / float(steps);
            out->push_back(toWorld(cubicPoint(p0, c0, c1, p1, t)));
        }
    }
    // Final point (or closing point).
    const VectorNode& last = path.closed ? path.nodes[0] : path.nodes[count - 1];
    out->push_back(toWorld(last.point));
}

void VectorEditor::buildGeometry(OverlayBuffer& overlay, bool showHandles, int selectedPath,
                                 int selectedNode, Vec3 eyePosition) const {
    overlay.setState(-1, true);

    // Working plane: a 1 m grid so the user can see where the plane is.
    const Vec4 gridColor(0.35f, 0.45f, 0.55f, 0.25f);
    const float extent = 0.5f;
    const int lines = 11;
    for (int i = 0; i < lines; ++i) {
        const float t = -extent + 2.0f * extent * float(i) / float(lines - 1);
        overlay.addBillboardLine(toWorld(Vec2(t, -extent)), toWorld(Vec2(t, extent)),
                                 eyePosition, 0.0015f, gridColor);
        overlay.addBillboardLine(toWorld(Vec2(-extent, t)), toWorld(Vec2(extent, t)),
                                 eyePosition, 0.0015f, gridColor);
    }

    std::vector<Vec3> points;
    for (size_t p = 0; p < paths_.size(); ++p) {
        const VectorPath& path = paths_[p];
        flattenPath(path, &points);
        const bool selected = int(p) == selectedPath;
        Vec4 color = path.strokeColor;
        if (selected) color = Vec4(1.0f, 0.85f, 0.2f, 1.0f);
        // Stroke width is stored in document units (mm); draw it in metres.
        const float width = std::max(0.002f, path.strokeWidth * 0.001f);
        for (size_t i = 0; i + 1 < points.size(); ++i)
            overlay.addBillboardLine(points[i], points[i + 1], eyePosition, width, color);

        if (!showHandles) continue;
        for (size_t n = 0; n < path.nodes.size(); ++n) {
            const VectorNode& node = path.nodes[n];
            const Vec3 world = toWorld(node.point);
            const bool nodeSelected = selected && int(n) == selectedNode;
            const Vec4 nodeColor = nodeSelected ? Vec4(1.0f, 0.4f, 0.1f, 1.0f)
                                                : Vec4(0.95f, 0.95f, 0.95f, 0.9f);
            // A small camera facing square marks the node.
            const Vec3 toEye = normalize(eyePosition - world);
            Vec3 right = cross(Vec3(0, 1, 0), toEye);
            if (lengthSq(right) < 1e-6f) right = Vec3(1, 0, 0);
            right = normalize(right) * kHandleSize;
            const Vec3 up = normalize(cross(toEye, right)) * kHandleSize;
            overlay.addQuad(world - right - up, world + right - up, world + right + up,
                            world - right + up, nodeColor);
            if (node.hasIn) {
                const Vec3 tip = toWorld(node.point + node.in);
                overlay.addBillboardLine(world, tip, eyePosition, 0.002f,
                                         Vec4(0.4f, 0.8f, 1.0f, 0.8f));
            }
            if (node.hasOut) {
                const Vec3 tip = toWorld(node.point + node.out);
                overlay.addBillboardLine(world, tip, eyePosition, 0.002f,
                                         Vec4(0.4f, 0.8f, 1.0f, 0.8f));
            }
        }
    }
}

VectorDoc VectorEditor::toDocument(double unitScale) const {
    VectorDoc document;
    document.present = true;
    document.version = 1;
    document.planeOrigin = origin_;
    document.planeRotation = rotation_;
    document.unitScale = unitScale > 0.0 ? unitScale : 0.001;

    // Plane-local metres -> document units.
    const float toUnits = float(1.0 / document.unitScale);
    document.paths.reserve(paths_.size());
    for (const VectorPath& path : paths_) {
        if (path.nodes.size() < 2) continue;
        VectorPath out = path;
        for (VectorNode& node : out.nodes) {
            node.point = node.point * toUnits;
            node.in = node.in * toUnits;
            node.out = node.out * toUnits;
        }
        document.paths.push_back(std::move(out));
    }
    return document;
}

void VectorEditor::fromDocument(const VectorDoc& document) {
    paths_.clear();
    activePath_ = -1;
    origin_ = document.planeOrigin;
    rotation_ = document.planeRotation;
    const float toMetres = float(document.unitScale > 0.0 ? document.unitScale : 0.001);
    for (const VectorPath& path : document.paths) {
        VectorPath out = path;
        for (VectorNode& node : out.nodes) {
            node.point = node.point * toMetres;
            node.in = node.in * toMetres;
            node.out = node.out * toMetres;
        }
        paths_.push_back(std::move(out));
    }
    nextId_ = int(paths_.size()) + 1;
    LOGI("vector document restored: %zu paths", paths_.size());
}

}  // namespace fcxr
