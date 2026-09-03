// SPDX-License-Identifier: LGPL-2.1-or-later
//
// The built-in single stroke font.
//
// The glyph outlines are *generated* from the Python reference tessellator
// (`xrenv.spec._GLYPHS`) into glyph_table.inc by quest/tools/gen_glyphs.py, so
// the §2 `text` primitive extrudes exactly the same letters on the headset as
// the desktop draws in Coin. The same outlines are reused for the in-VR UI.
//
// Glyph coordinates live in a 0..1 box: y = 0 is the baseline, y = 1 the cap
// height, x = 0..0.56 (kGlyphAdvance = 0.78 of the cap height per character).
// Lowercase is mapped to uppercase; unknown characters draw nothing but still
// advance, exactly as the Python layout does.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "math3d.h"

namespace fcxr {

// Layout metrics, mirrored from xrenv/spec.py (see glyph_table.inc).
float fontAdvance();     // _TEXT_ADVANCE, relative to the cap height
float fontGlyphWidth();  // _GLYPH_W
float fontStroke();      // _TEXT_STROKE
float fontLinePitch();   // line pitch relative to the cap height

// One glyph as polylines in the 0..1 box. Empty for unknown characters.
const std::vector<std::vector<Vec2>>& fontGlyph(uint32_t codepoint);

// xrenv.spec.text_metrics(): the block size of `text` at cap height `height`.
void fontTextMetrics(const std::string& utf8, float height, float* width, float* heightOut);
inline float fontTextWidth(const std::string& utf8, float height) {
    float w = 0.0f, h = 0.0f;
    fontTextMetrics(utf8, height, &w, &h);
    return w;
}

// Lays `utf8` out into stroke polylines, in metres.
//   centred = true  : the block is centred on the origin, which is what
//                     `_tess_text` does (used by the `text` primitive).
//   centred = false : the first line's baseline sits at y = 0 and text grows
//                     to the right from x = 0 (used by the UI).
void fontLayout(const std::string& utf8, float height, bool centred,
                std::vector<std::vector<Vec2>>* strokesOut);

// Decodes the next UTF-8 code point at `i`, advancing `i`. Invalid bytes are
// returned as U+FFFD and consume one byte.
uint32_t utf8Next(const std::string& s, size_t* i);
// Number of code points (what Python's len(str) would report).
size_t utf8Length(const std::string& s);

}  // namespace fcxr
