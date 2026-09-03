// SPDX-License-Identifier: LGPL-2.1-or-later
//
// A built-in single-stroke vector font.
//
// The app ships no font file: the same glyph outlines feed the §2 `text`
// primitive (extruded into solid geometry) and the in-VR UI text renderer
// (drawn as thin quads). Glyphs are polylines on an 8 unit grid:
//
//     y = 0 is the baseline, y = 8 is the cap height, x grows to the right.
//
// Lowercase letters are mapped to uppercase. Unknown characters render
// nothing but still advance, so layout never depends on coverage.
#pragma once

#include <string>
#include <vector>

#include "math3d.h"

namespace fcxr {

// One glyph: a set of open polylines in grid units, plus the pen advance.
struct Glyph {
    std::vector<std::vector<Vec2>> strokes;
    float advance = 8.0f;
};

// Grid units per cap height. Scale a glyph by (height / kFontUnitsPerEm) to
// get a cap height of `height` metres.
static constexpr float kFontUnitsPerEm = 8.0f;
// Extra space inserted between glyphs, in grid units.
static constexpr float kFontTracking = 1.0f;
// Stroke thickness used when a stroke has to be given width, as a fraction of
// the cap height.
static constexpr float kFontStrokeWidth = 0.11f;

// Returns the glyph for `c` (never null; unknown characters give an empty
// glyph with a normal advance).
const Glyph& fontGlyph(char c);

// Width of `text` in metres at the given cap height, honouring '\n'
// (the widest line wins).
float fontTextWidth(const std::string& text, float height);

// Number of lines in `text` (at least 1).
int fontLineCount(const std::string& text);

// Lays `text` out and returns every stroke in metres, with the first line's
// baseline at y = 0 and subsequent lines below it. `height` is the cap
// height; lines advance by height * 1.6.
void fontLayout(const std::string& text, float height,
                std::vector<std::vector<Vec2>>* strokesOut);

}  // namespace fcxr
