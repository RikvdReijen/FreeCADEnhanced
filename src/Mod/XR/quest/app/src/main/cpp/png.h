// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Self-contained PNG decoding/encoding on top of zlib (which ships with the
// NDK), so the app needs no image library.
//
// Supported on decode: all five colour types, bit depths 1/2/4/8/16,
// PLTE + tRNS, gAMA is ignored (PNG data is assumed sRGB, which is what the
// workbench writes). Interlaced (Adam7) images are *rejected* — the desktop
// side never produces them; see README.
//
// Everything is decoded to straight (non-premultiplied) RGBA8.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace fcxr {

struct Image {
    int width = 0;
    int height = 0;
    std::vector<uint8_t> rgba;  // width * height * 4, row 0 is the top row
    bool valid() const {
        return width > 0 && height > 0 &&
               rgba.size() == static_cast<size_t>(width) * height * 4;
    }
    uint8_t* pixel(int x, int y) { return &rgba[(static_cast<size_t>(y) * width + x) * 4]; }
    const uint8_t* pixel(int x, int y) const {
        return &rgba[(static_cast<size_t>(y) * width + x) * 4];
    }
};

// Returns false and fills `error` on failure.
bool pngDecode(const uint8_t* data, size_t size, Image* out, std::string* error = nullptr);

// Encodes RGBA8 (channels == 4) or RGB8 (channels == 3) with adaptive
// per-row filtering. `level` is the zlib compression level (0..9).
bool pngEncode(const uint8_t* pixels, int width, int height, int channels,
               std::vector<uint8_t>* out, int level = 6, std::string* error = nullptr);

inline bool pngEncode(const Image& img, std::vector<uint8_t>* out, int level = 6,
                      std::string* error = nullptr) {
    return pngEncode(img.rgba.data(), img.width, img.height, 4, out, level, error);
}

}  // namespace fcxr
