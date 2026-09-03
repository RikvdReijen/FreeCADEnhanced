// SPDX-License-Identifier: LGPL-2.1-or-later
#include "fcxr.h"

#include <cstring>

namespace fcxr {

// ------------------------------------------------------------- enum tables

size_t componentSize(ComponentType c) {
    switch (c) {
        case ComponentType::F32: return 4;
        case ComponentType::U32: return 4;
        case ComponentType::U16: return 2;
        case ComponentType::U8: return 1;
        default: return 0;
    }
}
size_t componentCount(AccessorType t) {
    switch (t) {
        case AccessorType::Scalar: return 1;
        case AccessorType::Vec2: return 2;
        case AccessorType::Vec3: return 3;
        case AccessorType::Vec4: return 4;
        default: return 0;
    }
}
const char* componentName(ComponentType c) {
    switch (c) {
        case ComponentType::F32: return "F32";
        case ComponentType::U32: return "U32";
        case ComponentType::U16: return "U16";
        case ComponentType::U8: return "U8";
        default: return "";
    }
}
const char* accessorTypeName(AccessorType t) {
    switch (t) {
        case AccessorType::Scalar: return "SCALAR";
        case AccessorType::Vec2: return "VEC2";
        case AccessorType::Vec3: return "VEC3";
        case AccessorType::Vec4: return "VEC4";
        default: return "";
    }
}
static ComponentType componentFromName(const std::string& s) {
    if (s == "F32") return ComponentType::F32;
    if (s == "U32") return ComponentType::U32;
    if (s == "U16") return ComponentType::U16;
    if (s == "U8") return ComponentType::U8;
    return ComponentType::Unknown;
}
static AccessorType accessorTypeFromName(const std::string& s) {
    if (s == "SCALAR") return AccessorType::Scalar;
    if (s == "VEC2") return AccessorType::Vec2;
    if (s == "VEC3") return AccessorType::Vec3;
    if (s == "VEC4") return AccessorType::Vec4;
    return AccessorType::Unknown;
}

const char* blendModeName(BlendMode m) {
    switch (m) {
        case BlendMode::Multiply: return "multiply";
        case BlendMode::Add: return "add";
        case BlendMode::Erase: return "erase";
        default: return "normal";
    }
}
BlendMode blendModeFromName(const std::string& s) {
    if (s == "multiply") return BlendMode::Multiply;
    if (s == "add") return BlendMode::Add;
    if (s == "erase") return BlendMode::Erase;
    return BlendMode::Normal;
}
const char* vectorNodeTypeName(VectorNodeType t) {
    switch (t) {
        case VectorNodeType::Smooth: return "smooth";
        case VectorNodeType::Symmetric: return "symmetric";
        default: return "corner";
    }
}
VectorNodeType vectorNodeTypeFromName(const std::string& s) {
    if (s == "smooth") return VectorNodeType::Smooth;
    if (s == "symmetric") return VectorNodeType::Symmetric;
    return VectorNodeType::Corner;
}

// ------------------------------------------------------------ small helpers

static bool fail(std::string* error, const std::string& msg) {
    if (error) *error = msg;
    return false;
}
static uint32_t readU32le(const uint8_t* p) {
    return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}
static void writeU32le(std::vector<uint8_t>& out, uint32_t v) {
    out.push_back(uint8_t(v));
    out.push_back(uint8_t(v >> 8));
    out.push_back(uint8_t(v >> 16));
    out.push_back(uint8_t(v >> 24));
}
static size_t padTo4(size_t n) { return (4 - (n & 3)) & 3; }

// Optional index: JSON null / missing -> -1.
static int optIndex(const json::Value& v) {
    return v.isNumber() ? v.asInt(-1) : -1;
}
static json::Value indexValue(int i) {
    return i >= 0 ? json::Value(i) : json::Value();
}
static Vec3 readVec3Value(const json::Value& v, Vec3 def) {
    float f[3];
    return json::readFloats(v, f, 3) ? Vec3(f[0], f[1], f[2]) : def;
}
static Vec4 readVec4Value(const json::Value& v, Vec4 def) {
    float f[4];
    return json::readFloats(v, f, 4) ? Vec4(f[0], f[1], f[2], f[3]) : def;
}
static Quat readQuatValue(const json::Value& v, Quat def) {
    float f[4];
    if (!json::readFloats(v, f, 4)) return def;
    // Kept bit-exact when the input is already unit length (so that
    // read-then-write reproduces the file byte for byte); only obviously
    // malformed quaternions are repaired.
    const float lenSq = f[0] * f[0] + f[1] * f[1] + f[2] * f[2] + f[3] * f[3];
    if (lenSq < 1e-8f) return def;
    Quat q(f[0], f[1], f[2], f[3]);
    return std::fabs(lenSq - 1.0f) > 1e-4f ? normalize(q) : q;
}
static json::Value vec3Value(Vec3 v) {
    json::Value a = json::Value::makeArray();
    a.push(json::Value(double(v.x)));
    a.push(json::Value(double(v.y)));
    a.push(json::Value(double(v.z)));
    return a;
}
static json::Value vec4Value(Vec4 v) {
    json::Value a = json::Value::makeArray();
    a.push(json::Value(double(v.x)));
    a.push(json::Value(double(v.y)));
    a.push(json::Value(double(v.z)));
    a.push(json::Value(double(v.w)));
    return a;
}
static json::Value vec2Value(Vec2 v) {
    json::Value a = json::Value::makeArray();
    a.push(json::Value(double(v.x)));
    a.push(json::Value(double(v.y)));
    return a;
}
static json::Value quatValue(const Quat& q) {
    json::Value a = json::Value::makeArray();
    a.push(json::Value(double(q.x)));
    a.push(json::Value(double(q.y)));
    a.push(json::Value(double(q.z)));
    a.push(json::Value(double(q.w)));
    return a;
}

// --------------------------------------------------------------- accessors

void Document::clear() { *this = Document(); }

bool Document::accessorRange(int index, const uint8_t** ptr, size_t* bytes) const {
    if (index < 0 || size_t(index) >= accessors.size()) return false;
    const Accessor& a = accessors[size_t(index)];
    const size_t stride = componentSize(a.component) * componentCount(a.type);
    if (stride == 0) return false;
    const size_t need = stride * a.count;
    if (need > a.length) return false;
    if (a.offset % 4 != 0) return false;  // §1: accessor offsets are 4 byte aligned
    if (a.offset > bin.size() || a.length > bin.size() - a.offset) return false;
    if (ptr) *ptr = bin.data() + a.offset;
    if (bytes) *bytes = need;
    return true;
}

bool Document::readVec3(int index, std::vector<Vec3>* out) const {
    const uint8_t* p = nullptr;
    size_t bytes = 0;
    if (!out || !accessorRange(index, &p, &bytes)) return false;
    const Accessor& a = accessors[size_t(index)];
    if (a.type != AccessorType::Vec3 || a.component != ComponentType::F32) return false;
    out->resize(a.count);
    for (size_t i = 0; i < a.count; ++i) {
        float f[3];
        std::memcpy(f, p + i * 12, 12);  // memcpy: the buffer is only 4 byte aligned
        (*out)[i] = Vec3(f[0], f[1], f[2]);
    }
    return true;
}

bool Document::readVec2(int index, std::vector<Vec2>* out) const {
    const uint8_t* p = nullptr;
    size_t bytes = 0;
    if (!out || !accessorRange(index, &p, &bytes)) return false;
    const Accessor& a = accessors[size_t(index)];
    if (a.type != AccessorType::Vec2 || a.component != ComponentType::F32) return false;
    out->resize(a.count);
    for (size_t i = 0; i < a.count; ++i) {
        float f[2];
        std::memcpy(f, p + i * 8, 8);
        (*out)[i] = Vec2(f[0], f[1]);
    }
    return true;
}

bool Document::readIndices(int index, std::vector<uint32_t>* out) const {
    const uint8_t* p = nullptr;
    size_t bytes = 0;
    if (!out || !accessorRange(index, &p, &bytes)) return false;
    const Accessor& a = accessors[size_t(index)];
    if (a.type != AccessorType::Scalar) return false;
    out->resize(a.count);
    switch (a.component) {
        case ComponentType::U32:
            for (size_t i = 0; i < a.count; ++i) {
                uint32_t v;
                std::memcpy(&v, p + i * 4, 4);
                (*out)[i] = v;
            }
            return true;
        case ComponentType::U16:
            for (size_t i = 0; i < a.count; ++i) {
                uint16_t v;
                std::memcpy(&v, p + i * 2, 2);
                (*out)[i] = v;
            }
            return true;
        case ComponentType::U8:
            for (size_t i = 0; i < a.count; ++i) (*out)[i] = p[i];
            return true;
        default:
            return false;
    }
}

size_t Document::primitiveTriangleCount(const Primitive& p) const {
    if (p.indices >= 0 && size_t(p.indices) < accessors.size())
        return accessors[size_t(p.indices)].count / 3;
    if (p.positions >= 0 && size_t(p.positions) < accessors.size())
        return accessors[size_t(p.positions)].count / 3;
    return 0;
}

Mat4 Document::worldMatrix(int index) const {
    // Depth-first walk from the scene root, accumulating transforms. Node
    // graphs here are small (tens to a few thousand nodes) and this is only
    // used on load, so a search is fine and needs no parent table.
    struct Frame { int node; Mat4 xf; };
    std::vector<Frame> stack;
    std::vector<char> seen(nodes.size(), 0);
    if (scene.root < 0 || size_t(scene.root) >= nodes.size()) return Mat4();
    stack.push_back({scene.root, nodes[size_t(scene.root)].localMatrix()});
    while (!stack.empty()) {
        Frame f = stack.back();
        stack.pop_back();
        if (f.node == index) return f.xf;
        if (seen[size_t(f.node)]) continue;  // guards against cycles
        seen[size_t(f.node)] = 1;
        for (int c : nodes[size_t(f.node)].children) {
            if (c < 0 || size_t(c) >= nodes.size()) continue;
            stack.push_back({c, f.xf * nodes[size_t(c)].localMatrix()});
        }
    }
    return Mat4();
}

// ----------------------------------------------------------- paint / vector

json::Value paintToJson(const PaintDoc& p) {
    json::Value root = json::Value::makeObject();
    root.set("version", json::Value(p.version));

    json::Value targets = json::Value::makeArray();
    for (const PaintTarget& t : p.targets) {
        json::Value tv = json::Value::makeObject();
        tv.set("fc_name", json::Value(t.fcName));
        json::Value layers = json::Value::makeArray();
        for (const PaintLayer& l : t.layers) {
            json::Value lv = json::Value::makeObject();
            lv.set("name", json::Value(l.name));
            lv.set("image", indexValue(l.image));
            lv.set("opacity", json::Value(double(l.opacity)));
            lv.set("blend", json::Value(std::string(blendModeName(l.blend))));
            lv.set("visible", json::Value(l.visible));
            json::Value res = json::Value::makeArray();
            res.push(json::Value(l.resolution[0]));
            res.push(json::Value(l.resolution[1]));
            lv.set("resolution", res);
            layers.push(lv);
        }
        tv.set("layers", layers);
        targets.push(tv);
    }
    root.set("targets", targets);

    json::Value strokes = json::Value::makeArray();
    for (const Stroke3D& s : p.strokes3d) {
        json::Value sv = json::Value::makeObject();
        sv.set("brush", json::Value(s.brush));
        sv.set("color", vec4Value(s.color));
        sv.set("width", json::Value(double(s.width)));
        json::Value pts = json::Value::makeArray();
        for (const StrokePoint& sp : s.points) {
            json::Value pv = json::Value::makeObject();
            pv.set("p", vec3Value(sp.p));
            pv.set("n", vec3Value(sp.n));
            pv.set("r", json::Value(double(sp.r)));
            pv.set("t", json::Value(double(sp.t)));
            pts.push(pv);
        }
        sv.set("points", pts);
        strokes.push(sv);
    }
    root.set("strokes3d", strokes);

    json::Value palette = json::Value::makeArray();
    for (const Vec4& c : p.palette) palette.push(vec4Value(c));
    root.set("palette", palette);
    return root;
}

bool paintFromJson(const json::Value& v, PaintDoc* out) {
    if (!v.isObject() || !out) return false;
    *out = PaintDoc();
    out->present = true;
    out->version = v["version"].asInt(1);

    const json::Value& targets = v["targets"];
    for (size_t i = 0; i < targets.size(); ++i) {
        const json::Value& tv = targets[i];
        PaintTarget t;
        t.fcName = tv["fc_name"].asString();
        const json::Value& layers = tv["layers"];
        for (size_t j = 0; j < layers.size(); ++j) {
            const json::Value& lv = layers[j];
            PaintLayer l;
            l.name = lv["name"].asString();
            l.image = optIndex(lv["image"]);
            l.opacity = lv["opacity"].asFloat(1.0f);
            l.blend = blendModeFromName(lv["blend"].asString());
            l.visible = lv["visible"].asBool(true);
            const json::Value& res = lv["resolution"];
            if (res.size() >= 2) {
                l.resolution[0] = res[0].asInt(1024);
                l.resolution[1] = res[1].asInt(1024);
            }
            t.layers.push_back(std::move(l));
        }
        out->targets.push_back(std::move(t));
    }

    const json::Value& strokes = v["strokes3d"];
    for (size_t i = 0; i < strokes.size(); ++i) {
        const json::Value& sv = strokes[i];
        Stroke3D s;
        s.brush = sv["brush"].asString();
        if (s.brush.empty()) s.brush = "ribbon";
        s.color = readVec4Value(sv["color"], s.color);
        s.width = sv["width"].asFloat(0.01f);
        const json::Value& pts = sv["points"];
        s.points.reserve(pts.size());
        for (size_t j = 0; j < pts.size(); ++j) {
            const json::Value& pv = pts[j];
            StrokePoint sp;
            sp.p = readVec3Value(pv["p"], sp.p);
            sp.n = readVec3Value(pv["n"], sp.n);
            sp.r = pv["r"].asFloat(s.width * 0.5f);
            sp.t = pv["t"].asFloat(0.0f);
            s.points.push_back(sp);
        }
        out->strokes3d.push_back(std::move(s));
    }

    const json::Value& palette = v["palette"];
    for (size_t i = 0; i < palette.size(); ++i)
        out->palette.push_back(readVec4Value(palette[i], Vec4(1, 1, 1, 1)));
    return true;
}

json::Value vectorToJson(const VectorDoc& d) {
    json::Value root = json::Value::makeObject();
    root.set("version", json::Value(d.version));
    json::Value plane = json::Value::makeObject();
    plane.set("origin", vec3Value(d.planeOrigin));
    plane.set("rotation", quatValue(d.planeRotation));
    root.set("plane", plane);
    root.set("unit_scale", json::Value(d.unitScale));

    json::Value paths = json::Value::makeArray();
    for (const VectorPath& p : d.paths) {
        json::Value pv = json::Value::makeObject();
        pv.set("id", json::Value(p.id));
        pv.set("closed", json::Value(p.closed));
        json::Value nodes = json::Value::makeArray();
        for (const VectorNode& n : p.nodes) {
            json::Value nv = json::Value::makeObject();
            nv.set("point", vec2Value(n.point));
            nv.set("in", n.hasIn ? vec2Value(n.in) : json::Value());
            nv.set("out", n.hasOut ? vec2Value(n.out) : json::Value());
            nv.set("type", json::Value(std::string(vectorNodeTypeName(n.type))));
            nodes.push(nv);
        }
        pv.set("nodes", nodes);
        json::Value stroke = json::Value::makeObject();
        stroke.set("color", vec4Value(p.strokeColor));
        stroke.set("width", json::Value(double(p.strokeWidth)));
        pv.set("stroke", stroke);
        if (p.hasFill) {
            json::Value fill = json::Value::makeObject();
            fill.set("color", vec4Value(p.fillColor));
            pv.set("fill", fill);
        } else {
            pv.set("fill", json::Value());
        }
        pv.set("target", json::Value(p.target));
        paths.push(pv);
    }
    root.set("paths", paths);
    return root;
}

bool vectorFromJson(const json::Value& v, VectorDoc* out) {
    if (!v.isObject() || !out) return false;
    *out = VectorDoc();
    out->present = true;
    out->version = v["version"].asInt(1);
    out->planeOrigin = readVec3Value(v["plane"]["origin"], Vec3(0, 0, 0));
    out->planeRotation = readQuatValue(v["plane"]["rotation"], Quat());
    out->unitScale = v["unit_scale"].asDouble(0.001);

    const json::Value& paths = v["paths"];
    for (size_t i = 0; i < paths.size(); ++i) {
        const json::Value& pv = paths[i];
        VectorPath p;
        p.id = pv["id"].asString();
        p.closed = pv["closed"].asBool(false);
        const json::Value& nodes = pv["nodes"];
        for (size_t j = 0; j < nodes.size(); ++j) {
            const json::Value& nv = nodes[j];
            VectorNode n;
            float f[2];
            if (json::readFloats(nv["point"], f, 2)) n.point = Vec2(f[0], f[1]);
            if (json::readFloats(nv["in"], f, 2)) { n.hasIn = true; n.in = Vec2(f[0], f[1]); }
            if (json::readFloats(nv["out"], f, 2)) { n.hasOut = true; n.out = Vec2(f[0], f[1]); }
            n.type = vectorNodeTypeFromName(nv["type"].asString());
            p.nodes.push_back(n);
        }
        p.strokeColor = readVec4Value(pv["stroke"]["color"], p.strokeColor);
        p.strokeWidth = pv["stroke"]["width"].asFloat(0.5f);
        if (pv["fill"].isObject()) {
            p.hasFill = true;
            p.fillColor = readVec4Value(pv["fill"]["color"], p.fillColor);
        }
        p.target = pv["target"].asString();
        if (p.target.empty()) p.target = "draft";
        out->paths.push_back(std::move(p));
    }
    return true;
}

// -------------------------------------------------------------- manifest IO

static bool manifestFromJson(const json::Value& m, Document* doc, std::string* error) {
    const json::Value& asset = m["asset"];
    doc->asset.generator = asset["generator"].asString();
    doc->asset.version = asset["version"].asInt(1);
    doc->asset.unitScale = asset["unit_scale"].asDouble(0.001);
    doc->asset.created = asset["created"].asString();
    doc->asset.sourceDocument = asset["source_document"].asString();

    const json::Value& scene = m["scene"];
    doc->scene.root = scene["root"].asInt(0);
    doc->scene.environment = scene["environment"].asString();
    doc->scene.userScale = scene["user_scale"].asFloat(1.0f);
    if (!(doc->scene.userScale > 0.0f)) doc->scene.userScale = 1.0f;

    const json::Value& nodes = m["nodes"];
    doc->nodes.reserve(nodes.size());
    for (size_t i = 0; i < nodes.size(); ++i) {
        const json::Value& nv = nodes[i];
        Node n;
        n.name = nv["name"].asString();
        n.fcName = nv["fc_name"].asString();
        n.mesh = optIndex(nv["mesh"]);
        n.translation = readVec3Value(nv["translation"], Vec3(0, 0, 0));
        n.rotation = readQuatValue(nv["rotation"], Quat());
        n.scale = readVec3Value(nv["scale"], Vec3(1, 1, 1));
        n.visible = nv["visible"].asBool(true);
        const json::Value& ch = nv["children"];
        for (size_t j = 0; j < ch.size(); ++j) n.children.push_back(ch[j].asInt(-1));
        doc->nodes.push_back(std::move(n));
    }

    const json::Value& meshes = m["meshes"];
    doc->meshes.reserve(meshes.size());
    for (size_t i = 0; i < meshes.size(); ++i) {
        const json::Value& mv = meshes[i];
        Mesh mesh;
        mesh.name = mv["name"].asString();
        const json::Value& prims = mv["primitives"];
        for (size_t j = 0; j < prims.size(); ++j) {
            const json::Value& pv = prims[j];
            Primitive p;
            p.positions = optIndex(pv["positions"]);
            p.normals = optIndex(pv["normals"]);
            p.uvs = optIndex(pv["uvs"]);
            p.indices = optIndex(pv["indices"]);
            p.material = optIndex(pv["material"]);
            mesh.primitives.push_back(p);
        }
        doc->meshes.push_back(std::move(mesh));
    }

    const json::Value& accessors = m["accessors"];
    doc->accessors.reserve(accessors.size());
    for (size_t i = 0; i < accessors.size(); ++i) {
        const json::Value& av = accessors[i];
        Accessor a;
        int64_t off = av["offset"].asInt64(-1);
        int64_t len = av["length"].asInt64(-1);
        int64_t cnt = av["count"].asInt64(-1);
        if (off < 0 || len < 0 || cnt < 0)
            return fail(error, "accessor " + std::to_string(i) + " has invalid offset/length/count");
        a.offset = size_t(off);
        a.length = size_t(len);
        a.count = size_t(cnt);
        a.type = accessorTypeFromName(av["type"].asString());
        a.component = componentFromName(av["component"].asString());
        if (a.type == AccessorType::Unknown || a.component == ComponentType::Unknown)
            return fail(error, "accessor " + std::to_string(i) + " has an unknown type");
        doc->accessors.push_back(a);
    }

    const json::Value& materials = m["materials"];
    doc->materials.reserve(materials.size());
    for (size_t i = 0; i < materials.size(); ++i) {
        const json::Value& mv = materials[i];
        Material mat;
        mat.name = mv["name"].asString();
        mat.baseColor = readVec4Value(mv["base_color"], mat.baseColor);
        mat.metallic = saturate(mv["metallic"].asFloat(0.0f));
        mat.roughness = clampf(mv["roughness"].asFloat(0.6f), 0.02f, 1.0f);
        mat.emissive = readVec3Value(mv["emissive"], Vec3(0, 0, 0));
        mat.baseColorTexture = optIndex(mv["base_color_texture"]);
        mat.doubleSided = mv["double_sided"].asBool(false);
        doc->materials.push_back(mat);
    }

    const json::Value& images = m["images"];
    doc->images.reserve(images.size());
    for (size_t i = 0; i < images.size(); ++i) {
        const json::Value& iv = images[i];
        ImageRef img;
        img.name = iv["name"].asString();
        img.mime = iv["mime"].asString();
        if (img.mime.empty()) img.mime = "image/png";
        img.chunk = optIndex(iv["chunk"]);
        doc->images.push_back(std::move(img));
    }

    if (m["paint"].isObject()) paintFromJson(m["paint"], &doc->paint);
    if (m["vector"].isObject()) vectorFromJson(m["vector"], &doc->vector);
    return true;
}

static json::Value manifestToJson(const Document& doc) {
    json::Value m = json::Value::makeObject();

    json::Value asset = json::Value::makeObject();
    asset.set("generator", json::Value(doc.asset.generator));
    asset.set("version", json::Value(doc.asset.version));
    asset.set("unit_scale", json::Value(doc.asset.unitScale));
    asset.set("created", json::Value(doc.asset.created));
    asset.set("source_document", json::Value(doc.asset.sourceDocument));
    m.set("asset", asset);

    json::Value scene = json::Value::makeObject();
    scene.set("root", json::Value(doc.scene.root));
    scene.set("environment", json::Value(doc.scene.environment));
    scene.set("user_scale", json::Value(double(doc.scene.userScale)));
    m.set("scene", scene);

    json::Value nodes = json::Value::makeArray();
    for (const Node& n : doc.nodes) {
        json::Value nv = json::Value::makeObject();
        nv.set("name", json::Value(n.name));
        nv.set("mesh", indexValue(n.mesh));
        nv.set("translation", vec3Value(n.translation));
        nv.set("rotation", quatValue(n.rotation));
        nv.set("scale", vec3Value(n.scale));
        json::Value ch = json::Value::makeArray();
        for (int c : n.children) ch.push(json::Value(c));
        nv.set("children", ch);
        nv.set("fc_name", json::Value(n.fcName));
        nv.set("visible", json::Value(n.visible));
        nodes.push(nv);
    }
    m.set("nodes", nodes);

    json::Value meshes = json::Value::makeArray();
    for (const Mesh& mesh : doc.meshes) {
        json::Value mv = json::Value::makeObject();
        mv.set("name", json::Value(mesh.name));
        json::Value prims = json::Value::makeArray();
        for (const Primitive& p : mesh.primitives) {
            json::Value pv = json::Value::makeObject();
            pv.set("positions", indexValue(p.positions));
            pv.set("normals", indexValue(p.normals));
            pv.set("uvs", indexValue(p.uvs));
            pv.set("indices", indexValue(p.indices));
            pv.set("material", indexValue(p.material));
            prims.push(pv);
        }
        mv.set("primitives", prims);
        meshes.push(mv);
    }
    m.set("meshes", meshes);

    json::Value accessors = json::Value::makeArray();
    for (const Accessor& a : doc.accessors) {
        json::Value av = json::Value::makeObject();
        av.set("offset", json::Value(int64_t(a.offset)));
        av.set("length", json::Value(int64_t(a.length)));
        av.set("type", json::Value(std::string(accessorTypeName(a.type))));
        av.set("component", json::Value(std::string(componentName(a.component))));
        av.set("count", json::Value(int64_t(a.count)));
        accessors.push(av);
    }
    m.set("accessors", accessors);

    json::Value materials = json::Value::makeArray();
    for (const Material& mat : doc.materials) {
        json::Value mv = json::Value::makeObject();
        mv.set("name", json::Value(mat.name));
        mv.set("base_color", vec4Value(mat.baseColor));
        mv.set("metallic", json::Value(double(mat.metallic)));
        mv.set("roughness", json::Value(double(mat.roughness)));
        mv.set("emissive", vec3Value(mat.emissive));
        mv.set("base_color_texture", indexValue(mat.baseColorTexture));
        mv.set("double_sided", json::Value(mat.doubleSided));
        materials.push(mv);
    }
    m.set("materials", materials);

    json::Value images = json::Value::makeArray();
    for (const ImageRef& img : doc.images) {
        json::Value iv = json::Value::makeObject();
        iv.set("name", json::Value(img.name));
        iv.set("mime", json::Value(img.mime));
        iv.set("chunk", indexValue(img.chunk));
        images.push(iv);
    }
    m.set("images", images);

    if (doc.paint.present) m.set("paint", paintToJson(doc.paint));
    if (doc.vector.present) m.set("vector", vectorToJson(doc.vector));
    return m;
}

// ------------------------------------------------------------- container IO

bool fcxrRead(const uint8_t* data, size_t size, Document* out, std::string* error) {
    if (!data || !out) return fail(error, "null argument");
    out->clear();
    if (size < 12) return fail(error, "file is shorter than the FCXR header");
    if (std::memcmp(data, "FCXR", 4) != 0) return fail(error, "bad FCXR magic");
    const uint32_t version = readU32le(data + 4);
    const uint32_t totalLength = readU32le(data + 8);
    if (version != 1) return fail(error, "unsupported FCXR version " + std::to_string(version));
    if (totalLength < 12) return fail(error, "bad FCXR total_length");
    if (totalLength > size)
        return fail(error, "FCXR total_length " + std::to_string(totalLength) +
                               " exceeds the " + std::to_string(size) + " bytes available");

    bool haveJson = false;
    bool haveBin = false;
    size_t pos = 12;
    while (pos + 8 <= totalLength) {
        const uint32_t payloadLength = readU32le(data + pos);
        const char* type = reinterpret_cast<const char*>(data + pos + 4);
        const size_t payloadStart = pos + 8;
        if (payloadLength > totalLength - payloadStart)
            return fail(error, "FCXR chunk payload runs past the end of the file");
        const uint8_t* payload = data + payloadStart;

        if (!std::memcmp(type, "JSON", 4)) {
            if (haveJson) return fail(error, "more than one JSON chunk");
            if (pos != 12) return fail(error, "the JSON chunk must come first");
            json::ParseError perr;
            json::Value manifest =
                json::parse(reinterpret_cast<const char*>(payload), payloadLength, &perr);
            if (!perr.ok)
                return fail(error, "manifest JSON parse error at line " +
                                       std::to_string(perr.line) + " column " +
                                       std::to_string(perr.column) + ": " + perr.message);
            if (!manifestFromJson(manifest, out, error)) return false;
            haveJson = true;
        } else if (!std::memcmp(type, "BIN\0", 4)) {
            if (haveBin) return fail(error, "more than one BIN chunk");
            out->bin.assign(payload, payload + payloadLength);
            haveBin = true;
        } else if (!std::memcmp(type, "PNG\0", 4)) {
            out->pngChunks.emplace_back(payload, payload + payloadLength);
        }
        // Unknown chunk types are skipped, which keeps older clients working
        // against newer writers.

        pos = payloadStart + payloadLength;
        pos += padTo4(payloadLength);
    }
    if (!haveJson) return fail(error, "no JSON chunk");

    // Validate the accessors now so nothing downstream has to.
    for (size_t i = 0; i < out->accessors.size(); ++i) {
        if (!out->accessorRange(int(i), nullptr, nullptr))
            return fail(error, "accessor " + std::to_string(i) +
                                   " is misaligned or out of range of the BIN chunk");
    }
    for (size_t i = 0; i < out->images.size(); ++i) {
        const int c = out->images[i].chunk;
        if (c >= 0 && size_t(c) >= out->pngChunks.size())
            return fail(error, "image " + std::to_string(i) + " references a missing PNG chunk");
    }
    return true;
}

bool fcxrWrite(const Document& doc, std::vector<uint8_t>* out, std::string* error) {
    if (!out) return fail(error, "null argument");
    const std::string manifest = manifestToJson(doc).dump();

    size_t total = 12;
    auto chunkSize = [](size_t payload) { return 8 + payload + padTo4(payload); };
    total += chunkSize(manifest.size());
    if (!doc.bin.empty()) total += chunkSize(doc.bin.size());
    for (const std::vector<uint8_t>& png : doc.pngChunks) total += chunkSize(png.size());
    if (total > 0xFFFFFFFFull) return fail(error, "FCXR document exceeds 4 GiB");

    out->clear();
    out->reserve(total);
    out->insert(out->end(), {'F', 'C', 'X', 'R'});
    writeU32le(*out, 1);
    writeU32le(*out, uint32_t(total));

    auto emit = [&](const char type[4], const uint8_t* payload, size_t len, uint8_t pad) {
        writeU32le(*out, uint32_t(len));
        out->insert(out->end(), type, type + 4);
        if (len) out->insert(out->end(), payload, payload + len);
        for (size_t i = 0, n = padTo4(len); i < n; ++i) out->push_back(pad);
    };

    emit("JSON", reinterpret_cast<const uint8_t*>(manifest.data()), manifest.size(), 0x20);
    if (!doc.bin.empty()) emit("BIN\0", doc.bin.data(), doc.bin.size(), 0x00);
    for (const std::vector<uint8_t>& png : doc.pngChunks)
        emit("PNG\0", png.data(), png.size(), 0x00);

    if (out->size() != total) return fail(error, "internal FCXR size mismatch");
    return true;
}

}  // namespace fcxr
