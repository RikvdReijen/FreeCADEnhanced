// SPDX-License-Identifier: LGPL-2.1-or-later
#include "gl_util.h"

#include <EGL/egl.h>
#include <GLES2/gl2ext.h>

#include <cmath>
#include <cstring>

#include "log.h"

namespace fcxr {
namespace {

// GL_EXT_multisampled_render_to_texture, resolved once.
typedef void(GL_APIENTRY* PFNRenderbufferStorageMultisampleEXT)(GLenum, GLsizei, GLenum,
                                                                GLsizei, GLsizei);
typedef void(GL_APIENTRY* PFNFramebufferTexture2DMultisampleEXT)(GLenum, GLenum, GLenum,
                                                                 GLuint, GLint, GLsizei);
PFNRenderbufferStorageMultisampleEXT gRenderbufferStorageMultisampleEXT = nullptr;
PFNFramebufferTexture2DMultisampleEXT gFramebufferTexture2DMultisampleEXT = nullptr;
bool gExtensionsResolved = false;

bool hasExtension(const char* name) {
    GLint count = 0;
    glGetIntegerv(GL_NUM_EXTENSIONS, &count);
    for (GLint i = 0; i < count; ++i) {
        const char* e = reinterpret_cast<const char*>(glGetStringi(GL_EXTENSIONS, GLuint(i)));
        if (e && !std::strcmp(e, name)) return true;
    }
    return false;
}

void resolveExtensions() {
    if (gExtensionsResolved) return;
    gExtensionsResolved = true;
    if (hasExtension("GL_EXT_multisampled_render_to_texture")) {
        gRenderbufferStorageMultisampleEXT = reinterpret_cast<PFNRenderbufferStorageMultisampleEXT>(
            eglGetProcAddress("glRenderbufferStorageMultisampleEXT"));
        gFramebufferTexture2DMultisampleEXT =
            reinterpret_cast<PFNFramebufferTexture2DMultisampleEXT>(
                eglGetProcAddress("glFramebufferTexture2DMultisampleEXT"));
    }
    LOGI("GL_EXT_multisampled_render_to_texture: %s",
         gFramebufferTexture2DMultisampleEXT ? "available" : "not available");
}

GLuint compileShader(GLenum type, const char* source, const char* debugName) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr);
    glCompileShader(shader);
    GLint ok = GL_FALSE;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        GLint len = 0;
        glGetShaderiv(shader, GL_INFO_LOG_LENGTH, &len);
        std::string log(size_t(len > 0 ? len : 1), '\0');
        glGetShaderInfoLog(shader, len, nullptr, &log[0]);
        LOGE("%s %s shader failed to compile:\n%s", debugName,
             type == GL_VERTEX_SHADER ? "vertex" : "fragment", log.c_str());
        glDeleteShader(shader);
        return 0;
    }
    return shader;
}

}  // namespace

void glCheck(const char* where) {
    GLenum err;
    int guard = 0;
    while ((err = glGetError()) != GL_NO_ERROR && guard++ < 8)
        LOGE("GL error 0x%04x at %s", err, where);
}

GLuint glCompileProgram(const char* vertexSource, const char* fragmentSource,
                        const char* debugName) {
    GLuint vs = compileShader(GL_VERTEX_SHADER, vertexSource, debugName);
    if (!vs) return 0;
    GLuint fs = compileShader(GL_FRAGMENT_SHADER, fragmentSource, debugName);
    if (!fs) {
        glDeleteShader(vs);
        return 0;
    }
    GLuint program = glCreateProgram();
    glAttachShader(program, vs);
    glAttachShader(program, fs);
    glLinkProgram(program);
    glDeleteShader(vs);
    glDeleteShader(fs);

    GLint ok = GL_FALSE;
    glGetProgramiv(program, GL_LINK_STATUS, &ok);
    if (!ok) {
        GLint len = 0;
        glGetProgramiv(program, GL_INFO_LOG_LENGTH, &len);
        std::string log(size_t(len > 0 ? len : 1), '\0');
        glGetProgramInfoLog(program, len, nullptr, &log[0]);
        LOGE("%s program failed to link:\n%s", debugName, log.c_str());
        glDeleteProgram(program);
        return 0;
    }
    return program;
}

// ------------------------------------------------------------- EyeTarget

EyeTarget::~EyeTarget() { destroy(); }

bool EyeTarget::create(int width, int height, int samples) {
    destroy();
    resolveExtensions();
    width_ = width;
    height_ = height;

    GLint maxSamples = 1;
    glGetIntegerv(GL_MAX_SAMPLES, &maxSamples);
    samples_ = samples < 1 ? 1 : (samples > maxSamples ? maxSamples : samples);

    if (samples_ > 1 && gFramebufferTexture2DMultisampleEXT &&
        gRenderbufferStorageMultisampleEXT) {
        implicitResolve_ = true;
        glGenRenderbuffers(1, &msaaDepth_);
        glBindRenderbuffer(GL_RENDERBUFFER, msaaDepth_);
        gRenderbufferStorageMultisampleEXT(GL_RENDERBUFFER, samples_, GL_DEPTH_COMPONENT24,
                                           width_, height_);
        glBindRenderbuffer(GL_RENDERBUFFER, 0);
    } else if (samples_ > 1) {
        implicitResolve_ = false;
        glGenRenderbuffers(1, &msaaColor_);
        glBindRenderbuffer(GL_RENDERBUFFER, msaaColor_);
        glRenderbufferStorageMultisample(GL_RENDERBUFFER, samples_, GL_SRGB8_ALPHA8, width_,
                                         height_);
        glGenRenderbuffers(1, &msaaDepth_);
        glBindRenderbuffer(GL_RENDERBUFFER, msaaDepth_);
        glRenderbufferStorageMultisample(GL_RENDERBUFFER, samples_, GL_DEPTH_COMPONENT24,
                                         width_, height_);
        glBindRenderbuffer(GL_RENDERBUFFER, 0);

        glGenFramebuffers(1, &msaaFbo_);
        glBindFramebuffer(GL_FRAMEBUFFER, msaaFbo_);
        glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER,
                                  msaaColor_);
        glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER,
                                  msaaDepth_);
        if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
            LOGE("multisample framebuffer incomplete, falling back to 1 sample");
            glBindFramebuffer(GL_FRAMEBUFFER, 0);
            destroy();
            width_ = width;
            height_ = height;
            samples_ = 1;
        }
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
    }

    if (samples_ == 1) {
        implicitResolve_ = false;
        glGenRenderbuffers(1, &msaaDepth_);
        glBindRenderbuffer(GL_RENDERBUFFER, msaaDepth_);
        glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24, width_, height_);
        glBindRenderbuffer(GL_RENDERBUFFER, 0);
    }

    LOGI("eye target %dx%d, %d sample(s), %s", width_, height_, samples_,
         implicitResolve_ ? "implicit resolve" : (samples_ > 1 ? "blit resolve" : "no MSAA"));
    return true;
}

void EyeTarget::destroy() {
    for (Attachment& a : attachments_) {
        if (a.fbo) glDeleteFramebuffers(1, &a.fbo);
    }
    attachments_.clear();
    if (msaaFbo_) glDeleteFramebuffers(1, &msaaFbo_);
    if (msaaColor_) glDeleteRenderbuffers(1, &msaaColor_);
    if (msaaDepth_) glDeleteRenderbuffers(1, &msaaDepth_);
    msaaFbo_ = msaaColor_ = msaaDepth_ = 0;
    width_ = height_ = 0;
    samples_ = 1;
    implicitResolve_ = false;
}

EyeTarget::Attachment* EyeTarget::attachmentFor(GLuint colorTexture) {
    for (Attachment& a : attachments_) {
        if (a.texture == colorTexture) return &a;
    }
    Attachment a;
    a.texture = colorTexture;
    glGenFramebuffers(1, &a.fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, a.fbo);
    if (implicitResolve_) {
        gFramebufferTexture2DMultisampleEXT(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                                            colorTexture, 0, samples_);
        glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER,
                                  msaaDepth_);
    } else {
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                               colorTexture, 0);
        if (samples_ == 1)
            glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER,
                                      msaaDepth_);
    }
    const GLenum status = glCheckFramebufferStatus(GL_FRAMEBUFFER);
    if (status != GL_FRAMEBUFFER_COMPLETE)
        LOGE("swapchain framebuffer incomplete: 0x%04x", status);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    attachments_.push_back(a);
    return &attachments_.back();
}

void EyeTarget::bind(GLuint colorTexture) {
    Attachment* a = attachmentFor(colorTexture);
    if (!implicitResolve_ && samples_ > 1) {
        glBindFramebuffer(GL_FRAMEBUFFER, msaaFbo_);
    } else {
        glBindFramebuffer(GL_FRAMEBUFFER, a->fbo);
    }
    glViewport(0, 0, width_, height_);
    glScissor(0, 0, width_, height_);
}

void EyeTarget::resolve(GLuint colorTexture) {
    if (!implicitResolve_ && samples_ > 1) {
        Attachment* a = attachmentFor(colorTexture);
        glBindFramebuffer(GL_READ_FRAMEBUFFER, msaaFbo_);
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, a->fbo);
        glBlitFramebuffer(0, 0, width_, height_, 0, 0, width_, height_, GL_COLOR_BUFFER_BIT,
                          GL_NEAREST);
    }
    // Depth is never needed after the eye is drawn; discarding it saves
    // bandwidth on the tiler.
    const GLenum discard[] = {GL_DEPTH_ATTACHMENT};
    glInvalidateFramebuffer(GL_FRAMEBUFFER, 1, discard);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
}

// -------------------------------------------------------------- textures

GLuint glCreateTexture2D(const Image& image, bool srgb, bool mipmap) {
    if (!image.valid()) return 0;
    GLuint tex = 0;
    glGenTextures(1, &tex);
    glBindTexture(GL_TEXTURE_2D, tex);
    const GLint levels = mipmap ? GLint(1 + std::floor(std::log2(double(
                                          image.width > image.height ? image.width
                                                                     : image.height))))
                                : 1;
    glTexStorage2D(GL_TEXTURE_2D, levels, srgb ? GL_SRGB8_ALPHA8 : GL_RGBA8, image.width,
                   image.height);
    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, image.width, image.height, GL_RGBA,
                    GL_UNSIGNED_BYTE, image.rgba.data());
    if (mipmap) glGenerateMipmap(GL_TEXTURE_2D);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER,
                    mipmap ? GL_LINEAR_MIPMAP_LINEAR : GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT);
    glBindTexture(GL_TEXTURE_2D, 0);
    return tex;
}

GLuint glCreateEmptyTexture2D(int width, int height, bool srgb) {
    GLuint tex = 0;
    glGenTextures(1, &tex);
    glBindTexture(GL_TEXTURE_2D, tex);
    glTexStorage2D(GL_TEXTURE_2D, 1, srgb ? GL_SRGB8_ALPHA8 : GL_RGBA8, width, height);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glBindTexture(GL_TEXTURE_2D, 0);
    return tex;
}

GLuint glCreateWhiteTexture() {
    Image white;
    white.width = white.height = 1;
    white.rgba.assign(4, 255);
    return glCreateTexture2D(white, false, false);
}

GLuint glCreateProceduralTexture(const std::string& name) {
    const int kSize = 128;
    Image img;
    img.width = img.height = kSize;
    img.rgba.assign(size_t(kSize) * kSize * 4, 255);

    if (name == "checker") {
        for (int y = 0; y < kSize; ++y) {
            for (int x = 0; x < kSize; ++x) {
                const bool on = ((x / 16) + (y / 16)) & 1;
                uint8_t* p = img.pixel(x, y);
                p[0] = p[1] = p[2] = on ? 235 : 190;
                p[3] = 255;
            }
        }
    } else if (name == "grid") {
        for (int y = 0; y < kSize; ++y) {
            for (int x = 0; x < kSize; ++x) {
                const bool line = (x % 32) < 2 || (y % 32) < 2;
                uint8_t* p = img.pixel(x, y);
                p[0] = p[1] = p[2] = line ? 120 : 245;
                p[3] = 255;
            }
        }
    } else if (name == "noise") {
        // Deterministic value noise so both eyes and every frame agree.
        uint32_t s = 0x9E3779B9u;
        for (int y = 0; y < kSize; ++y) {
            for (int x = 0; x < kSize; ++x) {
                s ^= s << 13;
                s ^= s >> 17;
                s ^= s << 5;
                const uint8_t v = uint8_t(200 + (s & 0x3F) / 2);
                uint8_t* p = img.pixel(x, y);
                p[0] = p[1] = p[2] = v;
                p[3] = 255;
            }
        }
    } else {
        return 0;
    }
    return glCreateTexture2D(img, true, true);
}

}  // namespace fcxr
