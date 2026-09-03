// SPDX-License-Identifier: LGPL-2.1-or-later
//
// GLES 3.2 forward renderer.
//
// Two passes per eye:
//   1. instanced PBR geometry (environment + loaded document),
//   2. an unlit overlay stream for UI panels, pointer rays, paint ribbons and
//      the environment cross-fade.
//
// Meshes, materials and textures live in handle pools so the rest of the app
// never touches a GL name. Handles are stable; deleting one frees the GL
// object and leaves a hole that later creations reuse.
#pragma once

#include <GLES3/gl32.h>

#include <string>
#include <vector>

#include "gl_util.h"
#include "mesh_data.h"

namespace fcxr {

struct RenderMaterial {
    Vec4 baseColor{0.8f, 0.8f, 0.8f, 1.0f};  // linear
    float metallic = 0.0f;
    float roughness = 0.6f;
    Vec3 emissive{0, 0, 0};
    int texture = -1;  // texture handle, -1 for none
    bool doubleSided = false;
};

struct RenderInstance {
    int mesh = -1;
    int material = -1;
    Mat4 transform;
    Aabb worldBounds;
};

struct RenderLight {
    int type = 0;  // 0 directional, 1 point, 2 spot
    Vec3 position{0, 0, 0};
    Vec3 direction{0, -1, 0};
    Vec3 color{1, 1, 1};
    float intensity = 1.0f;
    float cutoffCos = 0.7f;
    float range = 4.0f;
};

// ------------------------------------------------------------------ overlay

struct OverlayVertex {
    Vec3 p;
    Vec2 uv;
    Vec4 c;
};

// Triangle soup in world space, rebuilt every frame by ui.cpp / paint.cpp.
class OverlayBuffer {
public:
    struct Batch {
        int texture = -1;   // texture handle, -1 for flat colour
        size_t first = 0;   // first vertex
        size_t count = 0;
        bool depthTest = true;
    };

    void clear();
    bool empty() const { return vertices_.empty(); }

    // Starts a new batch. Consecutive calls with identical state are merged.
    void setState(int texture, bool depthTest);

    void addTriangle(const OverlayVertex& a, const OverlayVertex& b, const OverlayVertex& c);
    // Quad in loop order a-b-c-d.
    void addQuad(Vec3 a, Vec3 b, Vec3 c, Vec3 d, Vec4 color, bool withUv = false);
    // Axis aligned rectangle in a panel frame: `origin` is the lower-left
    // corner, `right`/`up` are the full edge vectors.
    void addRect(Vec3 origin, Vec3 right, Vec3 up, Vec4 color);
    // Outline of the same rectangle, `thickness` in metres.
    void addRectOutline(Vec3 origin, Vec3 right, Vec3 up, float thickness, Vec4 color);
    // Camera facing quad strip along a 3D segment; `width` in metres.
    void addBillboardLine(Vec3 a, Vec3 b, Vec3 eye, float width, Vec4 color);
    // Ribbon segment with an explicit orientation normal.
    void addRibbonSegment(Vec3 a, Vec3 b, Vec3 na, Vec3 nb, float wa, float wb, Vec4 color);
    // Stroke font text on a panel plane. `origin` is the baseline start.
    void addText(const std::string& text, Vec3 origin, Vec3 right, Vec3 up, float height,
                 float thickness, Vec4 color);

    const std::vector<OverlayVertex>& vertices() const { return vertices_; }
    const std::vector<Batch>& batches() const { return batches_; }

private:
    std::vector<OverlayVertex> vertices_;
    std::vector<Batch> batches_;
    int texture_ = -1;
    bool depthTest_ = true;
};

// ----------------------------------------------------------------- renderer

class Renderer {
public:
    struct Stats {
        int instances = 0;
        int drawCalls = 0;
        int culled = 0;
        int triangles = 0;
    };

    bool init(const std::string& pbrVert, const std::string& pbrFrag,
              const std::string& unlitVert, const std::string& unlitFrag);
    void shutdown();

    // ---- resources ------------------------------------------------------
    int createMesh(const MeshData& mesh);
    void updateMesh(int handle, const MeshData& mesh);
    void destroyMesh(int handle);
    Aabb meshBounds(int handle) const;

    int createMaterial(const RenderMaterial& material);
    void setMaterial(int handle, const RenderMaterial& material);
    void destroyMaterial(int handle);

    int createTexture(const Image& image, bool srgb, bool mipmap);
    int createEmptyTexture(int width, int height, bool srgb);
    int createProceduralTexture(const std::string& name);
    // Uploads a sub-rectangle of RGBA8 pixels; used by the paint dirty rects.
    void updateTexture(int handle, int x, int y, int width, int height, const uint8_t* rgba);
    void destroyTexture(int handle);

    // ---- frame ----------------------------------------------------------
    // `clearAlpha` is 0 in passthrough mode so the projection layer composites
    // over the camera feed.
    void beginEye(EyeTarget& target, GLuint swapchainImage, Vec3 clearColor, float clearAlpha);
    void drawScene(const Mat4& view, const Mat4& proj, Vec3 eyePosition,
                   const std::vector<RenderInstance>& instances,
                   const std::vector<RenderLight>& lights, Vec3 ambient, float alpha);
    void drawOverlay(const Mat4& view, const Mat4& proj, const OverlayBuffer& overlay);
    // Full screen black (or white) fade; `amount` 0..1.
    void drawFade(float amount, Vec3 color);
    void endEye(EyeTarget& target, GLuint swapchainImage);

    const Stats& stats() const { return stats_; }

private:
    struct GpuMesh {
        GLuint vao = 0, vbo = 0, ibo = 0;
        GLsizei indexCount = 0;
        Aabb bounds;
        bool alive = false;
    };
    struct GpuTexture {
        GLuint id = 0;
        int width = 0, height = 0;
        bool alive = false;
    };
    struct MaterialSlot {
        RenderMaterial material;
        bool alive = false;
    };

    void setupMeshVao(GpuMesh& m);
    GLuint textureId(int handle) const;

    GLuint pbrProgram_ = 0;
    GLuint unlitProgram_ = 0;
    GLuint instanceVbo_ = 0;
    size_t instanceVboCapacity_ = 0;
    GLuint overlayVao_ = 0, overlayVbo_ = 0;
    size_t overlayVboCapacity_ = 0;
    GLuint whiteTexture_ = 0;

    // pbr uniform locations
    GLint uViewProj_ = -1, uBaseColor_ = -1, uMaterialParams_ = -1, uEmissive_ = -1;
    GLint uAmbient_ = -1, uEyePos_ = -1, uLightCount_ = -1, uLightPosType_ = -1;
    GLint uLightDirRange_ = -1, uLightColor_ = -1, uBaseTexture_ = -1, uAlpha_ = -1;
    // unlit uniform locations
    GLint uOverlayViewProj_ = -1, uOverlayUseViewProj_ = -1, uOverlayTexture_ = -1,
          uOverlayUseTexture_ = -1;

    std::vector<GpuMesh> meshes_;
    std::vector<GpuTexture> textures_;
    std::vector<MaterialSlot> materials_;
    std::vector<float> instanceScratch_;
    Stats stats_;
};

}  // namespace fcxr
