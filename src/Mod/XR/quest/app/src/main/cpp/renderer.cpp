// SPDX-License-Identifier: LGPL-2.1-or-later
#include "renderer.h"

#include <algorithm>
#include <cstddef>

#include "log.h"
#include "text_font.h"

namespace fcxr {
namespace {

constexpr int kMaxLights = 8;

struct Vertex {
    Vec3 p;
    Vec3 n;
    Vec2 uv;
};

// model matrix (16) + normal matrix columns (3 x vec3)
constexpr size_t kInstanceFloats = 16 + 9;
constexpr size_t kInstanceStride = kInstanceFloats * sizeof(float);

}  // namespace

// -------------------------------------------------------------- OverlayBuffer

void OverlayBuffer::clear() {
    vertices_.clear();
    batches_.clear();
    texture_ = -1;
    depthTest_ = true;
}

void OverlayBuffer::setState(int texture, bool depthTest) {
    if (!batches_.empty() && batches_.back().texture == texture &&
        batches_.back().depthTest == depthTest)
        return;
    Batch b;
    b.texture = texture;
    b.depthTest = depthTest;
    b.first = vertices_.size();
    b.count = 0;
    batches_.push_back(b);
    texture_ = texture;
    depthTest_ = depthTest;
}

void OverlayBuffer::addTriangle(const OverlayVertex& a, const OverlayVertex& b,
                                const OverlayVertex& c) {
    if (batches_.empty()) setState(texture_, depthTest_);
    vertices_.push_back(a);
    vertices_.push_back(b);
    vertices_.push_back(c);
    batches_.back().count += 3;
}

void OverlayBuffer::addQuad(Vec3 a, Vec3 b, Vec3 c, Vec3 d, Vec4 color, bool withUv) {
    OverlayVertex va{a, withUv ? Vec2(0, 0) : Vec2(0, 0), color};
    OverlayVertex vb{b, withUv ? Vec2(1, 0) : Vec2(0, 0), color};
    OverlayVertex vc{c, withUv ? Vec2(1, 1) : Vec2(0, 0), color};
    OverlayVertex vd{d, withUv ? Vec2(0, 1) : Vec2(0, 0), color};
    addTriangle(va, vb, vc);
    addTriangle(va, vc, vd);
}

void OverlayBuffer::addRect(Vec3 origin, Vec3 right, Vec3 up, Vec4 color) {
    addQuad(origin, origin + right, origin + right + up, origin + up, color, true);
}

void OverlayBuffer::addRectOutline(Vec3 origin, Vec3 right, Vec3 up, float thickness,
                                   Vec4 color) {
    const Vec3 r = normalize(right) * thickness;
    const Vec3 u = normalize(up) * thickness;
    addRect(origin, right, u, color);                                   // bottom
    addRect(origin + up - u, right, u, color);                          // top
    addRect(origin, r, up, color);                                      // left
    addRect(origin + right - r, r, up, color);                          // right
}

void OverlayBuffer::addBillboardLine(Vec3 a, Vec3 b, Vec3 eye, float width, Vec4 color) {
    const Vec3 dir = b - a;
    if (lengthSq(dir) < 1e-12f) return;
    Vec3 toEye = eye - (a + b) * 0.5f;
    Vec3 side = cross(normalize(dir), normalize(toEye));
    if (lengthSq(side) < 1e-9f) side = Vec3(0, 1, 0);
    side = normalize(side) * (width * 0.5f);
    addQuad(a - side, b - side, b + side, a + side, color, true);
}

void OverlayBuffer::addRibbonSegment(Vec3 a, Vec3 b, Vec3 na, Vec3 nb, float wa, float wb,
                                     Vec4 color) {
    const Vec3 dir = b - a;
    if (lengthSq(dir) < 1e-12f) return;
    Vec3 sa = cross(normalize(dir), normalize(na));
    Vec3 sb = cross(normalize(dir), normalize(nb));
    if (lengthSq(sa) < 1e-9f) sa = Vec3(1, 0, 0);
    if (lengthSq(sb) < 1e-9f) sb = sa;
    sa = normalize(sa) * (wa * 0.5f);
    sb = normalize(sb) * (wb * 0.5f);
    addQuad(a - sa, b - sb, b + sb, a + sa, color, true);
}

void OverlayBuffer::addText(const std::string& text, Vec3 origin, Vec3 right, Vec3 up,
                            float height, float thickness, Vec4 color) {
    std::vector<std::vector<Vec2>> strokes;
    fontLayout(text, height, /*centred=*/false, &strokes);
    const Vec3 r = normalize(right);
    const Vec3 u = normalize(up);
    for (const std::vector<Vec2>& stroke : strokes) {
        for (size_t i = 0; i + 1 < stroke.size(); ++i) {
            const Vec3 a = origin + r * stroke[i].x + u * stroke[i].y;
            const Vec3 b = origin + r * stroke[i + 1].x + u * stroke[i + 1].y;
            Vec3 dir = b - a;
            const float len = length(dir);
            if (len < 1e-9f) continue;
            dir = dir / len;
            // Half-thickness offset perpendicular to the stroke inside the panel plane.
            const Vec3 side = cross(dir, cross(r, u)) * (thickness * 0.5f);
            // Extend the ends by half the thickness so joints stay closed.
            const Vec3 ext = dir * (thickness * 0.5f);
            addQuad(a - side - ext, b - side + ext, b + side + ext, a + side - ext, color);
        }
    }
}

// ------------------------------------------------------------------ Renderer

bool Renderer::init(const std::string& pbrVert, const std::string& pbrFrag,
                    const std::string& unlitVert, const std::string& unlitFrag) {
    pbrProgram_ = glCompileProgram(pbrVert.c_str(), pbrFrag.c_str(), "pbr");
    unlitProgram_ = glCompileProgram(unlitVert.c_str(), unlitFrag.c_str(), "unlit");
    if (!pbrProgram_ || !unlitProgram_) return false;

    uViewProj_ = glGetUniformLocation(pbrProgram_, "uViewProj");
    uBaseColor_ = glGetUniformLocation(pbrProgram_, "uBaseColor");
    uMaterialParams_ = glGetUniformLocation(pbrProgram_, "uMaterial");
    uEmissive_ = glGetUniformLocation(pbrProgram_, "uEmissive");
    uAmbient_ = glGetUniformLocation(pbrProgram_, "uAmbient");
    uEyePos_ = glGetUniformLocation(pbrProgram_, "uEyePos");
    uLightCount_ = glGetUniformLocation(pbrProgram_, "uLightCount");
    uLightPosType_ = glGetUniformLocation(pbrProgram_, "uLightPosType");
    uLightDirRange_ = glGetUniformLocation(pbrProgram_, "uLightDirRange");
    uLightColor_ = glGetUniformLocation(pbrProgram_, "uLightColor");
    uBaseTexture_ = glGetUniformLocation(pbrProgram_, "uBaseTexture");
    uAlpha_ = glGetUniformLocation(pbrProgram_, "uAlpha");

    uOverlayViewProj_ = glGetUniformLocation(unlitProgram_, "uViewProj");
    uOverlayUseViewProj_ = glGetUniformLocation(unlitProgram_, "uUseViewProj");
    uOverlayTexture_ = glGetUniformLocation(unlitProgram_, "uTexture");
    uOverlayUseTexture_ = glGetUniformLocation(unlitProgram_, "uUseTexture");

    glGenBuffers(1, &instanceVbo_);
    glGenBuffers(1, &overlayVbo_);
    glGenVertexArrays(1, &overlayVao_);

    glBindVertexArray(overlayVao_);
    glBindBuffer(GL_ARRAY_BUFFER, overlayVbo_);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, sizeof(OverlayVertex),
                          reinterpret_cast<void*>(offsetof(OverlayVertex, p)));
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, sizeof(OverlayVertex),
                          reinterpret_cast<void*>(offsetof(OverlayVertex, uv)));
    glEnableVertexAttribArray(2);
    glVertexAttribPointer(2, 4, GL_FLOAT, GL_FALSE, sizeof(OverlayVertex),
                          reinterpret_cast<void*>(offsetof(OverlayVertex, c)));
    glBindVertexArray(0);
    glBindBuffer(GL_ARRAY_BUFFER, 0);

    whiteTexture_ = glCreateWhiteTexture();
    glCheck("Renderer::init");
    LOGI("renderer initialised");
    return true;
}

void Renderer::shutdown() {
    for (GpuMesh& m : meshes_) {
        if (!m.alive) continue;
        glDeleteVertexArrays(1, &m.vao);
        glDeleteBuffers(1, &m.vbo);
        glDeleteBuffers(1, &m.ibo);
        m.alive = false;
    }
    meshes_.clear();
    for (GpuTexture& t : textures_) {
        if (t.alive) glDeleteTextures(1, &t.id);
        t.alive = false;
    }
    textures_.clear();
    materials_.clear();
    if (whiteTexture_) glDeleteTextures(1, &whiteTexture_);
    if (instanceVbo_) glDeleteBuffers(1, &instanceVbo_);
    if (overlayVbo_) glDeleteBuffers(1, &overlayVbo_);
    if (overlayVao_) glDeleteVertexArrays(1, &overlayVao_);
    if (pbrProgram_) glDeleteProgram(pbrProgram_);
    if (unlitProgram_) glDeleteProgram(unlitProgram_);
    whiteTexture_ = instanceVbo_ = overlayVbo_ = overlayVao_ = 0;
    pbrProgram_ = unlitProgram_ = 0;
}

void Renderer::setupMeshVao(GpuMesh& m) {
    glBindVertexArray(m.vao);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, m.ibo);

    // Binding 0: per-vertex data. Binding 1: per-instance data (divisor 1).
    glBindVertexBuffer(0, m.vbo, 0, sizeof(Vertex));
    glVertexAttribFormat(0, 3, GL_FLOAT, GL_FALSE, GLuint(offsetof(Vertex, p)));
    glVertexAttribFormat(1, 3, GL_FLOAT, GL_FALSE, GLuint(offsetof(Vertex, n)));
    glVertexAttribFormat(2, 2, GL_FLOAT, GL_FALSE, GLuint(offsetof(Vertex, uv)));
    for (GLuint i = 0; i < 3; ++i) {
        glVertexAttribBinding(i, 0);
        glEnableVertexAttribArray(i);
    }
    for (GLuint i = 0; i < 4; ++i) {  // model matrix columns
        glVertexAttribFormat(3 + i, 4, GL_FLOAT, GL_FALSE, GLuint(i * 16));
        glVertexAttribBinding(3 + i, 1);
        glEnableVertexAttribArray(3 + i);
    }
    for (GLuint i = 0; i < 3; ++i) {  // normal matrix columns
        glVertexAttribFormat(7 + i, 3, GL_FLOAT, GL_FALSE, GLuint(64 + i * 12));
        glVertexAttribBinding(7 + i, 1);
        glEnableVertexAttribArray(7 + i);
    }
    glVertexBindingDivisor(1, 1);
    glBindVertexArray(0);
}

int Renderer::createMesh(const MeshData& mesh) {
    if (mesh.positions.empty() || mesh.indices.empty()) return -1;
    int handle = -1;
    for (size_t i = 0; i < meshes_.size(); ++i) {
        if (!meshes_[i].alive) { handle = int(i); break; }
    }
    if (handle < 0) {
        meshes_.emplace_back();
        handle = int(meshes_.size()) - 1;
    }
    GpuMesh& m = meshes_[size_t(handle)];
    glGenVertexArrays(1, &m.vao);
    glGenBuffers(1, &m.vbo);
    glGenBuffers(1, &m.ibo);
    m.alive = true;
    setupMeshVao(m);
    updateMesh(handle, mesh);
    return handle;
}

void Renderer::updateMesh(int handle, const MeshData& mesh) {
    if (handle < 0 || size_t(handle) >= meshes_.size() || !meshes_[size_t(handle)].alive) return;
    GpuMesh& m = meshes_[size_t(handle)];

    std::vector<Vertex> verts(mesh.positions.size());
    for (size_t i = 0; i < mesh.positions.size(); ++i) {
        verts[i].p = mesh.positions[i];
        verts[i].n = i < mesh.normals.size() ? mesh.normals[i] : Vec3(0, 1, 0);
        verts[i].uv = i < mesh.uvs.size() ? mesh.uvs[i] : Vec2(0, 0);
    }
    glBindBuffer(GL_ARRAY_BUFFER, m.vbo);
    glBufferData(GL_ARRAY_BUFFER, GLsizeiptr(verts.size() * sizeof(Vertex)), verts.data(),
                 GL_STATIC_DRAW);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, m.ibo);
    glBufferData(GL_ELEMENT_ARRAY_BUFFER, GLsizeiptr(mesh.indices.size() * sizeof(uint32_t)),
                 mesh.indices.data(), GL_STATIC_DRAW);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0);

    m.indexCount = GLsizei(mesh.indices.size());
    m.bounds = mesh.bounds();
}

void Renderer::destroyMesh(int handle) {
    if (handle < 0 || size_t(handle) >= meshes_.size() || !meshes_[size_t(handle)].alive) return;
    GpuMesh& m = meshes_[size_t(handle)];
    glDeleteVertexArrays(1, &m.vao);
    glDeleteBuffers(1, &m.vbo);
    glDeleteBuffers(1, &m.ibo);
    m = GpuMesh();
}

Aabb Renderer::meshBounds(int handle) const {
    if (handle < 0 || size_t(handle) >= meshes_.size() || !meshes_[size_t(handle)].alive)
        return Aabb();
    return meshes_[size_t(handle)].bounds;
}

int Renderer::createMaterial(const RenderMaterial& material) {
    for (size_t i = 0; i < materials_.size(); ++i) {
        if (!materials_[i].alive) {
            materials_[i].material = material;
            materials_[i].alive = true;
            return int(i);
        }
    }
    materials_.push_back({material, true});
    return int(materials_.size()) - 1;
}

void Renderer::setMaterial(int handle, const RenderMaterial& material) {
    if (handle < 0 || size_t(handle) >= materials_.size()) return;
    materials_[size_t(handle)].material = material;
    materials_[size_t(handle)].alive = true;
}

void Renderer::destroyMaterial(int handle) {
    if (handle < 0 || size_t(handle) >= materials_.size()) return;
    materials_[size_t(handle)] = MaterialSlot();
}

int Renderer::createTexture(const Image& image, bool srgb, bool mipmap) {
    const GLuint id = glCreateTexture2D(image, srgb, mipmap);
    if (!id) return -1;
    for (size_t i = 0; i < textures_.size(); ++i) {
        if (!textures_[i].alive) {
            textures_[i] = {id, image.width, image.height, true};
            return int(i);
        }
    }
    textures_.push_back({id, image.width, image.height, true});
    return int(textures_.size()) - 1;
}

int Renderer::createEmptyTexture(int width, int height, bool srgb) {
    const GLuint id = glCreateEmptyTexture2D(width, height, srgb);
    if (!id) return -1;
    for (size_t i = 0; i < textures_.size(); ++i) {
        if (!textures_[i].alive) {
            textures_[i] = {id, width, height, true};
            return int(i);
        }
    }
    textures_.push_back({id, width, height, true});
    return int(textures_.size()) - 1;
}

int Renderer::createProceduralTexture(const std::string& name) {
    const GLuint id = glCreateProceduralTexture(name);
    if (!id) return -1;
    textures_.push_back({id, 128, 128, true});
    return int(textures_.size()) - 1;
}

void Renderer::updateTexture(int handle, int x, int y, int width, int height,
                             const uint8_t* rgba) {
    if (handle < 0 || size_t(handle) >= textures_.size() || !textures_[size_t(handle)].alive)
        return;
    if (width <= 0 || height <= 0 || !rgba) return;
    glBindTexture(GL_TEXTURE_2D, textures_[size_t(handle)].id);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexSubImage2D(GL_TEXTURE_2D, 0, x, y, width, height, GL_RGBA, GL_UNSIGNED_BYTE, rgba);
    glBindTexture(GL_TEXTURE_2D, 0);
}

void Renderer::destroyTexture(int handle) {
    if (handle < 0 || size_t(handle) >= textures_.size() || !textures_[size_t(handle)].alive)
        return;
    glDeleteTextures(1, &textures_[size_t(handle)].id);
    textures_[size_t(handle)] = GpuTexture();
}

GLuint Renderer::textureId(int handle) const {
    if (handle < 0 || size_t(handle) >= textures_.size() || !textures_[size_t(handle)].alive)
        return whiteTexture_;
    return textures_[size_t(handle)].id;
}

// -------------------------------------------------------------------- frame

void Renderer::beginEye(EyeTarget& target, GLuint swapchainImage, Vec3 clearColor,
                        float clearAlpha) {
    target.bind(swapchainImage);
    glEnable(GL_SCISSOR_TEST);
    glEnable(GL_DEPTH_TEST);
    glDepthMask(GL_TRUE);
    glDepthFunc(GL_LEQUAL);
    glDisable(GL_BLEND);
    glEnable(GL_CULL_FACE);
    glCullFace(GL_BACK);
    glFrontFace(GL_CCW);
    glClearColor(clearColor.x, clearColor.y, clearColor.z, clearAlpha);
    glClearDepthf(1.0f);
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
    stats_ = Stats();
}

void Renderer::drawScene(const Mat4& view, const Mat4& proj, Vec3 eyePosition,
                         const std::vector<RenderInstance>& instances,
                         const std::vector<RenderLight>& lights, Vec3 ambient, float alpha) {
    if (instances.empty()) return;
    const Mat4 viewProj = proj * view;
    Frustum frustum;
    frustum.fromViewProj(viewProj);

    // Cull, then sort by (material, mesh) so identical parts end up adjacent
    // and can be drawn with one instanced call.
    std::vector<const RenderInstance*> visible;
    visible.reserve(instances.size());
    for (const RenderInstance& i : instances) {
        if (i.mesh < 0 || size_t(i.mesh) >= meshes_.size() || !meshes_[size_t(i.mesh)].alive)
            continue;
        if (i.worldBounds.valid() && !frustum.intersects(i.worldBounds)) {
            ++stats_.culled;
            continue;
        }
        visible.push_back(&i);
    }
    if (visible.empty()) return;
    std::sort(visible.begin(), visible.end(),
              [](const RenderInstance* a, const RenderInstance* b) {
                  if (a->material != b->material) return a->material < b->material;
                  return a->mesh < b->mesh;
              });

    // Pack every instance's matrices into one buffer upload.
    instanceScratch_.clear();
    instanceScratch_.reserve(visible.size() * kInstanceFloats);
    for (const RenderInstance* i : visible) {
        const Mat4 nm = normalMatrix(i->transform);
        for (int k = 0; k < 16; ++k) instanceScratch_.push_back(i->transform.m[k]);
        for (int c = 0; c < 3; ++c)
            for (int r = 0; r < 3; ++r) instanceScratch_.push_back(nm.at(r, c));
    }
    const size_t bytes = instanceScratch_.size() * sizeof(float);
    glBindBuffer(GL_ARRAY_BUFFER, instanceVbo_);
    if (bytes > instanceVboCapacity_) {
        glBufferData(GL_ARRAY_BUFFER, GLsizeiptr(bytes), instanceScratch_.data(),
                     GL_STREAM_DRAW);
        instanceVboCapacity_ = bytes;
    } else {
        // Orphan first so the driver does not stall on the previous frame.
        glBufferData(GL_ARRAY_BUFFER, GLsizeiptr(instanceVboCapacity_), nullptr,
                     GL_STREAM_DRAW);
        glBufferSubData(GL_ARRAY_BUFFER, 0, GLsizeiptr(bytes), instanceScratch_.data());
    }
    glBindBuffer(GL_ARRAY_BUFFER, 0);

    glUseProgram(pbrProgram_);
    glUniformMatrix4fv(uViewProj_, 1, GL_FALSE, viewProj.m);
    glUniform3f(uEyePos_, eyePosition.x, eyePosition.y, eyePosition.z);
    glUniform3f(uAmbient_, ambient.x, ambient.y, ambient.z);
    glUniform1f(uAlpha_, alpha);
    glUniform1i(uBaseTexture_, 0);

    const int lightCount = int(std::min<size_t>(lights.size(), kMaxLights));
    float posType[kMaxLights * 4] = {0};
    float dirRange[kMaxLights * 4] = {0};
    float colors[kMaxLights * 4] = {0};
    for (int i = 0; i < lightCount; ++i) {
        const RenderLight& l = lights[size_t(i)];
        posType[i * 4 + 0] = l.position.x;
        posType[i * 4 + 1] = l.position.y;
        posType[i * 4 + 2] = l.position.z;
        posType[i * 4 + 3] = float(l.type);
        dirRange[i * 4 + 0] = l.direction.x;
        dirRange[i * 4 + 1] = l.direction.y;
        dirRange[i * 4 + 2] = l.direction.z;
        dirRange[i * 4 + 3] = l.range;
        colors[i * 4 + 0] = l.color.x * l.intensity;
        colors[i * 4 + 1] = l.color.y * l.intensity;
        colors[i * 4 + 2] = l.color.z * l.intensity;
        colors[i * 4 + 3] = l.cutoffCos;
    }
    glUniform1i(uLightCount_, lightCount);
    glUniform4fv(uLightPosType_, kMaxLights, posType);
    glUniform4fv(uLightDirRange_, kMaxLights, dirRange);
    glUniform4fv(uLightColor_, kMaxLights, colors);

    glActiveTexture(GL_TEXTURE0);

    size_t start = 0;
    while (start < visible.size()) {
        size_t end = start + 1;
        while (end < visible.size() && visible[end]->mesh == visible[start]->mesh &&
               visible[end]->material == visible[start]->material)
            ++end;

        const RenderInstance& first = *visible[start];
        const GpuMesh& mesh = meshes_[size_t(first.mesh)];
        RenderMaterial material;
        if (first.material >= 0 && size_t(first.material) < materials_.size() &&
            materials_[size_t(first.material)].alive)
            material = materials_[size_t(first.material)].material;

        glUniform4f(uBaseColor_, material.baseColor.x, material.baseColor.y,
                    material.baseColor.z, material.baseColor.w);
        glUniform4f(uMaterialParams_, material.metallic, material.roughness,
                    material.texture >= 0 ? 1.0f : 0.0f, 0.0f);
        glUniform3f(uEmissive_, material.emissive.x, material.emissive.y, material.emissive.z);
        glBindTexture(GL_TEXTURE_2D, textureId(material.texture));
        if (material.doubleSided) glDisable(GL_CULL_FACE);
        else glEnable(GL_CULL_FACE);

        glBindVertexArray(mesh.vao);
        glBindVertexBuffer(1, instanceVbo_, GLintptr(start * kInstanceStride),
                           GLsizei(kInstanceStride));
        glDrawElementsInstanced(GL_TRIANGLES, mesh.indexCount, GL_UNSIGNED_INT, nullptr,
                                GLsizei(end - start));
        ++stats_.drawCalls;
        stats_.instances += int(end - start);
        stats_.triangles += int((mesh.indexCount / 3) * GLsizei(end - start));
        start = end;
    }
    glBindVertexArray(0);
    glEnable(GL_CULL_FACE);
}

void Renderer::drawOverlay(const Mat4& view, const Mat4& proj, const OverlayBuffer& overlay) {
    if (overlay.empty()) return;
    const Mat4 viewProj = proj * view;

    const size_t bytes = overlay.vertices().size() * sizeof(OverlayVertex);
    glBindBuffer(GL_ARRAY_BUFFER, overlayVbo_);
    if (bytes > overlayVboCapacity_) {
        glBufferData(GL_ARRAY_BUFFER, GLsizeiptr(bytes), overlay.vertices().data(),
                     GL_STREAM_DRAW);
        overlayVboCapacity_ = bytes;
    } else {
        glBufferData(GL_ARRAY_BUFFER, GLsizeiptr(overlayVboCapacity_), nullptr,
                     GL_STREAM_DRAW);
        glBufferSubData(GL_ARRAY_BUFFER, 0, GLsizeiptr(bytes), overlay.vertices().data());
    }

    glUseProgram(unlitProgram_);
    glUniformMatrix4fv(uOverlayViewProj_, 1, GL_FALSE, viewProj.m);
    glUniform1i(uOverlayUseViewProj_, 1);
    glUniform1i(uOverlayTexture_, 0);
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glDisable(GL_CULL_FACE);
    glDepthMask(GL_FALSE);
    glActiveTexture(GL_TEXTURE0);
    glBindVertexArray(overlayVao_);

    for (const OverlayBuffer::Batch& b : overlay.batches()) {
        if (!b.count) continue;
        if (b.depthTest) glEnable(GL_DEPTH_TEST);
        else glDisable(GL_DEPTH_TEST);
        glUniform1i(uOverlayUseTexture_, b.texture >= 0 ? 1 : 0);
        glBindTexture(GL_TEXTURE_2D, textureId(b.texture));
        glDrawArrays(GL_TRIANGLES, GLint(b.first), GLsizei(b.count));
        ++stats_.drawCalls;
        stats_.triangles += int(b.count / 3);
    }

    glBindVertexArray(0);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glDepthMask(GL_TRUE);
    glEnable(GL_DEPTH_TEST);
    glEnable(GL_CULL_FACE);
    glDisable(GL_BLEND);
}

void Renderer::drawFade(float amount, Vec3 color) {
    if (amount <= 0.001f) return;
    const Vec4 c(color.x, color.y, color.z, saturate(amount));
    const OverlayVertex quad[6] = {
        {{-1, -1, 0}, {0, 0}, c}, {{1, -1, 0}, {1, 0}, c}, {{1, 1, 0}, {1, 1}, c},
        {{-1, -1, 0}, {0, 0}, c}, {{1, 1, 0}, {1, 1}, c},  {{-1, 1, 0}, {0, 1}, c},
    };
    glBindBuffer(GL_ARRAY_BUFFER, overlayVbo_);
    glBufferData(GL_ARRAY_BUFFER, sizeof(quad), quad, GL_STREAM_DRAW);
    overlayVboCapacity_ = sizeof(quad);

    glUseProgram(unlitProgram_);
    glUniform1i(uOverlayUseViewProj_, 0);
    glUniform1i(uOverlayUseTexture_, 0);
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glDisable(GL_DEPTH_TEST);
    glDepthMask(GL_FALSE);
    glDisable(GL_CULL_FACE);
    glBindVertexArray(overlayVao_);
    glDrawArrays(GL_TRIANGLES, 0, 6);
    glBindVertexArray(0);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glDepthMask(GL_TRUE);
    glEnable(GL_DEPTH_TEST);
    glEnable(GL_CULL_FACE);
    glDisable(GL_BLEND);
    ++stats_.drawCalls;
}

void Renderer::endEye(EyeTarget& target, GLuint swapchainImage) {
    target.resolve(swapchainImage);
    glDisable(GL_SCISSOR_TEST);
}

}  // namespace fcxr
