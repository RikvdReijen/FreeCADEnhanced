// SPDX-License-Identifier: LGPL-2.1-or-later
//
// In-VR user interface: flat quad panels that the controller ray points at.
//
// The API is immediate mode — the app rebuilds the whole UI every frame — which
// keeps the interaction state in one place and means a panel can be moved or
// re-parented (to the wrist, to the world) without any retained bookkeeping.
//
// Panels are drawn into an OverlayBuffer as triangles: no offscreen textures,
// no texture atlas, and text comes from the same stroke font as the §2 `text`
// primitive, so it stays sharp at any distance.
#pragma once

#include <string>
#include <vector>

#include "input.h"
#include "renderer.h"

namespace fcxr {

struct UiTheme {
    Vec4 panel{0.06f, 0.07f, 0.09f, 0.88f};
    Vec4 panelEdge{0.25f, 0.55f, 0.75f, 0.85f};
    Vec4 title{0.80f, 0.90f, 1.00f, 1.0f};
    Vec4 text{0.88f, 0.90f, 0.93f, 1.0f};
    Vec4 textDim{0.55f, 0.58f, 0.62f, 1.0f};
    Vec4 widget{0.14f, 0.16f, 0.20f, 0.95f};
    Vec4 widgetHover{0.20f, 0.32f, 0.42f, 0.98f};
    Vec4 widgetActive{0.16f, 0.52f, 0.72f, 1.0f};
    Vec4 accent{0.20f, 0.72f, 0.95f, 1.0f};
    Vec4 warning{0.95f, 0.55f, 0.15f, 1.0f};
    float rowHeight = 0.042f;
    float padding = 0.012f;
    float textHeight = 0.020f;
    float strokeWidth = 0.0028f;
};

class Ui {
public:
    // `pointer` is the hand that drives the ray; `eye` positions billboards.
    void beginFrame(const HandState& pointer, Vec3 eye, float deltaSeconds);
    // Emits the pointer ray and cursor, and returns the finished geometry.
    void endFrame(OverlayBuffer& overlay);

    // ---- panels ----------------------------------------------------------
    // `transform` places the panel: its origin is the panel centre, local +X
    // is right, +Y is up and +Z points at the viewer.
    void beginPanel(const std::string& title, const Mat4& transform, Vec2 size);
    void endPanel();
    bool panelHovered() const { return panelHovered_; }

    // ---- widgets (all return true on the frame they are activated) -------
    bool button(const std::string& label, bool enabled = true);
    bool toggle(const std::string& label, bool* value);
    bool slider(const std::string& label, float* value, float minimum, float maximum);
    bool listItem(const std::string& label, bool selected, const std::string& detail = "");
    void text(const std::string& value, bool dim = false);
    void heading(const std::string& value);
    void separator();
    void spacer(float metres);
    // Hue/saturation disc with a value strip underneath. Returns true while
    // the colour is being changed.
    bool colorWheel(Vec4* color, float size = 0.16f);
    // A read-only progress bar, 0..1.
    void progressBar(float value, const Vec4& color);
    // Starts a row of `count` equal width buttons; call button() `count` times.
    void beginRow(int count);
    void endRow();

    UiTheme& theme() { return theme_; }
    // True when the ray hit any panel this frame (so the app should not also
    // paint or teleport with the same trigger press).
    bool consumedPointer() const { return consumed_; }

    // Where the ray hit the world, for the pointer dot.
    void setWorldHit(bool hit, Vec3 position);

private:
    struct Rect {
        float x = 0, y = 0, w = 0, h = 0;
        bool contains(Vec2 p) const {
            return p.x >= x && p.x <= x + w && p.y >= y && p.y <= y + h;
        }
    };

    Rect nextRow(float height);
    bool hit(const Rect& rect) const;
    // Panel-local -> world.
    Vec3 world(float x, float y) const;
    void quad(const Rect& rect, const Vec4& color);
    void outline(const Rect& rect, float thickness, const Vec4& color);
    void label(const std::string& value, float x, float y, float height, const Vec4& color);
    void labelCentred(const std::string& value, const Rect& rect, float height,
                      const Vec4& color);

    UiTheme theme_;
    OverlayBuffer* buffer_ = nullptr;
    OverlayBuffer local_;

    HandState pointer_;
    Vec3 eye_{0, 0, 0};
    float delta_ = 0.0f;
    bool consumed_ = false;
    bool worldHit_ = false;
    Vec3 worldHitPosition_{0, 0, 0};

    // current panel
    Mat4 panelTransform_;
    Vec2 panelSize_{0, 0};
    bool inPanel_ = false;
    bool panelHovered_ = false;
    Vec2 pointerLocal_{0, 0};
    float pointerDistance_ = 0.0f;
    float cursorY_ = 0.0f;

    // row layout
    int rowCount_ = 0;
    int rowIndex_ = 0;
    Rect rowRect_;

    bool triggerWasDown_ = false;
    int activeWidget_ = -1;  // index of the widget being dragged
    int widgetCounter_ = 0;
};

}  // namespace fcxr
