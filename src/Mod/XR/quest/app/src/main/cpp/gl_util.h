// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Thin GLES 3.2 helpers: shader compilation, the multisampled render target
// the eye views are drawn into, and texture creation.
#pragma once

#include <GLES3/gl32.h>

#include <string>
#include <vector>

#include "png.h"

namespace fcxr {

// Logs and clears any pending GL error. `where` names the call site.
void glCheck(const char* where);

// Compiles and links a program from GLSL ES sources. Returns 0 on failure
// (with the compiler log written to logcat).
GLuint glCompileProgram(const char* vertexSource, const char* fragmentSource,
                        const char* debugName);

// Colour + depth render target for one eye.
//
// Quest's OpenXR swapchain images are plain 2D textures, so multisampling has
// to be arranged by us. Three paths, best first:
//   1. GL_EXT_multisampled_render_to_texture — render straight into the
//      swapchain texture with an implicit, tile-local resolve (free on the
//      Adreno tiler; this is what every Meta sample uses).
//   2. explicit multisampled renderbuffers + glBlitFramebuffer resolve.
//   3. no multisampling.
class EyeTarget {
public:
    ~EyeTarget();
    // `samples` is a request; the class silently drops to what is supported.
    bool create(int width, int height, int samples);
    void destroy();

    // Binds the framebuffer for `colorTexture` (one of the swapchain images),
    // creating the per-image framebuffer on first use.
    void bind(GLuint colorTexture);
    // Resolves (path 2 only) and unbinds.
    void resolve(GLuint colorTexture);

    int width() const { return width_; }
    int height() const { return height_; }
    int samples() const { return samples_; }

private:
    struct Attachment {
        GLuint texture = 0;
        GLuint fbo = 0;
    };
    Attachment* attachmentFor(GLuint colorTexture);

    int width_ = 0, height_ = 0, samples_ = 1;
    bool implicitResolve_ = false;
    GLuint msaaColor_ = 0;   // path 2 colour renderbuffer
    GLuint msaaDepth_ = 0;   // depth renderbuffer (both MSAA paths)
    GLuint msaaFbo_ = 0;     // path 2 draw framebuffer
    std::vector<Attachment> attachments_;
};

// Uploads an RGBA8 image. `srgb` selects GL_SRGB8_ALPHA8 so the sampler
// returns linear values (all PNG content is sRGB encoded).
GLuint glCreateTexture2D(const Image& image, bool srgb, bool mipmap);
// Empty texture for painting into; always linear-from-sRGB on sample.
GLuint glCreateEmptyTexture2D(int width, int height, bool srgb);
// 1x1 white texture used when a material has no map.
GLuint glCreateWhiteTexture();
// Procedural textures referenced by environment materials ("checker", "grid",
// "noise"); returns 0 for an unknown name.
GLuint glCreateProceduralTexture(const std::string& name);

}  // namespace fcxr
