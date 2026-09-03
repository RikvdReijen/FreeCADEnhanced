// SPDX-License-Identifier: LGPL-2.1-or-later
#include "paint.h"

#include <algorithm>
#include <cstring>

#include "log.h"
#include "png.h"

namespace fcxr {
namespace {

constexpr size_t kMaxUndo = 24;

inline uint8_t toByte(float v) { return uint8_t(clampf(v, 0.0f, 1.0f) * 255.0f + 0.5f); }
inline float fromByte(uint8_t v) { return float(v) * (1.0f / 255.0f); }

// Source-over and friends, on straight alpha.
void blendPixel(uint8_t* dst, const float src[4], BlendMode mode, float amount) {
    const float sa = src[3] * amount;
    if (sa <= 0.0f) return;
    float d[4] = {fromByte(dst[0]), fromByte(dst[1]), fromByte(dst[2]), fromByte(dst[3])};

    switch (mode) {
        case BlendMode::Erase: {
            d[3] = d[3] * (1.0f - sa);
            break;
        }
        case BlendMode::Add: {
            for (int i = 0; i < 3; ++i) d[i] = saturate(d[i] + src[i] * sa);
            d[3] = saturate(d[3] + sa);
            break;
        }
        case BlendMode::Multiply: {
            for (int i = 0; i < 3; ++i)
                d[i] = d[i] * (1.0f - sa) + (d[i] * src[i]) * sa;
            d[3] = saturate(d[3] + sa * (1.0f - d[3]));
            break;
        }
        default: {  // Normal: source-over
            const float outAlpha = sa + d[3] * (1.0f - sa);
            if (outAlpha > 1e-5f) {
                for (int i = 0; i < 3; ++i)
                    d[i] = (src[i] * sa + d[i] * d[3] * (1.0f - sa)) / outAlpha;
            }
            d[3] = outAlpha;
            break;
        }
    }
    for (int i = 0; i < 4; ++i) dst[i] = toByte(d[i]);
}

// Composites `layer` over `base` (both straight alpha RGBA8) for one pixel.
void compositeLayerPixel(uint8_t* base, const uint8_t* layer, float opacity, BlendMode mode) {
    float src[4] = {fromByte(layer[0]), fromByte(layer[1]), fromByte(layer[2]),
                    fromByte(layer[3])};
    blendPixel(base, src, mode, opacity);
}

}  // namespace

void PaintSystem::clear() {
    if (renderer_) {
        for (PaintTargetData& target : targets_) {
            if (target.texture >= 0) renderer_->destroyTexture(target.texture);
        }
    }
    targets_.clear();
    undoStack_.clear();
    ribbons_.clear();
    strokeActive_ = false;
    ribbonActive_ = false;
}

int PaintSystem::targetForPrimitive(int primitive) const {
    for (size_t i = 0; i < targets_.size(); ++i) {
        if (targets_[i].primitive == primitive) return int(i);
    }
    return -1;
}

int PaintSystem::ensureTarget(DocumentScene& scene, int primitive, int resolution) {
    const int existing = targetForPrimitive(primitive);
    if (existing >= 0) return existing;
    if (primitive < 0 || size_t(primitive) >= scene.primitives().size() || !renderer_)
        return -1;

    resolution = resolution < 128 ? 128 : (resolution > 4096 ? 4096 : resolution);
    PaintTargetData target;
    target.primitive = primitive;
    target.fcName = scene.primitives()[size_t(primitive)].fcName;
    target.width = target.height = resolution;
    const size_t bytes = size_t(resolution) * size_t(resolution) * 4;

    PaintLayerData base;
    base.name = "Base";
    base.width = base.height = resolution;
    base.pixels.assign(bytes, 0);
    target.layers.push_back(std::move(base));
    target.activeLayer = 0;
    target.composite.assign(bytes, 0);
    target.texture = renderer_->createEmptyTexture(resolution, resolution, /*srgb=*/true);
    if (target.texture < 0) return -1;
    renderer_->updateTexture(target.texture, 0, 0, resolution, resolution,
                             target.composite.data());
    targets_.push_back(std::move(target));
    const int index = int(targets_.size()) - 1;
    scene.setPaintTexture(primitive, targets_[size_t(index)].texture, renderer_);
    LOGI("paint target %d on '%s' at %dx%d", index, targets_[size_t(index)].fcName.c_str(),
         resolution, resolution);
    return index;
}

int PaintSystem::addLayer(int target, const std::string& name) {
    if (target < 0 || size_t(target) >= targets_.size()) return -1;
    PaintTargetData& t = targets_[size_t(target)];
    if (t.layers.size() >= 16) return -1;
    PaintLayerData layer;
    layer.name = name.empty() ? ("Layer " + std::to_string(t.layers.size() + 1)) : name;
    layer.width = t.width;
    layer.height = t.height;
    layer.pixels.assign(size_t(t.width) * size_t(t.height) * 4, 0);
    t.layers.push_back(std::move(layer));
    t.activeLayer = int(t.layers.size()) - 1;
    return t.activeLayer;
}

void PaintSystem::setActiveLayer(int target, int layer) {
    if (target < 0 || size_t(target) >= targets_.size()) return;
    PaintTargetData& t = targets_[size_t(target)];
    if (layer >= 0 && size_t(layer) < t.layers.size()) t.activeLayer = layer;
}

void PaintSystem::setLayerOpacity(int target, int layer, float opacity) {
    if (target < 0 || size_t(target) >= targets_.size()) return;
    PaintTargetData& t = targets_[size_t(target)];
    if (layer < 0 || size_t(layer) >= t.layers.size()) return;
    t.layers[size_t(layer)].opacity = saturate(opacity);
    compositeRect(t, 0, 0, t.width, t.height);
}

void PaintSystem::setLayerBlend(int target, int layer, BlendMode blend) {
    if (target < 0 || size_t(target) >= targets_.size()) return;
    PaintTargetData& t = targets_[size_t(target)];
    if (layer < 0 || size_t(layer) >= t.layers.size()) return;
    t.layers[size_t(layer)].blend = blend;
    compositeRect(t, 0, 0, t.width, t.height);
}

void PaintSystem::setLayerVisible(int target, int layer, bool visible) {
    if (target < 0 || size_t(target) >= targets_.size()) return;
    PaintTargetData& t = targets_[size_t(target)];
    if (layer < 0 || size_t(layer) >= t.layers.size()) return;
    t.layers[size_t(layer)].visible = visible;
    compositeRect(t, 0, 0, t.width, t.height);
}

bool PaintSystem::removeLayer(int target, int layer) {
    if (target < 0 || size_t(target) >= targets_.size()) return false;
    PaintTargetData& t = targets_[size_t(target)];
    if (layer < 0 || size_t(layer) >= t.layers.size() || t.layers.size() <= 1) return false;
    t.layers.erase(t.layers.begin() + layer);
    t.activeLayer = std::min<int>(t.activeLayer, int(t.layers.size()) - 1);
    compositeRect(t, 0, 0, t.width, t.height);
    return true;
}

// UV units per metre at the hit triangle: sqrt(uvArea / worldArea).
float PaintSystem::uvScaleAt(const DocumentScene& scene, const DocumentHit& hit) const {
    if (hit.primitive < 0 || size_t(hit.primitive) >= scene.primitives().size()) return 1.0f;
    const DocumentPrimitive& item = scene.primitives()[size_t(hit.primitive)];
    const MeshData& mesh = item.cpu;
    const size_t base = size_t(hit.triangle) * 3;
    if (base + 2 >= mesh.indices.size()) return 1.0f;
    const uint32_t a = mesh.indices[base], b = mesh.indices[base + 1],
                   c = mesh.indices[base + 2];
    if (a >= mesh.uvs.size() || b >= mesh.uvs.size() || c >= mesh.uvs.size()) return 1.0f;

    const Mat4 model = scene.placement() * item.nodeTransform;
    const Vec3 pa = transformPoint(model, mesh.positions[a]);
    const Vec3 pb = transformPoint(model, mesh.positions[b]);
    const Vec3 pc = transformPoint(model, mesh.positions[c]);
    const float worldArea = 0.5f * length(cross(pb - pa, pc - pa));
    const Vec2 ua = mesh.uvs[a], ub = mesh.uvs[b], uc = mesh.uvs[c];
    const float uvArea =
        0.5f * std::fabs((ub.x - ua.x) * (uc.y - ua.y) - (uc.x - ua.x) * (ub.y - ua.y));
    if (worldArea < 1e-12f || uvArea < 1e-12f) return 1.0f;
    return std::sqrt(uvArea / worldArea);
}

void PaintSystem::markDirty(PaintTargetData& target, int x0, int y0, int x1, int y1) {
    x0 = std::max(0, x0);
    y0 = std::max(0, y0);
    x1 = std::min(target.width, x1);
    y1 = std::min(target.height, y1);
    if (x1 <= x0 || y1 <= y0) return;
    if (!target.dirty) {
        target.dirtyX0 = x0;
        target.dirtyY0 = y0;
        target.dirtyX1 = x1;
        target.dirtyY1 = y1;
        target.dirty = true;
        return;
    }
    target.dirtyX0 = std::min(target.dirtyX0, x0);
    target.dirtyY0 = std::min(target.dirtyY0, y0);
    target.dirtyX1 = std::max(target.dirtyX1, x1);
    target.dirtyY1 = std::max(target.dirtyY1, y1);
}

void PaintSystem::compositeRect(PaintTargetData& target, int x0, int y0, int x1, int y1) {
    x0 = std::max(0, x0);
    y0 = std::max(0, y0);
    x1 = std::min(target.width, x1);
    y1 = std::min(target.height, y1);
    if (x1 <= x0 || y1 <= y0) return;
    for (int y = y0; y < y1; ++y) {
        const size_t row = size_t(y) * size_t(target.width);
        for (int x = x0; x < x1; ++x) {
            uint8_t* out = &target.composite[(row + size_t(x)) * 4];
            out[0] = out[1] = out[2] = out[3] = 0;
            for (const PaintLayerData& layer : target.layers) {
                if (!layer.visible || layer.opacity <= 0.0f) continue;
                compositeLayerPixel(out, &layer.pixels[(row + size_t(x)) * 4], layer.opacity,
                                    layer.blend);
            }
        }
    }
    markDirty(target, x0, y0, x1, y1);
}

void PaintSystem::stamp(PaintTargetData& target, Vec2 uv, float radiusUv, float pressure) {
    if (target.layers.empty()) return;
    PaintLayerData& layer = target.layers[size_t(target.activeLayer)];

    const float radiusX = radiusUv * float(target.width);
    const float radiusY = radiusUv * float(target.height);
    // OpenGL UV: v = 0 is the bottom row of the image.
    const float centreX = uv.x * float(target.width);
    const float centreY = (1.0f - uv.y) * float(target.height);

    const int x0 = int(std::floor(centreX - radiusX)) - 1;
    const int x1 = int(std::ceil(centreX + radiusX)) + 1;
    const int y0 = int(std::floor(centreY - radiusY)) - 1;
    const int y1 = int(std::ceil(centreY + radiusY)) + 1;

    const float src[4] = {brush_.color.x, brush_.color.y, brush_.color.z, brush_.color.w};
    const float hardness = clampf(brush_.hardness, 0.0f, 0.99f);

    for (int y = std::max(0, y0); y < std::min(target.height, y1); ++y) {
        for (int x = std::max(0, x0); x < std::min(target.width, x1); ++x) {
            const float dx = (float(x) + 0.5f - centreX) / std::max(radiusX, 1e-3f);
            const float dy = (float(y) + 0.5f - centreY) / std::max(radiusY, 1e-3f);
            const float d = std::sqrt(dx * dx + dy * dy);
            if (d > 1.0f) continue;
            // Smooth falloff from `hardness` to the edge.
            float weight = 1.0f;
            if (d > hardness) weight = 1.0f - (d - hardness) / std::max(1e-3f, 1.0f - hardness);
            weight = saturate(weight);
            weight *= weight * (3.0f - 2.0f * weight);  // smoothstep
            if (weight <= 0.0f) continue;
            uint8_t* pixel = &layer.pixels[(size_t(y) * size_t(target.width) + size_t(x)) * 4];
            blendPixel(pixel, src, brush_.blend, weight * pressure * brush_.flow);
        }
    }
    compositeRect(target, x0, y0, x1, y1);
}

void PaintSystem::beginStroke(DocumentScene& scene, const DocumentHit& hit) {
    if (!hit.hit) return;
    const int target = ensureTarget(scene, hit.primitive);
    if (target < 0) return;
    strokeActive_ = true;
    strokeTarget_ = target;
    haveLastUv_ = false;

    // Snapshot the whole active layer for undo. Layers are a few megabytes,
    // and the stack is capped, so this stays well inside the app's budget.
    PaintTargetData& t = targets_[size_t(target)];
    UndoEntry entry;
    entry.target = target;
    entry.layer = t.activeLayer;
    entry.x0 = 0;
    entry.y0 = 0;
    entry.x1 = t.width;
    entry.y1 = t.height;
    entry.pixels = t.layers[size_t(t.activeLayer)].pixels;
    undoStack_.push_back(std::move(entry));
    if (undoStack_.size() > kMaxUndo) undoStack_.erase(undoStack_.begin());

    continueStroke(scene, hit);
}

void PaintSystem::continueStroke(DocumentScene& scene, const DocumentHit& hit) {
    if (!strokeActive_ || !hit.hit) return;
    if (strokeTarget_ < 0 || size_t(strokeTarget_) >= targets_.size()) return;
    PaintTargetData& target = targets_[size_t(strokeTarget_)];
    if (hit.primitive != target.primitive) return;  // do not paint across parts

    const float radiusUv = brush_.radius * uvScaleAt(scene, hit);
    if (!(radiusUv > 0.0f)) return;

    if (haveLastUv_) {
        // Interpolate between dabs so a fast sweep is still a continuous line.
        const Vec2 delta = hit.uv - lastUv_;
        const float distance = length(delta);
        const float step = std::max(radiusUv * 0.35f, 1.0f / float(target.width * 4));
        const int steps = std::min(256, int(distance / step));
        for (int i = 1; i <= steps; ++i)
            stamp(target, lastUv_ + delta * (float(i) / float(steps + 1)), radiusUv, 1.0f);
    }
    stamp(target, hit.uv, radiusUv, 1.0f);
    lastUv_ = hit.uv;
    haveLastUv_ = true;
}

void PaintSystem::endStroke() {
    strokeActive_ = false;
    haveLastUv_ = false;
}

void PaintSystem::undo() {
    if (undoStack_.empty()) return;
    UndoEntry entry = std::move(undoStack_.back());
    undoStack_.pop_back();
    if (entry.target < 0 || size_t(entry.target) >= targets_.size()) return;
    PaintTargetData& target = targets_[size_t(entry.target)];
    if (entry.layer < 0 || size_t(entry.layer) >= target.layers.size()) return;
    target.layers[size_t(entry.layer)].pixels = std::move(entry.pixels);
    compositeRect(target, 0, 0, target.width, target.height);
}

void PaintSystem::flush() {
    if (!renderer_) return;
    for (PaintTargetData& target : targets_) {
        if (!target.dirty || target.texture < 0) continue;
        const int width = target.dirtyX1 - target.dirtyX0;
        const int height = target.dirtyY1 - target.dirtyY0;
        if (width <= 0 || height <= 0) {
            target.dirty = false;
            continue;
        }
        // glTexSubImage2D wants tightly packed rows, so copy the rectangle out.
        std::vector<uint8_t> patch(size_t(width) * size_t(height) * 4);
        for (int y = 0; y < height; ++y) {
            const size_t source =
                (size_t(target.dirtyY0 + y) * size_t(target.width) + size_t(target.dirtyX0)) * 4;
            std::memcpy(&patch[size_t(y) * size_t(width) * 4], &target.composite[source],
                        size_t(width) * 4);
        }
        renderer_->updateTexture(target.texture, target.dirtyX0, target.dirtyY0, width, height,
                                 patch.data());
        target.dirty = false;
    }
}

// ------------------------------------------------------------------ ribbons

void PaintSystem::beginRibbon(Vec3 position, Vec3 normal, float radius) {
    Stroke3D stroke;
    stroke.brush = "ribbon";
    stroke.color = brush_.color;
    stroke.width = radius * 2.0f;
    ribbons_.push_back(std::move(stroke));
    ribbonActive_ = true;
    extendRibbon(position, normal, radius, 0.0f);
}

void PaintSystem::extendRibbon(Vec3 position, Vec3 normal, float radius, float seconds) {
    if (!ribbonActive_ || ribbons_.empty()) return;
    Stroke3D& stroke = ribbons_.back();
    // Skip points that are too close together; the ribbon builder needs a
    // usable direction for each segment.
    if (!stroke.points.empty() &&
        lengthSq(stroke.points.back().p - position) < (radius * 0.25f) * (radius * 0.25f))
        return;
    if (stroke.points.size() >= 4096) return;
    StrokePoint point;
    point.p = position;
    point.n = normalize(normal);
    if (lengthSq(point.n) < 0.5f) point.n = Vec3(0, 1, 0);
    point.r = radius;
    point.t = seconds;
    stroke.points.push_back(point);
}

void PaintSystem::endRibbon() {
    ribbonActive_ = false;
    // Drop degenerate strokes so they never reach the desktop.
    if (!ribbons_.empty() && ribbons_.back().points.size() < 2) ribbons_.pop_back();
}

void PaintSystem::undoRibbon() {
    if (!ribbons_.empty()) ribbons_.pop_back();
}

void PaintSystem::buildRibbonGeometry(OverlayBuffer& overlay) const {
    overlay.setState(-1, true);
    for (const Stroke3D& stroke : ribbons_) {
        for (size_t i = 0; i + 1 < stroke.points.size(); ++i) {
            const StrokePoint& a = stroke.points[i];
            const StrokePoint& b = stroke.points[i + 1];
            overlay.addRibbonSegment(a.p, b.p, a.n, b.n, a.r * 2.0f, b.r * 2.0f, stroke.color);
        }
    }
}

// ------------------------------------------------------------ serialisation

size_t PaintSystem::layerCount() const {
    size_t count = 0;
    for (const PaintTargetData& target : targets_) count += target.layers.size();
    return count;
}

size_t PaintSystem::memoryBytes() const {
    size_t bytes = 0;
    for (const PaintTargetData& target : targets_) {
        bytes += target.composite.size();
        for (const PaintLayerData& layer : target.layers) bytes += layer.pixels.size();
    }
    return bytes;
}

bool PaintSystem::buildPaintDocument(PaintDoc* paint,
                                     std::vector<std::vector<uint8_t>>* pngChunks,
                                     std::vector<ImageRef>* images) const {
    if (!paint || !pngChunks || !images) return false;
    *paint = PaintDoc();
    paint->present = true;
    paint->version = 1;

    for (const PaintTargetData& target : targets_) {
        PaintTarget outTarget;
        outTarget.fcName = target.fcName;
        for (const PaintLayerData& layer : target.layers) {
            // Skip layers that were never painted on: an empty PNG is a
            // pointless megabyte over the wire.
            bool empty = true;
            for (size_t i = 3; i < layer.pixels.size(); i += 4) {
                if (layer.pixels[i]) {
                    empty = false;
                    break;
                }
            }
            if (empty) continue;

            std::vector<uint8_t> png;
            std::string error;
            if (!pngEncode(layer.pixels.data(), layer.width, layer.height, 4, &png, 6,
                           &error)) {
                LOGE("cannot encode paint layer '%s': %s", layer.name.c_str(), error.c_str());
                continue;
            }
            ImageRef image;
            image.name = target.fcName + "_" + layer.name;
            image.mime = "image/png";
            image.chunk = int(pngChunks->size());
            pngChunks->push_back(std::move(png));

            PaintLayer outLayer;
            outLayer.name = layer.name;
            outLayer.image = int(images->size());
            outLayer.opacity = layer.opacity;
            outLayer.blend = layer.blend;
            outLayer.visible = layer.visible;
            outLayer.resolution[0] = layer.width;
            outLayer.resolution[1] = layer.height;
            images->push_back(std::move(image));
            outTarget.layers.push_back(std::move(outLayer));
        }
        if (!outTarget.layers.empty()) paint->targets.push_back(std::move(outTarget));
    }

    paint->strokes3d = ribbons_;
    paint->palette.push_back(brush_.color);
    return !paint->targets.empty() || !paint->strokes3d.empty();
}

bool PaintSystem::buildUpload(const std::string& sourceDocument, Document* out) const {
    if (!out) return false;
    *out = Document();
    out->asset.generator = "FreeCAD-XR Quest 1.0";
    out->asset.sourceDocument = sourceDocument;
    out->asset.unitScale = 0.001;
    // No `created` timestamp: the desktop hashes packages for change polling.
    out->scene.root = 0;
    Node root;
    root.name = "paint";
    out->nodes.push_back(root);
    return buildPaintDocument(&out->paint, &out->pngChunks, &out->images);
}

void PaintSystem::loadFromDocument(const Document& document, DocumentScene& scene) {
    if (!document.paint.present || !renderer_) return;
    for (const PaintTarget& target : document.paint.targets) {
        // Find the primitive with this FreeCAD name.
        int primitive = -1;
        for (size_t i = 0; i < scene.primitives().size(); ++i) {
            if (scene.primitives()[i].fcName == target.fcName) {
                primitive = int(i);
                break;
            }
        }
        if (primitive < 0 || target.layers.empty()) continue;
        const int resolution = target.layers[0].resolution[0];
        const int handle = ensureTarget(scene, primitive, resolution);
        if (handle < 0) continue;
        PaintTargetData& data = targets_[size_t(handle)];
        data.layers.clear();

        for (const PaintLayer& layer : target.layers) {
            PaintLayerData out;
            out.name = layer.name;
            out.opacity = layer.opacity;
            out.blend = layer.blend;
            out.visible = layer.visible;
            out.width = data.width;
            out.height = data.height;
            out.pixels.assign(size_t(data.width) * size_t(data.height) * 4, 0);
            if (layer.image >= 0 && size_t(layer.image) < document.images.size()) {
                const int chunk = document.images[size_t(layer.image)].chunk;
                if (chunk >= 0 && size_t(chunk) < document.pngChunks.size()) {
                    Image image;
                    std::string error;
                    if (pngDecode(document.pngChunks[size_t(chunk)].data(),
                                  document.pngChunks[size_t(chunk)].size(), &image, &error) &&
                        image.width == data.width && image.height == data.height) {
                        out.pixels = image.rgba;
                    } else if (!error.empty()) {
                        LOGW("paint layer '%s' could not be decoded: %s", layer.name.c_str(),
                             error.c_str());
                    }
                }
            }
            data.layers.push_back(std::move(out));
        }
        if (data.layers.empty()) addLayer(handle, "Base");
        data.activeLayer = int(data.layers.size()) - 1;
        compositeRect(data, 0, 0, data.width, data.height);
    }
    for (const Stroke3D& stroke : document.paint.strokes3d) ribbons_.push_back(stroke);
    LOGI("restored %zu paint targets and %zu ribbons", targets_.size(), ribbons_.size());
}

}  // namespace fcxr
