// SPDX-License-Identifier: LGPL-2.1-or-later
#include "egl_context.h"

#include <vector>

#include "log.h"

namespace fcxr {

bool EglContext::create() {
    display_ = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (display_ == EGL_NO_DISPLAY) {
        LOGE("eglGetDisplay failed");
        return false;
    }
    EGLint major = 0, minor = 0;
    if (!eglInitialize(display_, &major, &minor)) {
        LOGE("eglInitialize failed: 0x%x", eglGetError());
        return false;
    }
    LOGI("EGL %d.%d", major, minor);

    // The runtime composites our swapchain textures, so the config only has to
    // be able to host a context; it is never used for presentation. We still
    // ask for a plain 8888 config because some drivers validate it.
    const EGLint configAttribs[] = {EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT_KHR,
                                    EGL_SURFACE_TYPE,    EGL_PBUFFER_BIT,
                                    EGL_RED_SIZE,        8,
                                    EGL_GREEN_SIZE,      8,
                                    EGL_BLUE_SIZE,       8,
                                    EGL_ALPHA_SIZE,      8,
                                    EGL_DEPTH_SIZE,      0,
                                    EGL_STENCIL_SIZE,    0,
                                    EGL_SAMPLES,         0,
                                    EGL_NONE};
    EGLint configCount = 0;
    if (!eglChooseConfig(display_, configAttribs, &config_, 1, &configCount) ||
        configCount < 1) {
        LOGE("eglChooseConfig found no usable config");
        return false;
    }

    const EGLint contextAttribs[] = {EGL_CONTEXT_MAJOR_VERSION, 3, EGL_CONTEXT_MINOR_VERSION,
                                     2, EGL_NONE};
    context_ = eglCreateContext(display_, config_, EGL_NO_CONTEXT, contextAttribs);
    if (context_ == EGL_NO_CONTEXT) {
        // Fall back to 3.1 then 3.0 — every Quest supports 3.2, but a
        // clear log line beats a mysterious black screen on other hardware.
        const EGLint fallback31[] = {EGL_CONTEXT_MAJOR_VERSION, 3, EGL_CONTEXT_MINOR_VERSION,
                                     1, EGL_NONE};
        context_ = eglCreateContext(display_, config_, EGL_NO_CONTEXT, fallback31);
    }
    if (context_ == EGL_NO_CONTEXT) {
        LOGE("eglCreateContext failed: 0x%x", eglGetError());
        return false;
    }

    const EGLint surfaceAttribs[] = {EGL_WIDTH, 16, EGL_HEIGHT, 16, EGL_NONE};
    surface_ = eglCreatePbufferSurface(display_, config_, surfaceAttribs);
    if (surface_ == EGL_NO_SURFACE) {
        LOGE("eglCreatePbufferSurface failed: 0x%x", eglGetError());
        return false;
    }
    if (!makeCurrent()) return false;

    LOGI("GL_VENDOR   %s", glGetString(GL_VENDOR));
    LOGI("GL_RENDERER %s", glGetString(GL_RENDERER));
    LOGI("GL_VERSION  %s", glGetString(GL_VERSION));
    return true;
}

bool EglContext::makeCurrent() {
    if (!eglMakeCurrent(display_, surface_, surface_, context_)) {
        LOGE("eglMakeCurrent failed: 0x%x", eglGetError());
        return false;
    }
    return true;
}

void EglContext::destroy() {
    if (display_ != EGL_NO_DISPLAY) {
        eglMakeCurrent(display_, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (surface_ != EGL_NO_SURFACE) eglDestroySurface(display_, surface_);
        if (context_ != EGL_NO_CONTEXT) eglDestroyContext(display_, context_);
        eglTerminate(display_);
    }
    display_ = EGL_NO_DISPLAY;
    surface_ = EGL_NO_SURFACE;
    context_ = EGL_NO_CONTEXT;
    config_ = nullptr;
}

}  // namespace fcxr
