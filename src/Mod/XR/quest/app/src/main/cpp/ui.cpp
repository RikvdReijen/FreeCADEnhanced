// SPDX-License-Identifier: LGPL-2.1-or-later
#include "ui.h"

#include <algorithm>
#include <cstdio>

#include "text_font.h"

namespace fcxr {
namespace {

// HSV -> linear RGB, for the colour wheel.
Vec3 hsvToRgb(float h, float s, float v) {
    h = h - std::floor(h);
    const float i = std::floor(h * 6.0f);
    const float f = h * 6.0f - i;
    const float p = v * (1.0f - s);
    const float q = v * (1.0f - f * s);
    const float t = v * (1.0f - (1.0f - f) * s);
    switch (int(i) % 6) {
        case 0: return Vec3(v, t, p);
        case 1: return Vec3(q, v, p);
        case 2: return Vec3(p, v, t);
        case 3: return Vec3(p, q, v);
        case 4: return Vec3(t, p, v);
        default: return Vec3(v, p, q);
    }
}

void rgbToHsv(Vec3 rgb, float* h, float* s, float* v) {
    const float maximum = std::max(rgb.x, std::max(rgb.y, rgb.z));
    const float minimum = std::min(rgb.x, std::min(rgb.y, rgb.z));
    const float delta = maximum - minimum;
    *v = maximum;
    *s = maximum > 1e-5f ? delta / maximum : 0.0f;
    if (delta < 1e-5f) {
        *h = 0.0f;
        return;
    }
    if (maximum == rgb.x) *h = (rgb.y - rgb.z) / delta / 6.0f;
    else if (maximum == rgb.y) *h = (2.0f + (rgb.z - rgb.x) / delta) / 6.0f;
    else *h = (4.0f + (rgb.x - rgb.y) / delta) / 6.0f;
    if (*h < 0.0f) *h += 1.0f;
}

}  // namespace

void Ui::beginFrame(const HandState& pointer, Vec3 eye, float deltaSeconds) {
    pointer_ = pointer;
    eye_ = eye;
    delta_ = deltaSeconds;
    consumed_ = false;
    worldHit_ = false;
    widgetCounter_ = 0;
    local_.clear();
    if (!pointer_.trigger) activeWidget_ = -1;
}

void Ui::setWorldHit(bool hit, Vec3 position) {
    worldHit_ = hit;
    worldHitPosition_ = position;
}

void Ui::endFrame(OverlayBuffer& overlay) {
    // The pointer ray: from the hand to whatever it hit (a panel, the world,
    // or a fixed length into space).
    if (pointer_.active) {
        const Vec3 origin = pointer_.aimPosition;
        const Vec3 direction = pointer_.rayDirection();
        float distance = 1.6f;
        if (panelHovered_ && pointerDistance_ > 0.0f) distance = pointerDistance_;
        else if (worldHit_) distance = length(worldHitPosition_ - origin);
        const Vec3 end = origin + direction * distance;
        const Vec4 rayColor = panelHovered_ ? theme_.accent : Vec4(0.6f, 0.65f, 0.7f, 0.55f);
        local_.setState(-1, false);
        local_.addBillboardLine(origin, end, eye_, 0.0035f, rayColor);
        // Cursor dot at the end.
        const Vec3 toEye = normalize(eye_ - end);
        Vec3 right = cross(Vec3(0, 1, 0), toEye);
        if (lengthSq(right) < 1e-6f) right = Vec3(1, 0, 0);
        right = normalize(right) * 0.006f;
        const Vec3 up = normalize(cross(toEye, right)) * 0.006f;
        local_.addQuad(end - right - up, end + right - up, end + right + up, end - right + up,
                       rayColor);
    }
    overlay.setState(-1, true);
    // Panels are drawn without depth testing so they are never buried in the
    // machine they float inside.
    for (const OverlayBuffer::Batch& batch : local_.batches()) {
        overlay.setState(batch.texture, batch.depthTest);
        for (size_t i = 0; i < batch.count; i += 3) {
            overlay.addTriangle(local_.vertices()[batch.first + i],
                                local_.vertices()[batch.first + i + 1],
                                local_.vertices()[batch.first + i + 2]);
        }
    }
    triggerWasDown_ = pointer_.trigger > 0.5f;
    panelHovered_ = false;
}

// ------------------------------------------------------------------ panels

Vec3 Ui::world(float x, float y) const {
    return transformPoint(panelTransform_, Vec3(x, y, 0.0f));
}

void Ui::quad(const Rect& rect, const Vec4& color) {
    local_.addQuad(world(rect.x, rect.y), world(rect.x + rect.w, rect.y),
                   world(rect.x + rect.w, rect.y + rect.h), world(rect.x, rect.y + rect.h),
                   color);
}

void Ui::outline(const Rect& rect, float thickness, const Vec4& color) {
    Rect r = rect;
    quad({r.x, r.y, r.w, thickness}, color);
    quad({r.x, r.y + r.h - thickness, r.w, thickness}, color);
    quad({r.x, r.y, thickness, r.h}, color);
    quad({r.x + r.w - thickness, r.y, thickness, r.h}, color);
}

void Ui::label(const std::string& value, float x, float y, float height, const Vec4& color) {
    const Vec3 origin = world(x, y);
    const Vec3 right = transformDir(panelTransform_, Vec3(1, 0, 0));
    const Vec3 up = transformDir(panelTransform_, Vec3(0, 1, 0));
    local_.addText(value, origin, right, up, height, theme_.strokeWidth, color);
}

void Ui::labelCentred(const std::string& value, const Rect& rect, float height,
                      const Vec4& color) {
    const float width = fontTextWidth(value, height);
    label(value, rect.x + (rect.w - width) * 0.5f, rect.y + (rect.h - height) * 0.5f, height,
          color);
}

void Ui::beginPanel(const std::string& title, const Mat4& transform, Vec2 size) {
    panelTransform_ = transform;
    panelSize_ = size;
    inPanel_ = true;
    local_.setState(-1, false);

    // Where does the pointer ray cross this panel?
    pointerLocal_ = Vec2(-1000.0f, -1000.0f);
    pointerDistance_ = 0.0f;
    bool onPanel = false;
    if (pointer_.active) {
        const Vec3 origin = transformPoint(transform, Vec3(0, 0, 0));
        const Vec3 normal = normalize(transformDir(transform, Vec3(0, 0, 1)));
        float t = 0.0f;
        if (rayPlane(pointer_.aimPosition, pointer_.rayDirection(), origin, normal, &t)) {
            const Vec3 hitPoint = pointer_.aimPosition + pointer_.rayDirection() * t;
            const Mat4 inverse = mat4InverseRigid(transform);
            const Vec3 localHit = transformPoint(inverse, hitPoint);
            pointerLocal_ = Vec2(localHit.x, localHit.y);
            pointerDistance_ = t;
            onPanel = std::fabs(localHit.x) <= size.x * 0.5f &&
                      std::fabs(localHit.y) <= size.y * 0.5f;
        }
    }
    if (onPanel) {
        panelHovered_ = true;
        consumed_ = true;
    }

    const Rect background{-size.x * 0.5f, -size.y * 0.5f, size.x, size.y};
    quad(background, theme_.panel);
    outline(background, 0.0022f, theme_.panelEdge);

    cursorY_ = size.y * 0.5f - theme_.padding;
    if (!title.empty()) {
        const float height = theme_.textHeight * 1.15f;
        cursorY_ -= height;
        label(title, background.x + theme_.padding, cursorY_, height, theme_.title);
        cursorY_ -= theme_.padding * 0.6f;
        quad({background.x + theme_.padding, cursorY_,
              size.x - theme_.padding * 2.0f, 0.0015f},
             theme_.panelEdge);
        cursorY_ -= theme_.padding * 0.8f;
    }
}

void Ui::endPanel() { inPanel_ = false; }

Ui::Rect Ui::nextRow(float height) {
    if (rowCount_ > 0) {
        // Inside a row: split the reserved rectangle horizontally.
        const float width = rowRect_.w / float(rowCount_);
        Rect rect{rowRect_.x + width * float(rowIndex_), rowRect_.y, width - theme_.padding * 0.4f,
                  rowRect_.h};
        ++rowIndex_;
        return rect;
    }
    Rect rect{-panelSize_.x * 0.5f + theme_.padding, cursorY_ - height,
              panelSize_.x - theme_.padding * 2.0f, height};
    cursorY_ -= height + theme_.padding * 0.5f;
    return rect;
}

bool Ui::hit(const Rect& rect) const {
    return panelHovered_ && rect.contains(pointerLocal_);
}

void Ui::beginRow(int count) {
    rowRect_ = nextRow(theme_.rowHeight);
    rowCount_ = std::max(1, count);
    rowIndex_ = 0;
}

void Ui::endRow() {
    rowCount_ = 0;
    rowIndex_ = 0;
}

// ----------------------------------------------------------------- widgets

bool Ui::button(const std::string& labelText, bool enabled) {
    const Rect rect = nextRow(theme_.rowHeight);
    const bool over = enabled && hit(rect);
    const bool pressed = over && pointer_.trigger > 0.5f;
    quad(rect, !enabled ? theme_.widget
                        : (pressed ? theme_.widgetActive
                                   : (over ? theme_.widgetHover : theme_.widget)));
    if (over) outline(rect, 0.0018f, theme_.accent);
    labelCentred(labelText, rect, theme_.textHeight,
                 enabled ? theme_.text : theme_.textDim);
    // Activate on the press edge so a held trigger does not repeat.
    return over && pointer_.trigger > 0.5f && !triggerWasDown_;
}

bool Ui::toggle(const std::string& labelText, bool* value) {
    const Rect rect = nextRow(theme_.rowHeight);
    const bool over = hit(rect);
    quad(rect, over ? theme_.widgetHover : theme_.widget);
    if (over) outline(rect, 0.0018f, theme_.accent);

    const float box = rect.h * 0.55f;
    const Rect boxRect{rect.x + rect.w - box - theme_.padding, rect.y + (rect.h - box) * 0.5f,
                       box, box};
    quad(boxRect, (value && *value) ? theme_.accent : Vec4(0.10f, 0.11f, 0.13f, 1.0f));
    outline(boxRect, 0.0015f, theme_.panelEdge);
    label(labelText, rect.x + theme_.padding, rect.y + (rect.h - theme_.textHeight) * 0.5f,
          theme_.textHeight, theme_.text);

    if (over && pointer_.trigger > 0.5f && !triggerWasDown_) {
        if (value) *value = !*value;
        return true;
    }
    return false;
}

bool Ui::slider(const std::string& labelText, float* value, float minimum, float maximum) {
    const int id = widgetCounter_++;
    const Rect rect = nextRow(theme_.rowHeight);
    const bool over = hit(rect);
    quad(rect, theme_.widget);

    const float range = maximum - minimum;
    float normalised = range > 1e-9f ? (*value - minimum) / range : 0.0f;
    normalised = saturate(normalised);

    bool changed = false;
    const bool triggerDown = pointer_.trigger > 0.5f;
    if (over && triggerDown && !triggerWasDown_) activeWidget_ = id;
    if (activeWidget_ == id && triggerDown) {
        // Dragging continues even when the ray slides off the widget.
        const float t = saturate((pointerLocal_.x - rect.x) / std::max(rect.w, 1e-5f));
        const float updated = minimum + t * range;
        if (value && updated != *value) {
            *value = updated;
            changed = true;
        }
        normalised = t;
    }

    const Rect fill{rect.x, rect.y, rect.w * normalised, rect.h};
    quad(fill, theme_.widgetActive);
    if (over || activeWidget_ == id) outline(rect, 0.0018f, theme_.accent);

    char buffer[64];
    std::snprintf(buffer, sizeof(buffer), "%s  %.3g", labelText.c_str(),
                  value ? double(*value) : 0.0);
    label(buffer, rect.x + theme_.padding, rect.y + (rect.h - theme_.textHeight) * 0.5f,
          theme_.textHeight, theme_.text);
    return changed;
}

bool Ui::listItem(const std::string& labelText, bool selected, const std::string& detail) {
    const Rect rect = nextRow(theme_.rowHeight * 0.95f);
    const bool over = hit(rect);
    quad(rect, selected ? theme_.widgetActive
                        : (over ? theme_.widgetHover : theme_.widget));
    if (over) outline(rect, 0.0018f, theme_.accent);
    label(labelText, rect.x + theme_.padding,
          rect.y + (rect.h - theme_.textHeight) * 0.5f, theme_.textHeight, theme_.text);
    if (!detail.empty()) {
        const float width = fontTextWidth(detail, theme_.textHeight * 0.8f);
        label(detail, rect.x + rect.w - width - theme_.padding,
              rect.y + (rect.h - theme_.textHeight * 0.8f) * 0.5f, theme_.textHeight * 0.8f,
              theme_.textDim);
    }
    return over && pointer_.trigger > 0.5f && !triggerWasDown_;
}

void Ui::text(const std::string& value, bool dim) {
    const Rect rect = nextRow(theme_.textHeight * 1.5f);
    label(value, rect.x, rect.y + theme_.textHeight * 0.25f, theme_.textHeight,
          dim ? theme_.textDim : theme_.text);
}

void Ui::heading(const std::string& value) {
    const Rect rect = nextRow(theme_.textHeight * 1.8f);
    label(value, rect.x, rect.y + theme_.textHeight * 0.3f, theme_.textHeight * 1.1f,
          theme_.title);
}

void Ui::separator() {
    const Rect rect = nextRow(theme_.padding);
    quad({rect.x, rect.y + rect.h * 0.5f, rect.w, 0.0012f}, theme_.panelEdge);
}

void Ui::spacer(float metres) { cursorY_ -= metres; }

void Ui::progressBar(float value, const Vec4& color) {
    const Rect rect = nextRow(theme_.rowHeight * 0.45f);
    quad(rect, theme_.widget);
    quad({rect.x, rect.y, rect.w * saturate(value), rect.h}, color);
    outline(rect, 0.0012f, theme_.panelEdge);
}

bool Ui::colorWheel(Vec4* color, float size) {
    const int id = widgetCounter_++;
    if (!color) return false;
    const float radius = size * 0.5f;
    const float strip = theme_.rowHeight * 0.7f;
    const Rect area = nextRow(size + strip + theme_.padding);
    const float centreX = area.x + area.w * 0.5f;
    const float centreY = area.y + strip + theme_.padding + radius;

    float h = 0.0f, s = 0.0f, v = 1.0f;
    rgbToHsv(Vec3(color->x, color->y, color->z), &h, &s, &v);

    // Hue/saturation disc, as a fan of quads.
    const int sectors = 32;
    const int rings = 4;
    for (int ring = 0; ring < rings; ++ring) {
        const float r0 = radius * float(ring) / float(rings);
        const float r1 = radius * float(ring + 1) / float(rings);
        for (int sector = 0; sector < sectors; ++sector) {
            const float a0 = 2.0f * kPi * float(sector) / float(sectors);
            const float a1 = 2.0f * kPi * float(sector + 1) / float(sectors);
            const float hue0 = float(sector) / float(sectors);
            const float hue1 = float(sector + 1) / float(sectors);
            const Vec3 c00 = hsvToRgb(hue0, r0 / radius, v);
            const Vec3 c10 = hsvToRgb(hue1, r0 / radius, v);
            const Vec3 c11 = hsvToRgb(hue1, r1 / radius, v);
            const Vec3 c01 = hsvToRgb(hue0, r1 / radius, v);
            const Vec3 p00 = world(centreX + r0 * std::cos(a0), centreY + r0 * std::sin(a0));
            const Vec3 p10 = world(centreX + r0 * std::cos(a1), centreY + r0 * std::sin(a1));
            const Vec3 p11 = world(centreX + r1 * std::cos(a1), centreY + r1 * std::sin(a1));
            const Vec3 p01 = world(centreX + r1 * std::cos(a0), centreY + r1 * std::sin(a0));
            local_.addTriangle({p00, {0, 0}, Vec4(c00, 1.0f)}, {p10, {0, 0}, Vec4(c10, 1.0f)},
                               {p11, {0, 0}, Vec4(c11, 1.0f)});
            local_.addTriangle({p00, {0, 0}, Vec4(c00, 1.0f)}, {p11, {0, 0}, Vec4(c11, 1.0f)},
                               {p01, {0, 0}, Vec4(c01, 1.0f)});
        }
    }

    // Value strip.
    const Rect valueRect{area.x, area.y, area.w, strip};
    const int steps = 24;
    for (int i = 0; i < steps; ++i) {
        const float t0 = float(i) / float(steps);
        const float t1 = float(i + 1) / float(steps);
        const Vec3 c = hsvToRgb(h, s, (t0 + t1) * 0.5f);
        quad({valueRect.x + valueRect.w * t0, valueRect.y, valueRect.w * (t1 - t0),
              valueRect.h},
             Vec4(c, 1.0f));
    }
    outline(valueRect, 0.0012f, theme_.panelEdge);

    bool changed = false;
    const bool triggerDown = pointer_.trigger > 0.5f;
    const bool overDisc =
        panelHovered_ && length(Vec2(pointerLocal_.x - centreX, pointerLocal_.y - centreY)) <=
                             radius * 1.05f;
    const bool overStrip = hit(valueRect);
    if ((overDisc || overStrip) && triggerDown && !triggerWasDown_) activeWidget_ = id;

    if (activeWidget_ == id && triggerDown) {
        if (overStrip || pointerLocal_.y < area.y + strip + theme_.padding * 0.5f) {
            v = saturate((pointerLocal_.x - valueRect.x) / std::max(valueRect.w, 1e-5f));
        } else {
            const float dx = pointerLocal_.x - centreX;
            const float dy = pointerLocal_.y - centreY;
            const float distance = std::sqrt(dx * dx + dy * dy);
            s = saturate(distance / radius);
            float angle = std::atan2(dy, dx);
            if (angle < 0.0f) angle += 2.0f * kPi;
            h = angle / (2.0f * kPi);
        }
        const Vec3 rgb = hsvToRgb(h, s, v);
        *color = Vec4(rgb.x, rgb.y, rgb.z, color->w);
        changed = true;
    }

    // Current colour marker on the disc.
    const float markerAngle = h * 2.0f * kPi;
    const float markerRadius = s * radius;
    const Vec3 marker =
        world(centreX + markerRadius * std::cos(markerAngle),
              centreY + markerRadius * std::sin(markerAngle));
    const Vec3 right = transformDir(panelTransform_, Vec3(1, 0, 0)) * 0.006f;
    const Vec3 up = transformDir(panelTransform_, Vec3(0, 1, 0)) * 0.006f;
    local_.addQuad(marker - right - up, marker + right - up, marker + right + up,
                   marker - right + up, Vec4(1, 1, 1, 1));
    return changed;
}

}  // namespace fcxr
