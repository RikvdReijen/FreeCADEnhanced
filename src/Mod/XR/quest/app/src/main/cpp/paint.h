// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Painting: texture layers stamped through the loaded document's UVs, and
// free-floating 3D ribbon strokes. Both serialise to the §4 schemas and go
// back to the desktop inside an FCXR package.
//
// Texture painting keeps every layer as an RGBA8 buffer in RAM, composites the
// dirty rectangle into a single RGBA8 image and uploads only that rectangle
// with glTexSubImage2D, so a 1024x1024 layer stack costs ~4 MB per layer and a
// brush dab costs a few kilobytes of upload.
#pragma once

#include <string>
#include <vector>

#include "document.h"
#include "fcxr.h"
#include "renderer.h"

namespace fcxr {

struct Brush {
    Vec4 color{0.85f, 0.15f, 0.15f, 1.0f};  // linear RGBA
    float radius = 0.01f;                   // metres on the surface
    float hardness = 0.6f;                  // 0 soft, 1 hard edge
    float flow = 0.85f;                     // per dab
    BlendMode blend = BlendMode::Normal;
};

struct PaintLayerData {
    std::string name = "Layer";
    int width = 1024;
    int height = 1024;
    std::vector<uint8_t> pixels;  // RGBA8, straight alpha
    float opacity = 1.0f;
    BlendMode blend = BlendMode::Normal;
    bool visible = true;
};

// Everything painted onto one document primitive.
struct PaintTargetData {
    int primitive = -1;
    std::string fcName;
    std::vector<PaintLayerData> layers;
    int activeLayer = 0;
    int width = 1024;
    int height = 1024;
    std::vector<uint8_t> composite;  // RGBA8 flattened stack
    int texture = -1;                // renderer texture handle
    // Dirty rectangle in texels, empty when nothing changed.
    int dirtyX0 = 0, dirtyY0 = 0, dirtyX1 = 0, dirtyY1 = 0;
    bool dirty = false;
};

class PaintSystem {
public:
    void init(Renderer* renderer) { renderer_ = renderer; }
    void clear();

    Brush& brush() { return brush_; }
    const Brush& brush() const { return brush_; }

    // ---- texture painting ------------------------------------------------
    // Creates (or finds) the layer stack for a primitive and attaches its
    // texture to the document's material.
    int ensureTarget(DocumentScene& scene, int primitive, int resolution = 1024);
    int targetForPrimitive(int primitive) const;
    const std::vector<PaintTargetData>& targets() const { return targets_; }
    std::vector<PaintTargetData>& targets() { return targets_; }

    int addLayer(int target, const std::string& name);
    void setActiveLayer(int target, int layer);
    void setLayerOpacity(int target, int layer, float opacity);
    void setLayerBlend(int target, int layer, BlendMode blend);
    void setLayerVisible(int target, int layer, bool visible);
    bool removeLayer(int target, int layer);

    // A stroke is a run of dabs; the layer is snapshotted at beginStroke so
    // undo() can put it back.
    void beginStroke(DocumentScene& scene, const DocumentHit& hit);
    void continueStroke(DocumentScene& scene, const DocumentHit& hit);
    void endStroke();
    bool strokeActive() const { return strokeActive_; }
    void undo();
    bool canUndo() const { return !undoStack_.empty(); }

    // Uploads pending dirty rectangles. Call once per frame on the render
    // thread.
    void flush();

    // ---- 3D ribbons ------------------------------------------------------
    void beginRibbon(Vec3 position, Vec3 normal, float radius);
    void extendRibbon(Vec3 position, Vec3 normal, float radius, float seconds);
    void endRibbon();
    bool ribbonActive() const { return ribbonActive_; }
    const std::vector<Stroke3D>& ribbons() const { return ribbons_; }
    void undoRibbon();
    // Appends the ribbon geometry to the overlay stream.
    void buildRibbonGeometry(OverlayBuffer& overlay) const;

    // ---- serialisation ---------------------------------------------------
    // Fills a PaintDoc and the PNG chunks for an FCXR upload. Returns false
    // when there is nothing to send.
    bool buildPaintDocument(PaintDoc* paint, std::vector<std::vector<uint8_t>>* pngChunks,
                            std::vector<ImageRef>* images) const;
    // Convenience: a complete document ready for POST /api/v1/paint.
    bool buildUpload(const std::string& sourceDocument, Document* out) const;
    // Restores layers from a document that already carries paint.
    void loadFromDocument(const Document& document, DocumentScene& scene);

    size_t layerCount() const;
    size_t memoryBytes() const;

private:
    struct UndoEntry {
        int target = -1;
        int layer = -1;
        int x0 = 0, y0 = 0, x1 = 0, y1 = 0;
        std::vector<uint8_t> pixels;
    };

    void stamp(PaintTargetData& target, Vec2 uv, float radiusUv, float pressure);
    void compositeRect(PaintTargetData& target, int x0, int y0, int x1, int y1);
    void markDirty(PaintTargetData& target, int x0, int y0, int x1, int y1);
    // UV units per metre at a hit, from the ratio of the triangle's UV area to
    // its world area. Keeps the brush the same physical size everywhere.
    float uvScaleAt(const DocumentScene& scene, const DocumentHit& hit) const;

    Renderer* renderer_ = nullptr;
    Brush brush_;
    std::vector<PaintTargetData> targets_;
    std::vector<UndoEntry> undoStack_;

    bool strokeActive_ = false;
    int strokeTarget_ = -1;
    Vec2 lastUv_{0, 0};
    bool haveLastUv_ = false;

    bool ribbonActive_ = false;
    std::vector<Stroke3D> ribbons_;
};

}  // namespace fcxr
