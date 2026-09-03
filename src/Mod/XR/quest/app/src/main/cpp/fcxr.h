// SPDX-License-Identifier: LGPL-2.1-or-later
//
// FCXR v1 container — reader and writer. See ARCHITECTURE.md §1 and §4.
//
// Layout, restated exactly as implemented here (and mirrored by
// quest/tools/verify_fcxr.py so the two readers can be cross-checked):
//
//   header: 'FCXR', uint32 version(=1), uint32 total_length (whole file)
//   chunk:  uint32 payload_length (padding NOT included)
//           char[4] type          'JSON' | 'BIN\0' | 'PNG\0'
//           uint8[payload_length] payload
//           padding to the next 4 byte boundary, 0x20 for JSON, 0x00 otherwise
//
// All integers are little endian. Exactly one JSON chunk, first; at most one
// BIN chunk; any number of PNG chunks, whose order defines the `chunk` index
// used by manifest `images` entries.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "json.h"
#include "math3d.h"

namespace fcxr {

enum class ComponentType { F32, U32, U16, U8, Unknown };
enum class AccessorType { Scalar, Vec2, Vec3, Vec4, Unknown };

size_t componentSize(ComponentType c);
size_t componentCount(AccessorType t);
const char* componentName(ComponentType c);
const char* accessorTypeName(AccessorType t);

struct Accessor {
    size_t offset = 0;  // relative to the BIN payload, 4 byte aligned
    size_t length = 0;
    AccessorType type = AccessorType::Unknown;
    ComponentType component = ComponentType::Unknown;
    size_t count = 0;
};

struct Primitive {
    int positions = -1;
    int normals = -1;
    int uvs = -1;
    int indices = -1;
    int material = -1;
};

struct Mesh {
    std::string name;
    std::vector<Primitive> primitives;
};

struct Node {
    std::string name;
    std::string fcName;
    int mesh = -1;
    Vec3 translation{0, 0, 0};
    Quat rotation;
    Vec3 scale{1, 1, 1};
    std::vector<int> children;
    bool visible = true;
    Mat4 localMatrix() const { return mat4TRS(translation, rotation, scale); }
};

struct Material {
    std::string name;
    Vec4 baseColor{0.8f, 0.8f, 0.8f, 1.0f};  // linear
    float metallic = 0.0f;
    float roughness = 0.6f;
    Vec3 emissive{0, 0, 0};
    int baseColorTexture = -1;
    bool doubleSided = false;
};

struct ImageRef {
    std::string name;
    std::string mime = "image/png";
    int chunk = -1;  // index into Document::pngChunks
};

struct AssetInfo {
    std::string generator = "FreeCAD-XR Quest";
    int version = 1;
    double unitScale = 0.001;  // document units -> metres
    std::string created;
    std::string sourceDocument;
};

struct SceneInfo {
    int root = 0;
    std::string environment;
    float userScale = 1.0f;
};

// ------------------------------------------------------------------- paint
enum class BlendMode { Normal, Multiply, Add, Erase };
const char* blendModeName(BlendMode m);
BlendMode blendModeFromName(const std::string& s);

struct PaintLayer {
    std::string name = "Layer";
    int image = -1;  // index into Document::images
    float opacity = 1.0f;
    BlendMode blend = BlendMode::Normal;
    bool visible = true;
    int resolution[2] = {1024, 1024};
};

struct PaintTarget {
    std::string fcName;
    std::vector<PaintLayer> layers;
};

struct StrokePoint {
    Vec3 p{0, 0, 0};
    Vec3 n{0, 1, 0};
    float r = 0.01f;
    float t = 0.0f;
};

struct Stroke3D {
    std::string brush = "ribbon";
    Vec4 color{1, 1, 1, 1};
    float width = 0.01f;
    std::vector<StrokePoint> points;
};

struct PaintDoc {
    bool present = false;
    int version = 1;
    std::vector<PaintTarget> targets;
    std::vector<Stroke3D> strokes3d;
    std::vector<Vec4> palette;
};

// ------------------------------------------------------------------ vector
enum class VectorNodeType { Corner, Smooth, Symmetric };
const char* vectorNodeTypeName(VectorNodeType t);
VectorNodeType vectorNodeTypeFromName(const std::string& s);

struct VectorNode {
    Vec2 point{0, 0};
    bool hasIn = false;
    bool hasOut = false;
    Vec2 in{0, 0};   // handle, relative to `point`
    Vec2 out{0, 0};
    VectorNodeType type = VectorNodeType::Corner;
};

struct VectorPath {
    std::string id;
    bool closed = false;
    std::vector<VectorNode> nodes;
    Vec4 strokeColor{0, 0, 0, 1};
    float strokeWidth = 0.5f;
    bool hasFill = false;
    Vec4 fillColor{1, 1, 1, 1};
    std::string target = "draft";
};

struct VectorDoc {
    bool present = false;
    int version = 1;
    Vec3 planeOrigin{0, 0, 0};
    Quat planeRotation;
    double unitScale = 0.001;
    std::vector<VectorPath> paths;
};

// ---------------------------------------------------------------- document
class Document {
public:
    AssetInfo asset;
    SceneInfo scene;
    std::vector<Node> nodes;
    std::vector<Mesh> meshes;
    std::vector<Accessor> accessors;
    std::vector<Material> materials;
    std::vector<ImageRef> images;
    PaintDoc paint;
    VectorDoc vector;

    std::vector<uint8_t> bin;                       // the single BIN payload
    std::vector<std::vector<uint8_t>> pngChunks;    // raw PNG bytes, in order

    void clear();

    // Accessor readers. All return false if the accessor index is out of
    // range, its declared layout does not match, or it runs off the buffer.
    bool readVec3(int accessor, std::vector<Vec3>* out) const;
    bool readVec2(int accessor, std::vector<Vec2>* out) const;
    // Accepts U8/U16/U32 SCALAR accessors and widens to uint32.
    bool readIndices(int accessor, std::vector<uint32_t>* out) const;

    // Byte range of an accessor inside `bin`, validated.
    bool accessorRange(int index, const uint8_t** ptr, size_t* bytes) const;

    // World transform of node `index` following `children` links from
    // `scene.root`. Returns identity for unreachable nodes.
    Mat4 worldMatrix(int index) const;

    // Number of triangles a primitive expands to (for budgeting).
    size_t primitiveTriangleCount(const Primitive& p) const;
};

// Parses a whole `.fcxr` blob. On failure fills `error` and returns false.
bool fcxrRead(const uint8_t* data, size_t size, Document* out, std::string* error = nullptr);

// Serialises a document. `bin` and `pngChunks` are emitted verbatim; the
// manifest is rebuilt from the structured fields.
bool fcxrWrite(const Document& doc, std::vector<uint8_t>* out, std::string* error = nullptr);

// Manifest <-> struct conversion, exposed because /api/v1/vector posts the
// vector document as bare JSON rather than inside a container.
json::Value paintToJson(const PaintDoc& p);
json::Value vectorToJson(const VectorDoc& v);
bool paintFromJson(const json::Value& v, PaintDoc* out);
bool vectorFromJson(const json::Value& v, VectorDoc* out);

}  // namespace fcxr
