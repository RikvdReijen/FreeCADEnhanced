// SPDX-License-Identifier: LGPL-2.1-or-later
#include "text_font.h"

#include <algorithm>

namespace fcxr {
namespace {

struct GlyphEntry {
    uint32_t codepoint;
    const signed char* data;
};

#include "glyph_table.inc"

// Decoded form, built once.
struct Glyph {
    std::vector<std::vector<Vec2>> strokes;
};

const std::vector<Glyph>& decodedGlyphs() {
    static const std::vector<Glyph>* table = [] {
        auto* t = new std::vector<Glyph>();
        t->reserve(sizeof(kGlyphTable) / sizeof(kGlyphTable[0]));
        for (const GlyphEntry& e : kGlyphTable) {
            Glyph g;
            const signed char* p = e.data;
            const int polylineCount = *p++;
            for (int i = 0; i < polylineCount; ++i) {
                const int points = *p++;
                std::vector<Vec2> stroke;
                stroke.reserve(size_t(points));
                for (int k = 0; k < points; ++k) {
                    const float x = float(int(p[0])) / 100.0f;
                    const float y = float(int(p[1])) / 100.0f;
                    p += 2;
                    stroke.push_back(Vec2(x, y));
                }
                g.strokes.push_back(std::move(stroke));
            }
            t->push_back(std::move(g));
        }
        return t;
    }();
    return *table;
}

// ASCII uppercasing, which is all the table needs (it holds no lowercase).
uint32_t toUpper(uint32_t cp) {
    return (cp >= 'a' && cp <= 'z') ? cp - 'a' + 'A' : cp;
}

int glyphIndex(uint32_t codepoint) {
    // kGlyphTable is generated in ascending codepoint order.
    const size_t n = sizeof(kGlyphTable) / sizeof(kGlyphTable[0]);
    size_t lo = 0, hi = n;
    while (lo < hi) {
        const size_t mid = (lo + hi) / 2;
        if (kGlyphTable[mid].codepoint < codepoint) lo = mid + 1;
        else hi = mid;
    }
    if (lo < n && kGlyphTable[lo].codepoint == codepoint) return int(lo);
    return -1;
}

}  // namespace

float fontAdvance() { return kGlyphAdvance; }
float fontGlyphWidth() { return kGlyphWidth; }
float fontStroke() { return kGlyphStroke; }
float fontLinePitch() { return kGlyphLinePitch; }

uint32_t utf8Next(const std::string& s, size_t* i) {
    if (!i || *i >= s.size()) return 0;
    const unsigned char* p = reinterpret_cast<const unsigned char*>(s.data());
    const size_t n = s.size();
    size_t k = *i;
    const unsigned char c = p[k];
    auto cont = [&](size_t off) { return k + off < n && (p[k + off] & 0xC0) == 0x80; };
    if (c < 0x80) {
        *i = k + 1;
        return c;
    }
    if ((c & 0xE0) == 0xC0 && cont(1)) {
        *i = k + 2;
        return uint32_t((c & 0x1Fu) << 6) | (p[k + 1] & 0x3Fu);
    }
    if ((c & 0xF0) == 0xE0 && cont(1) && cont(2)) {
        *i = k + 3;
        return (uint32_t(c & 0x0Fu) << 12) | (uint32_t(p[k + 1] & 0x3Fu) << 6) |
               (p[k + 2] & 0x3Fu);
    }
    if ((c & 0xF8) == 0xF0 && cont(1) && cont(2) && cont(3)) {
        *i = k + 4;
        return (uint32_t(c & 0x07u) << 18) | (uint32_t(p[k + 1] & 0x3Fu) << 12) |
               (uint32_t(p[k + 2] & 0x3Fu) << 6) | (p[k + 3] & 0x3Fu);
    }
    *i = k + 1;
    return 0xFFFD;
}

size_t utf8Length(const std::string& s) {
    size_t i = 0, count = 0;
    while (i < s.size()) {
        utf8Next(s, &i);
        ++count;
    }
    return count;
}

const std::vector<std::vector<Vec2>>& fontGlyph(uint32_t codepoint) {
    static const std::vector<std::vector<Vec2>> kEmpty;
    const int index = glyphIndex(toUpper(codepoint));
    if (index < 0) return kEmpty;
    return decodedGlyphs()[size_t(index)].strokes;
}

// Mirrors xrenv.spec.text_metrics().
void fontTextMetrics(const std::string& utf8, float height, float* width, float* heightOut) {
    size_t longest = 0;
    size_t lines = 1;
    size_t current = 0;
    size_t i = 0;
    while (i < utf8.size()) {
        const uint32_t cp = utf8Next(utf8, &i);
        if (cp == '\n') {
            longest = std::max(longest, current);
            current = 0;
            ++lines;
        } else {
            ++current;
        }
    }
    longest = std::max(longest, current);
    if (width) {
        const float w =
            (float(longest) * kGlyphAdvance - (kGlyphAdvance - kGlyphWidth)) * height;
        *width = w > 0.0f ? w : 0.0f;
    }
    if (heightOut)
        *heightOut = (float(lines) + float(lines - 1) * (kGlyphLinePitch - 1.0f)) * height;
}

void fontLayout(const std::string& utf8, float height, bool centred,
                std::vector<std::vector<Vec2>>* out) {
    if (!out) return;
    out->clear();

    // Split into lines of code points.
    std::vector<std::vector<uint32_t>> lines(1);
    size_t i = 0;
    while (i < utf8.size()) {
        const uint32_t cp = utf8Next(utf8, &i);
        if (cp == '\n') lines.emplace_back();
        else lines.back().push_back(cp);
    }

    float totalHeight = 0.0f;
    fontTextMetrics(utf8, height, nullptr, &totalHeight);
    const float linePitch = height * kGlyphLinePitch;

    for (size_t li = 0; li < lines.size(); ++li) {
        const std::vector<uint32_t>& line = lines[li];
        float lineWidth =
            (float(line.size()) * kGlyphAdvance - (kGlyphAdvance - kGlyphWidth)) * height;
        if (lineWidth < 0.0f) lineWidth = 0.0f;
        // Centred: the block straddles the origin exactly as _tess_text lays it
        // out. Otherwise: left aligned with the first baseline at y = 0.
        const float x0 = centred ? -lineWidth * 0.5f : 0.0f;
        const float y0 = centred ? (totalHeight * 0.5f - height - float(li) * linePitch)
                                 : -float(li) * linePitch;
        for (size_t ci = 0; ci < line.size(); ++ci) {
            const std::vector<std::vector<Vec2>>& glyph = fontGlyph(line[ci]);
            if (glyph.empty()) continue;
            const float gx = x0 + float(ci) * kGlyphAdvance * height;
            for (const std::vector<Vec2>& poly : glyph) {
                std::vector<Vec2> stroke;
                stroke.reserve(poly.size());
                for (const Vec2& p : poly)
                    stroke.push_back(Vec2(gx + p.x * height, y0 + p.y * height));
                out->push_back(std::move(stroke));
            }
        }
    }
}

}  // namespace fcxr
