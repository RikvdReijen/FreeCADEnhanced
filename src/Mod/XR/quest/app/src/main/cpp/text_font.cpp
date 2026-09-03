// SPDX-License-Identifier: LGPL-2.1-or-later
#include "text_font.h"

#include <cstring>
#include <map>

namespace fcxr {
namespace {

// Encoding: polylines separated by '|', points separated by ' ', each point
// two digits "xy" on the 0..8 grid (see text_font.h).
struct GlyphSource {
    char ch;
    float advance;
    const char* strokes;
};

const GlyphSource kGlyphs[] = {
    {' ', 6.0f, ""},
    {'A', 8.0f, "00 38 60|13 53"},
    {'B', 8.0f, "00 08|08 48 66 44 04|04 44 62 40 00"},
    {'C', 8.0f, "66 48 28 06 02 20 40 62"},
    {'D', 8.0f, "00 08|08 38 66 62 30 00"},
    {'E', 8.0f, "60 00 08 68|04 44"},
    {'F', 8.0f, "00 08 68|04 44"},
    {'G', 8.0f, "66 48 28 06 02 20 40 62 63 33"},
    {'H', 8.0f, "00 08|60 68|04 64"},
    {'I', 6.0f, "30 38|18 58|10 50"},
    {'J', 8.0f, "28 68|68 62 40 20 02"},
    {'K', 8.0f, "00 08|68 03 60"},
    {'L', 8.0f, "08 00 60"},
    {'M', 8.0f, "00 08 34 68 60"},
    {'N', 8.0f, "00 08 60 68"},
    {'O', 8.0f, "28 48 66 62 40 20 02 06 28"},
    {'P', 8.0f, "00 08|08 48 66 64 44 04"},
    {'Q', 8.0f, "28 48 66 62 40 20 02 06 28|42 61"},
    {'R', 8.0f, "00 08|08 48 66 64 44 04|34 60"},
    {'S', 8.0f, "67 48 28 06 24 44 62 40 20 01"},
    {'T', 8.0f, "08 68|38 30"},
    {'U', 8.0f, "08 02 20 40 62 68"},
    {'V', 8.0f, "08 30 68"},
    {'W', 8.0f, "08 10 34 50 68"},
    {'X', 8.0f, "00 68|08 60"},
    {'Y', 8.0f, "08 34 68|34 30"},
    {'Z', 8.0f, "08 68 00 60"},
    {'0', 8.0f, "28 48 66 62 40 20 02 06 28|11 57"},
    {'1', 6.0f, "16 38 30|10 50"},
    {'2', 8.0f, "06 28 48 66 64 00 60"},
    {'3', 8.0f, "07 28 48 66 44|44 64 62 40 20 01"},
    {'4', 8.0f, "48 03 63|40 48"},
    {'5', 8.0f, "68 08 04 44 62 40 20 01"},
    {'6', 8.0f, "58 28 06 02 20 40 62 44 04"},
    {'7', 8.0f, "08 68 20"},
    {'8', 8.0f, "24 46 48 28 06 24|24 44 62 40 20 02 24"},
    {'9', 8.0f, "44 24 06 28 48 66 62 40 20"},
    {'.', 4.0f, "10 11"},
    {',', 4.0f, "21 10"},
    {':', 4.0f, "11 12|15 16"},
    {';', 4.0f, "20 11 12|15 16"},
    {'-', 8.0f, "14 54"},
    {'+', 8.0f, "14 54|32 36"},
    {'=', 8.0f, "13 53|15 55"},
    {'*', 8.0f, "34 36|24 46|44 26"},
    {'/', 8.0f, "00 68"},
    {'\\', 8.0f, "08 60"},
    {'(', 5.0f, "38 16 12 30"},
    {')', 5.0f, "18 36 32 10"},
    {'[', 5.0f, "38 18 10 30"},
    {']', 5.0f, "18 38 30 10"},
    {'<', 8.0f, "56 24 52"},
    {'>', 8.0f, "16 44 12"},
    {'!', 4.0f, "18 13|10 11"},
    {'?', 8.0f, "06 28 48 66 44 33|31 32"},
    {'\'', 4.0f, "18 16"},
    {'"', 6.0f, "18 16|38 36"},
    {'_', 8.0f, "00 60"},
    {'#', 8.0f, "17 13|47 43|05 65|02 62"},
    {'%', 8.0f, "08 60|06 16 17 07 06|51 61 62 52 51"},
    {'@', 8.0f, "56 46 36 34 54 55 26 06 02 40 60"},
    {'&', 8.0f, "60 16 38 56 04 20 41 62"},
    {'|', 4.0f, "10 18"},
};

// Parses one encoded glyph.
Glyph decodeGlyph(const GlyphSource& src) {
    Glyph g;
    g.advance = src.advance;
    const char* p = src.strokes;
    std::vector<Vec2> current;
    while (*p) {
        if (*p == '|') {
            if (current.size() >= 2) g.strokes.push_back(current);
            current.clear();
            ++p;
        } else if (*p == ' ') {
            ++p;
        } else if (p[0] >= '0' && p[0] <= '8' && p[1] >= '0' && p[1] <= '8') {
            current.push_back(Vec2(float(p[0] - '0'), float(p[1] - '0')));
            p += 2;
        } else {
            ++p;  // ignore anything unexpected rather than mis-parsing
        }
    }
    if (current.size() >= 2) g.strokes.push_back(current);
    return g;
}

const std::map<char, Glyph>& glyphTable() {
    static const std::map<char, Glyph>* table = [] {
        auto* t = new std::map<char, Glyph>();
        for (const GlyphSource& src : kGlyphs) (*t)[src.ch] = decodeGlyph(src);
        return t;
    }();
    return *table;
}

char normaliseChar(char c) {
    if (c >= 'a' && c <= 'z') return char(c - 'a' + 'A');
    return c;
}

}  // namespace

const Glyph& fontGlyph(char c) {
    static const Glyph kEmpty;
    const std::map<char, Glyph>& t = glyphTable();
    auto it = t.find(normaliseChar(c));
    return it == t.end() ? kEmpty : it->second;
}

float fontTextWidth(const std::string& text, float height) {
    const float scale = height / kFontUnitsPerEm;
    float best = 0.0f, line = 0.0f;
    for (size_t i = 0; i < text.size(); ++i) {
        if (text[i] == '\n') {
            best = std::max(best, line);
            line = 0.0f;
            continue;
        }
        line += fontGlyph(text[i]).advance + kFontTracking;
    }
    best = std::max(best, line);
    // The trailing tracking is not part of the visible width.
    if (best > 0.0f) best -= kFontTracking;
    return best * scale;
}

int fontLineCount(const std::string& text) {
    int lines = 1;
    for (char c : text) {
        if (c == '\n') ++lines;
    }
    return lines;
}

void fontLayout(const std::string& text, float height,
                std::vector<std::vector<Vec2>>* out) {
    if (!out) return;
    out->clear();
    const float scale = height / kFontUnitsPerEm;
    const float lineStep = height * 1.6f;
    float penX = 0.0f;
    float penY = 0.0f;
    for (size_t i = 0; i < text.size(); ++i) {
        const char c = text[i];
        if (c == '\n') {
            penX = 0.0f;
            penY -= lineStep;
            continue;
        }
        const Glyph& g = fontGlyph(c);
        for (const std::vector<Vec2>& stroke : g.strokes) {
            std::vector<Vec2> pts;
            pts.reserve(stroke.size());
            for (const Vec2& p : stroke)
                pts.push_back(Vec2(penX + p.x * scale, penY + p.y * scale));
            out->push_back(std::move(pts));
        }
        penX += (g.advance + kFontTracking) * scale;
    }
}

}  // namespace fcxr
