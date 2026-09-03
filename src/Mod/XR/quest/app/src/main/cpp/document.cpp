// SPDX-License-Identifier: LGPL-2.1-or-later
#include "document.h"

#include <algorithm>

#include "log.h"
#include "png.h"

namespace fcxr {

bool DocumentScene::load(const Document& document, Renderer* renderer, std::string* error) {
    if (!renderer) return false;
    unload(renderer);
    title_ = document.asset.sourceDocument;

    if (!document.asset.upAxis.empty() && document.asset.upAxis != "Y") {
        LOGW("document declares up_axis '%s'; this build assumes the Y-up root node",
             document.asset.upAxis.c_str());
    }

    // Decode the images once; several materials may share one.
    std::vector<int> imageHandles(document.images.size(), -1);
    for (size_t i = 0; i < document.images.size(); ++i) {
        const int chunk = document.images[i].chunk;
        if (chunk < 0 || size_t(chunk) >= document.pngChunks.size()) continue;
        Image image;
        std::string decodeError;
        if (!pngDecode(document.pngChunks[size_t(chunk)].data(),
                       document.pngChunks[size_t(chunk)].size(), &image, &decodeError)) {
            LOGW("image %zu could not be decoded: %s", i, decodeError.c_str());
            continue;
        }
        const int handle = renderer->createTexture(image, /*srgb=*/true, /*mipmap=*/true);
        if (handle >= 0) {
            imageHandles[i] = handle;
            textureHandles_.push_back(handle);
        }
    }

    materialHandles_.reserve(document.materials.size() + 1);
    for (const Material& material : document.materials) {
        RenderMaterial gpu;
        gpu.baseColor = material.baseColor;
        gpu.metallic = material.metallic;
        gpu.roughness = material.roughness;
        gpu.emissive = material.emissive;
        gpu.doubleSided = material.doubleSided;
        if (material.baseColorTexture >= 0 &&
            size_t(material.baseColorTexture) < imageHandles.size())
            gpu.texture = imageHandles[size_t(material.baseColorTexture)];
        materialHandles_.push_back(renderer->createMaterial(gpu));
    }
    const int fallbackMaterial = renderer->createMaterial(RenderMaterial());
    materialHandles_.push_back(fallbackMaterial);

    // Walk the node tree from the scene root, accumulating transforms.
    struct Frame {
        int node;
        Mat4 transform;
    };
    std::vector<Frame> stack;
    std::vector<char> visited(document.nodes.size(), 0);
    if (document.scene.root < 0 || size_t(document.scene.root) >= document.nodes.size()) {
        if (error) *error = "scene.root does not name a node";
        return false;
    }
    stack.push_back({document.scene.root,
                     document.nodes[size_t(document.scene.root)].localMatrix()});

    size_t triangles = 0;
    while (!stack.empty()) {
        const Frame frame = stack.back();
        stack.pop_back();
        if (visited[size_t(frame.node)]) continue;
        visited[size_t(frame.node)] = 1;
        const Node& node = document.nodes[size_t(frame.node)];

        if (node.visible && node.mesh >= 0 && size_t(node.mesh) < document.meshes.size()) {
            const Mesh& mesh = document.meshes[size_t(node.mesh)];
            for (const Primitive& primitive : mesh.primitives) {
                MeshData data;
                std::vector<Vec3> positions;
                if (!document.readVec3(primitive.positions, &positions) || positions.empty())
                    continue;
                std::vector<Vec3> normals;
                const bool haveNormals = document.readVec3(primitive.normals, &normals) &&
                                         normals.size() == positions.size();
                std::vector<Vec2> uvs;
                const bool haveUvs =
                    document.readVec2(primitive.uvs, &uvs) && uvs.size() == positions.size();
                std::vector<uint32_t> indices;
                if (!document.readIndices(primitive.indices, &indices)) {
                    indices.resize(positions.size());
                    for (uint32_t i = 0; i < positions.size(); ++i) indices[i] = i;
                }
                data.positions = std::move(positions);
                data.normals = haveNormals ? std::move(normals)
                                           : std::vector<Vec3>(data.positions.size(),
                                                               Vec3(0, 1, 0));
                data.uvs = haveUvs ? std::move(uvs)
                                   : std::vector<Vec2>(data.positions.size(), Vec2(0, 0));
                data.indices = std::move(indices);
                if (!haveNormals) data.computeSmoothNormals();

                DocumentPrimitive item;
                item.nodeIndex = frame.node;
                item.fcName = node.fcName.empty() ? node.name : node.fcName;
                item.nodeTransform = frame.transform;
                item.localBounds = data.bounds();
                item.materialHandle =
                    (primitive.material >= 0 &&
                     size_t(primitive.material) < materialHandles_.size() - 1)
                        ? materialHandles_[size_t(primitive.material)]
                        : fallbackMaterial;
                item.meshHandle = renderer->createMesh(data);
                triangles += data.triangleCount();
                item.cpu = std::move(data);
                if (item.meshHandle >= 0) primitives_.push_back(std::move(item));
            }
        }

        for (int child : node.children) {
            if (child < 0 || size_t(child) >= document.nodes.size()) continue;
            stack.push_back(
                {child, frame.transform * document.nodes[size_t(child)].localMatrix()});
        }
    }

    if (primitives_.empty()) {
        if (error) *error = "the document contains no drawable geometry";
        return false;
    }

    bounds_ = Aabb();
    for (const DocumentPrimitive& item : primitives_)
        bounds_.add(transformAabb(item.nodeTransform, item.localBounds));

    LOGI("document '%s': %zu primitives, %zu triangles, bounds %.3f x %.3f x %.3f m",
         title_.c_str(), primitives_.size(), triangles, bounds_.hi.x - bounds_.lo.x,
         bounds_.hi.y - bounds_.lo.y, bounds_.hi.z - bounds_.lo.z);
    rebuildInstances();
    return true;
}

void DocumentScene::unload(Renderer* renderer) {
    if (renderer) {
        for (DocumentPrimitive& item : primitives_) renderer->destroyMesh(item.meshHandle);
        for (int handle : materialHandles_) renderer->destroyMaterial(handle);
        for (int handle : textureHandles_) renderer->destroyTexture(handle);
    }
    primitives_.clear();
    instances_.clear();
    materialHandles_.clear();
    textureHandles_.clear();
    bounds_ = Aabb();
    fitScale_ = 1.0f;
    userScale_ = 1.0f;
    title_.clear();
}

void DocumentScene::place(const Mat4& anchorWorld, Vec2 anchorSize, bool fit) {
    anchor_ = anchorWorld;
    anchorSize_ = anchorSize;
    fitScale_ = 1.0f;
    if (fit && bounds_.valid() && anchorSize.x > 0.0f && anchorSize.y > 0.0f) {
        const float width = bounds_.hi.x - bounds_.lo.x;
        const float depth = bounds_.hi.z - bounds_.lo.z;
        float scale = 1.0f;
        if (width > anchorSize.x) scale = std::min(scale, anchorSize.x / width);
        if (depth > anchorSize.y) scale = std::min(scale, anchorSize.y / depth);
        fitScale_ = scale;
    }
    rebuildInstances();
}

void DocumentScene::setUserScale(float scale) {
    userScale_ = clampf(scale, 0.01f, 100.0f);
    rebuildInstances();
}

void DocumentScene::rebuildInstances() {
    // Y-up document -> anchor whose +Z is the surface normal.
    const Quat standUp = quatAxisAngle(Vec3(1, 0, 0), kPi * 0.5f);
    const float scale = fitScale_ * userScale_;
    Vec3 offset(0, 0, 0);
    if (bounds_.valid()) {
        const Vec3 centre = bounds_.centre();
        // Centre on the plate in X/Z and stand the document on it in Y.
        offset = Vec3(-centre.x, -bounds_.lo.y, -centre.z);
    }
    placement_ = anchor_ * mat4FromQuat(standUp) * mat4Scale(Vec3(scale, scale, scale)) *
                 mat4Translate(offset);

    instances_.resize(primitives_.size());
    for (size_t i = 0; i < primitives_.size(); ++i) {
        RenderInstance& instance = instances_[i];
        instance.mesh = primitives_[i].meshHandle;
        instance.material = primitives_[i].materialHandle;
        instance.transform = placement_ * primitives_[i].nodeTransform;
        instance.worldBounds = transformAabb(instance.transform, primitives_[i].localBounds);
    }
}

bool DocumentScene::raycast(Vec3 origin, Vec3 direction, float maxDistance,
                            DocumentHit* out) const {
    if (!out) return false;
    *out = DocumentHit();
    float best = maxDistance;
    const Vec3 dir = normalize(direction);
    const Vec3 invDir(dir.x != 0.0f ? 1.0f / dir.x : 1e30f,
                      dir.y != 0.0f ? 1.0f / dir.y : 1e30f,
                      dir.z != 0.0f ? 1.0f / dir.z : 1e30f);

    for (size_t p = 0; p < primitives_.size(); ++p) {
        const DocumentPrimitive& item = primitives_[p];
        const Mat4 model = placement_ * item.nodeTransform;
        const Aabb worldBounds = transformAabb(model, item.localBounds);
        float boxT = 0.0f;
        if (!rayAabb(origin, invDir, worldBounds, best, &boxT)) continue;

        // Move the ray into the primitive's own space so the triangle test
        // needs no per-vertex transform.
        const Mat4 inverse = mat4Inverse(model);
        const Vec3 localOrigin = transformPoint(inverse, origin);
        const Vec3 localDir = transformDir(inverse, dir);
        const float localLength = length(localDir);
        if (localLength < 1e-12f) continue;
        const Vec3 localDirNormalised = localDir / localLength;

        const MeshData& mesh = item.cpu;
        for (size_t i = 0; i + 2 < mesh.indices.size(); i += 3) {
            const uint32_t a = mesh.indices[i], b = mesh.indices[i + 1],
                           c = mesh.indices[i + 2];
            if (a >= mesh.positions.size() || b >= mesh.positions.size() ||
                c >= mesh.positions.size())
                continue;
            float t = 0.0f, u = 0.0f, v = 0.0f;
            if (!rayTriangle(localOrigin, localDirNormalised, mesh.positions[a],
                             mesh.positions[b], mesh.positions[c], true, &t, &u, &v))
                continue;
            // t is measured along the normalised local direction; convert back
            // to world distance.
            const float worldT = t / localLength;
            if (worldT >= best) continue;
            best = worldT;
            out->hit = true;
            out->primitive = int(p);
            out->distance = worldT;
            out->triangle = uint32_t(i / 3);
            const float w = 1.0f - u - v;
            const Vec3 localPoint =
                mesh.positions[a] * w + mesh.positions[b] * u + mesh.positions[c] * v;
            out->position = transformPoint(model, localPoint);
            Vec3 localNormal(0, 1, 0);
            if (a < mesh.normals.size() && b < mesh.normals.size() && c < mesh.normals.size())
                localNormal = mesh.normals[a] * w + mesh.normals[b] * u + mesh.normals[c] * v;
            out->normal = normalize(transformDir(normalMatrix(model), localNormal));
            if (a < mesh.uvs.size() && b < mesh.uvs.size() && c < mesh.uvs.size()) {
                out->uv = mesh.uvs[a] * w + mesh.uvs[b] * u + mesh.uvs[c] * v;
            }
        }
    }
    return out->hit;
}

void DocumentScene::setPaintTexture(int primitive, int textureHandle, Renderer* renderer) {
    if (primitive < 0 || size_t(primitive) >= primitives_.size() || !renderer) return;
    primitives_[size_t(primitive)].paintTexture = textureHandle;
    // Swap in a material that samples the painted texture.
    RenderMaterial material;
    material.baseColor = Vec4(1, 1, 1, 1);
    material.roughness = 0.75f;
    material.texture = textureHandle;
    const int handle = renderer->createMaterial(material);
    materialHandles_.push_back(handle);
    primitives_[size_t(primitive)].materialHandle = handle;
    rebuildInstances();
}

}  // namespace fcxr
