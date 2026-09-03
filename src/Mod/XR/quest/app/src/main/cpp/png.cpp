// SPDX-License-Identifier: LGPL-2.1-or-later
#include "png.h"

#include <zlib.h>

#include <cstring>

namespace fcxr {
namespace {

const uint8_t kSignature[8] = {0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'};

inline uint32_t readU32(const uint8_t* p) {
    return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) | p[3];
}
inline void writeU32(std::vector<uint8_t>& out, uint32_t v) {
    out.push_back(uint8_t(v >> 24));
    out.push_back(uint8_t(v >> 16));
    out.push_back(uint8_t(v >> 8));
    out.push_back(uint8_t(v));
}

bool setError(std::string* error, const char* msg) {
    if (error) *error = msg;
    return false;
}

int channelsForColourType(int ct) {
    switch (ct) {
        case 0: return 1;  // greyscale
        case 2: return 3;  // truecolour
        case 3: return 1;  // indexed
        case 4: return 2;  // greyscale + alpha
        case 6: return 4;  // truecolour + alpha
        default: return 0;
    }
}

inline int paeth(int a, int b, int c) {
    int p = a + b - c;
    int pa = p > a ? p - a : a - p;
    int pb = p > b ? p - b : b - p;
    int pc = p > c ? p - c : c - p;
    if (pa <= pb && pa <= pc) return a;
    if (pb <= pc) return b;
    return c;
}

// Reverses one scanline's filter in place. `bpp` is bytes per pixel rounded up.
void unfilterRow(uint8_t filter, uint8_t* cur, const uint8_t* prev, size_t len, size_t bpp) {
    switch (filter) {
        case 0: break;
        case 1:  // Sub
            for (size_t i = bpp; i < len; ++i) cur[i] = uint8_t(cur[i] + cur[i - bpp]);
            break;
        case 2:  // Up
            if (prev) {
                for (size_t i = 0; i < len; ++i) cur[i] = uint8_t(cur[i] + prev[i]);
            }
            break;
        case 3:  // Average
            for (size_t i = 0; i < len; ++i) {
                int a = i >= bpp ? cur[i - bpp] : 0;
                int b = prev ? prev[i] : 0;
                cur[i] = uint8_t(cur[i] + ((a + b) >> 1));
            }
            break;
        case 4:  // Paeth
            for (size_t i = 0; i < len; ++i) {
                int a = i >= bpp ? cur[i - bpp] : 0;
                int b = prev ? prev[i] : 0;
                int c = (prev && i >= bpp) ? prev[i - bpp] : 0;
                cur[i] = uint8_t(cur[i] + paeth(a, b, c));
            }
            break;
        default: break;  // caller validated
    }
}

// Extracts the `index`-th sample of `bitDepth` bits from a packed row.
inline uint32_t sampleAt(const uint8_t* row, size_t index, int bitDepth) {
    switch (bitDepth) {
        case 8: return row[index];
        case 16: return (uint32_t(row[index * 2]) << 8) | row[index * 2 + 1];
        case 4: return (row[index >> 1] >> (index & 1 ? 0 : 4)) & 0x0F;
        case 2: return (row[index >> 2] >> (6 - 2 * (index & 3))) & 0x03;
        case 1: return (row[index >> 3] >> (7 - (index & 7))) & 0x01;
        default: return 0;
    }
}

// Scales a sample of `bitDepth` bits to 0..255.
inline uint8_t scaleSample(uint32_t s, int bitDepth) {
    switch (bitDepth) {
        case 16: return uint8_t(s >> 8);
        case 8: return uint8_t(s);
        case 4: return uint8_t(s * 17);            // 0..15  -> 0..255
        case 2: return uint8_t(s * 85);            // 0..3   -> 0..255
        case 1: return uint8_t(s ? 255 : 0);
        default: return 0;
    }
}

}  // namespace

bool pngDecode(const uint8_t* data, size_t size, Image* out, std::string* error) {
    if (!out) return false;
    if (size < 8 || std::memcmp(data, kSignature, 8) != 0)
        return setError(error, "not a PNG file");

    uint32_t width = 0, height = 0;
    int bitDepth = 0, colourType = 0, interlace = 0;
    bool haveIhdr = false;
    std::vector<uint8_t> palette;    // RGB triples
    std::vector<uint8_t> paletteA;   // alpha per palette entry
    std::vector<uint8_t> idat;
    bool haveTrnsColour = false;
    uint32_t trnsColour[3] = {0, 0, 0};

    size_t pos = 8;
    while (pos + 8 <= size) {
        uint32_t len = readU32(data + pos);
        if (len > 0x7FFFFFFFu || pos + 12 + len > size)
            return setError(error, "truncated PNG chunk");
        const char* type = reinterpret_cast<const char*>(data + pos + 4);
        const uint8_t* payload = data + pos + 8;

        if (!std::memcmp(type, "IHDR", 4)) {
            if (len != 13) return setError(error, "bad IHDR length");
            width = readU32(payload);
            height = readU32(payload + 4);
            bitDepth = payload[8];
            colourType = payload[9];
            if (payload[10] != 0) return setError(error, "unsupported PNG compression method");
            if (payload[11] != 0) return setError(error, "unsupported PNG filter method");
            interlace = payload[12];
            haveIhdr = true;
            if (width == 0 || height == 0) return setError(error, "zero sized PNG");
            if (width > 16384 || height > 16384) return setError(error, "PNG too large");
            if (interlace != 0) return setError(error, "interlaced PNG is not supported");
            if (!channelsForColourType(colourType))
                return setError(error, "unsupported PNG colour type");
            const bool depthOk =
                (colourType == 3 && (bitDepth == 1 || bitDepth == 2 || bitDepth == 4 || bitDepth == 8)) ||
                (colourType == 0 && (bitDepth == 1 || bitDepth == 2 || bitDepth == 4 ||
                                     bitDepth == 8 || bitDepth == 16)) ||
                ((colourType == 2 || colourType == 4 || colourType == 6) &&
                 (bitDepth == 8 || bitDepth == 16));
            if (!depthOk) return setError(error, "unsupported PNG bit depth");
        } else if (!std::memcmp(type, "PLTE", 4)) {
            if (len % 3 || len == 0 || len > 256 * 3) return setError(error, "bad PLTE");
            palette.assign(payload, payload + len);
        } else if (!std::memcmp(type, "tRNS", 4)) {
            if (colourType == 3) {
                paletteA.assign(payload, payload + len);
            } else if (colourType == 0 && len >= 2) {
                haveTrnsColour = true;
                trnsColour[0] = trnsColour[1] = trnsColour[2] =
                    (uint32_t(payload[0]) << 8) | payload[1];
            } else if (colourType == 2 && len >= 6) {
                haveTrnsColour = true;
                for (int i = 0; i < 3; ++i)
                    trnsColour[i] = (uint32_t(payload[i * 2]) << 8) | payload[i * 2 + 1];
            }
        } else if (!std::memcmp(type, "IDAT", 4)) {
            if (!haveIhdr) return setError(error, "IDAT before IHDR");
            idat.insert(idat.end(), payload, payload + len);
        } else if (!std::memcmp(type, "IEND", 4)) {
            break;
        }
        pos += 12 + len;  // length + type + payload + CRC
    }

    if (!haveIhdr) return setError(error, "missing IHDR");
    if (idat.empty()) return setError(error, "missing IDAT");
    if (colourType == 3 && palette.empty()) return setError(error, "indexed PNG without PLTE");

    const int channels = channelsForColourType(colourType);
    const size_t rowBits = static_cast<size_t>(width) * channels * bitDepth;
    const size_t rowBytes = (rowBits + 7) / 8;
    const size_t bpp = (static_cast<size_t>(channels) * bitDepth + 7) / 8;  // >= 1

    // Inflate the concatenated IDAT payload.
    std::vector<uint8_t> raw;
    raw.resize((rowBytes + 1) * height);
    {
        z_stream zs;
        std::memset(&zs, 0, sizeof(zs));
        if (inflateInit(&zs) != Z_OK) return setError(error, "inflateInit failed");
        zs.next_in = const_cast<Bytef*>(idat.data());
        zs.avail_in = static_cast<uInt>(idat.size());
        zs.next_out = raw.data();
        zs.avail_out = static_cast<uInt>(raw.size());
        int rc = inflate(&zs, Z_FINISH);
        const size_t produced = raw.size() - zs.avail_out;
        inflateEnd(&zs);
        if (produced != raw.size() && rc != Z_STREAM_END)
            return setError(error, "PNG inflate failed or short image data");
    }

    out->width = static_cast<int>(width);
    out->height = static_cast<int>(height);
    out->rgba.assign(static_cast<size_t>(width) * height * 4, 0);

    std::vector<uint8_t> prevRow(rowBytes, 0);
    std::vector<uint8_t> curRow(rowBytes, 0);

    for (uint32_t y = 0; y < height; ++y) {
        const uint8_t* src = raw.data() + static_cast<size_t>(y) * (rowBytes + 1);
        uint8_t filter = src[0];
        if (filter > 4) return setError(error, "invalid PNG row filter");
        std::memcpy(curRow.data(), src + 1, rowBytes);
        unfilterRow(filter, curRow.data(), y ? prevRow.data() : nullptr, rowBytes, bpp);

        uint8_t* dst = out->rgba.data() + static_cast<size_t>(y) * width * 4;
        for (uint32_t x = 0; x < width; ++x, dst += 4) {
            switch (colourType) {
                case 0: {  // greyscale
                    uint32_t s = sampleAt(curRow.data(), x, bitDepth);
                    uint8_t g = scaleSample(s, bitDepth);
                    dst[0] = dst[1] = dst[2] = g;
                    dst[3] = (haveTrnsColour && s == trnsColour[0]) ? 0 : 255;
                    break;
                }
                case 2: {  // RGB
                    uint32_t r = sampleAt(curRow.data(), x * 3 + 0, bitDepth);
                    uint32_t g = sampleAt(curRow.data(), x * 3 + 1, bitDepth);
                    uint32_t b = sampleAt(curRow.data(), x * 3 + 2, bitDepth);
                    dst[0] = scaleSample(r, bitDepth);
                    dst[1] = scaleSample(g, bitDepth);
                    dst[2] = scaleSample(b, bitDepth);
                    dst[3] = (haveTrnsColour && r == trnsColour[0] && g == trnsColour[1] &&
                              b == trnsColour[2])
                                 ? 0
                                 : 255;
                    break;
                }
                case 3: {  // indexed
                    uint32_t idx = sampleAt(curRow.data(), x, bitDepth);
                    if (idx * 3 + 2 >= palette.size()) return setError(error, "palette index out of range");
                    dst[0] = palette[idx * 3 + 0];
                    dst[1] = palette[idx * 3 + 1];
                    dst[2] = palette[idx * 3 + 2];
                    dst[3] = idx < paletteA.size() ? paletteA[idx] : 255;
                    break;
                }
                case 4: {  // grey + alpha
                    uint8_t g = scaleSample(sampleAt(curRow.data(), x * 2 + 0, bitDepth), bitDepth);
                    uint8_t a = scaleSample(sampleAt(curRow.data(), x * 2 + 1, bitDepth), bitDepth);
                    dst[0] = dst[1] = dst[2] = g;
                    dst[3] = a;
                    break;
                }
                default: {  // 6: RGBA
                    for (int c = 0; c < 4; ++c)
                        dst[c] = scaleSample(sampleAt(curRow.data(), x * 4 + c, bitDepth), bitDepth);
                    break;
                }
            }
        }
        curRow.swap(prevRow);
    }
    return true;
}

bool pngEncode(const uint8_t* pixels, int width, int height, int channels,
               std::vector<uint8_t>* out, int level, std::string* error) {
    if (!out || !pixels) return setError(error, "null argument");
    if (width <= 0 || height <= 0) return setError(error, "empty image");
    if (channels != 3 && channels != 4) return setError(error, "channels must be 3 or 4");

    const size_t bpp = static_cast<size_t>(channels);
    const size_t rowBytes = static_cast<size_t>(width) * bpp;

    // Filter every row with all five filters and keep the one with the
    // smallest sum of absolute (signed) differences — the heuristic from the
    // PNG spec, and what libpng does by default.
    std::vector<uint8_t> filtered;
    filtered.resize((rowBytes + 1) * static_cast<size_t>(height));
    std::vector<uint8_t> candidate(rowBytes);
    std::vector<uint8_t> best(rowBytes);

    for (int y = 0; y < height; ++y) {
        const uint8_t* cur = pixels + static_cast<size_t>(y) * rowBytes;
        const uint8_t* prev = y ? pixels + static_cast<size_t>(y - 1) * rowBytes : nullptr;
        uint32_t bestScore = 0xFFFFFFFFu;
        uint8_t bestFilter = 0;
        for (int f = 0; f < 5; ++f) {
            for (size_t i = 0; i < rowBytes; ++i) {
                int a = i >= bpp ? cur[i - bpp] : 0;
                int b = prev ? prev[i] : 0;
                int c = (prev && i >= bpp) ? prev[i - bpp] : 0;
                int v;
                switch (f) {
                    case 0: v = cur[i]; break;
                    case 1: v = cur[i] - a; break;
                    case 2: v = cur[i] - b; break;
                    case 3: v = cur[i] - ((a + b) >> 1); break;
                    default: v = cur[i] - paeth(a, b, c); break;
                }
                candidate[i] = uint8_t(v);
            }
            uint32_t score = 0;
            for (size_t i = 0; i < rowBytes; ++i) {
                int8_t sv = static_cast<int8_t>(candidate[i]);
                score += uint32_t(sv < 0 ? -sv : sv);
            }
            if (score < bestScore) {
                bestScore = score;
                bestFilter = uint8_t(f);
                best.swap(candidate);
            }
        }
        uint8_t* dst = filtered.data() + static_cast<size_t>(y) * (rowBytes + 1);
        dst[0] = bestFilter;
        std::memcpy(dst + 1, best.data(), rowBytes);
    }

    uLongf compressedCap = compressBound(static_cast<uLong>(filtered.size()));
    std::vector<uint8_t> compressed(compressedCap);
    if (compress2(compressed.data(), &compressedCap, filtered.data(),
                  static_cast<uLong>(filtered.size()), level) != Z_OK)
        return setError(error, "deflate failed");
    compressed.resize(compressedCap);

    out->clear();
    out->reserve(compressed.size() + 128);
    out->insert(out->end(), kSignature, kSignature + 8);

    auto chunk = [&](const char* type, const uint8_t* payload, size_t len) {
        writeU32(*out, static_cast<uint32_t>(len));
        const size_t crcStart = out->size();
        out->insert(out->end(), type, type + 4);
        if (len) out->insert(out->end(), payload, payload + len);
        uLong crc = crc32(0L, Z_NULL, 0);
        crc = crc32(crc, out->data() + crcStart, static_cast<uInt>(4 + len));
        writeU32(*out, static_cast<uint32_t>(crc));
    };

    uint8_t ihdr[13];
    ihdr[0] = uint8_t(uint32_t(width) >> 24);
    ihdr[1] = uint8_t(uint32_t(width) >> 16);
    ihdr[2] = uint8_t(uint32_t(width) >> 8);
    ihdr[3] = uint8_t(uint32_t(width));
    ihdr[4] = uint8_t(uint32_t(height) >> 24);
    ihdr[5] = uint8_t(uint32_t(height) >> 16);
    ihdr[6] = uint8_t(uint32_t(height) >> 8);
    ihdr[7] = uint8_t(uint32_t(height));
    ihdr[8] = 8;                                  // bit depth
    ihdr[9] = uint8_t(channels == 4 ? 6 : 2);     // colour type
    ihdr[10] = 0;                                 // compression
    ihdr[11] = 0;                                 // filter
    ihdr[12] = 0;                                 // interlace
    chunk("IHDR", ihdr, sizeof(ihdr));
    chunk("IDAT", compressed.data(), compressed.size());
    chunk("IEND", nullptr, 0);
    return true;
}

}  // namespace fcxr
