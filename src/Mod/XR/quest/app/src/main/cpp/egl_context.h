// SPDX-License-Identifier: LGPL-2.1-or-later
//
// The GLES context OpenXR renders with.
//
// OpenXR on Android wants an EGL context that is current on the render thread
// and never presents to a window: the runtime owns the display. A 16x16
// pbuffer surface keeps the context complete without any window system
// involvement.
#pragma once

#include <EGL/egl.h>
#include <GLES3/gl32.h>

namespace fcxr {

class EglContext {
public:
    bool create();
    void destroy();
    bool valid() const { return context_ != EGL_NO_CONTEXT; }
    // Re-makes the context current; called when the render thread restarts.
    bool makeCurrent();

    EGLDisplay display() const { return display_; }
    EGLConfig config() const { return config_; }
    EGLContext context() const { return context_; }
    EGLSurface surface() const { return surface_; }

private:
    EGLDisplay display_ = EGL_NO_DISPLAY;
    EGLConfig config_ = nullptr;
    EGLContext context_ = EGL_NO_CONTEXT;
    EGLSurface surface_ = EGL_NO_SURFACE;
};

}  // namespace fcxr
