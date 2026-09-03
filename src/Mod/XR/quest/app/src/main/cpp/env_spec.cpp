// SPDX-License-Identifier: LGPL-2.1-or-later
#include "env_spec.h"

#include "tessellate.h"

namespace fcxr {
namespace {

Vec3 readVec3(const json::Value& v, Vec3 def) {
    float f[3];
    return json::readFloats(v, f, 3) ? Vec3(f[0], f[1], f[2]) : def;
}
Vec4 readVec4(const json::Value& v, Vec4 def) {
    float f[4];
    return json::readFloats(v, f, 4) ? Vec4(f[0], f[1], f[2], f[3]) : def;
}
Vec2 readVec2(const json::Value& v, Vec2 def) {
    float f[2];
    return json::readFloats(v, f, 2) ? Vec2(f[0], f[1]) : def;
}
Quat readQuat(const json::Value& v, Quat def) {
    float f[4];
    if (!json::readFloats(v, f, 4)) return def;
    const float lenSq = f[0] * f[0] + f[1] * f[1] + f[2] * f[2] + f[3] * f[3];
    if (lenSq < 1e-8f) return def;
    Quat q(f[0], f[1], f[2], f[3]);
    return std::fabs(lenSq - 1.0f) > 1e-4f ? normalize(q) : q;
}

// Loader state: keeps the shape -> mesh index cache used for instancing.
struct Loader {
    EnvSpec* spec;
    std::map<std::string, int> shapeCache;

    // Returns the mesh index for a shape, tessellating it the first time it is
    // seen. Identical shape objects (same JSON, key order included) share one
    // mesh so the renderer can instance them.
    int meshFor(const json::Value& shape) {
        const std::string key = shape.dump();
        auto it = shapeCache.find(key);
        if (it != shapeCache.end()) return it->second;
        MeshData mesh;
        std::string err;
        if (!tessellateShape(shape, &mesh, &err)) {
            spec->warnings.push_back("shape skipped: " + err);
            shapeCache[key] = -1;
            return -1;
        }
        if (mesh.empty()) {
            shapeCache[key] = -1;
            return -1;
        }
        spec->meshes.push_back(std::move(mesh));
        const int index = int(spec->meshes.size()) - 1;
        shapeCache[key] = index;
        return index;
    }

    // Recursively converts a node object; returns its index, or -1.
    int node(const json::Value& v, int depth) {
        if (!v.isObject() || depth > 64) return -1;
        EnvNode n;
        n.name = v["name"].asString();
        n.translation = readVec3(v["translation"], Vec3(0, 0, 0));
        n.rotation = readQuat(v["rotation"], Quat());
        n.scale = readVec3(v["scale"], Vec3(1, 1, 1));
        const json::Value& mat = v["material"];
        n.material = mat.isNumber() ? mat.asInt(-1) : -1;
        if (n.material >= int(spec->materials.size())) {
            spec->warnings.push_back("node '" + n.name + "' references material " +
                                     std::to_string(n.material) + " which does not exist");
            n.material = -1;
        }
        if (v["shape"].isObject()) n.mesh = meshFor(v["shape"]);

        const size_t self = spec->nodes.size();
        spec->nodes.push_back(std::move(n));

        const json::Value& children = v["children"];
        std::vector<int> kids;
        kids.reserve(children.size());
        for (size_t i = 0; i < children.size(); ++i) {
            const int c = node(children[i], depth + 1);
            if (c >= 0) kids.push_back(c);
        }
        spec->nodes[self].children = std::move(kids);
        return int(self);
    }
};

}  // namespace

const EnvAnchor* EnvSpec::anchor(const std::string& anchorName) const {
    for (const EnvAnchor& a : anchors) {
        if (a.name == anchorName) return &a;
    }
    return nullptr;
}

size_t EnvSpec::triangleCount() const {
    size_t total = 0;
    for (const MeshData& m : meshes) total += m.triangleCount();
    return total;
}

bool envSpecParse(const json::Value& root, EnvSpec* out, std::string* error) {
    if (!out) return false;
    if (!root.isObject()) {
        if (error) *error = "environment spec is not a JSON object";
        return false;
    }
    *out = EnvSpec();
    out->id = root["id"].asString();
    out->name = root["name"].asString();
    out->description = root["description"].asString();
    out->version = root["version"].asInt(1);
    out->userScale = root["user_scale"].asFloat(1.0f);
    if (!(out->userScale > 0.0f)) out->userScale = 1.0f;
    out->bounds = readVec3(root["bounds"], Vec3(4, 4, 3));
    out->spawn = readVec3(root["spawn"], Vec3(0, 0, 0));
    out->ambient = readVec3(root["ambient"], Vec3(0.05f, 0.05f, 0.06f));

    if (out->id.empty()) {
        if (error) *error = "environment spec has no id";
        return false;
    }

    const json::Value& lights = root["lights"];
    for (size_t i = 0; i < lights.size(); ++i) {
        const json::Value& lv = lights[i];
        EnvLight l;
        const std::string type = lv["type"].asString();
        if (type == "point") l.type = EnvLight::Type::Point;
        else if (type == "spot") l.type = EnvLight::Type::Spot;
        else l.type = EnvLight::Type::Directional;
        l.direction = normalize(readVec3(lv["direction"], Vec3(0, -1, 0)));
        if (lengthSq(l.direction) < 0.5f) l.direction = Vec3(0, -1, 0);
        l.position = readVec3(lv["position"], Vec3(0, 0, 0));
        l.color = readVec3(lv["color"], Vec3(1, 1, 1));
        l.intensity = lv["intensity"].asFloat(1.0f);
        l.cutoffDeg = clampf(lv["cutoff_deg"].asFloat(45.0f), 1.0f, 89.0f);
        l.range = std::max(0.01f, lv["range"].asFloat(4.0f));
        out->lights.push_back(l);
    }

    const json::Value& materials = root["materials"];
    for (size_t i = 0; i < materials.size(); ++i) {
        const json::Value& mv = materials[i];
        EnvMaterial m;
        m.name = mv["name"].asString();
        m.baseColor = readVec4(mv["base_color"], m.baseColor);
        m.metallic = saturate(mv["metallic"].asFloat(0.0f));
        m.roughness = clampf(mv["roughness"].asFloat(0.6f), 0.02f, 1.0f);
        m.emissive = readVec3(mv["emissive"], Vec3(0, 0, 0));
        m.texture = mv["texture"].asString();
        out->materials.push_back(std::move(m));
    }

    const json::Value& anchors = root["anchors"];
    if (anchors.isObject()) {
        for (const json::Member& kv : anchors.object()) {
            EnvAnchor a;
            a.name = kv.first;
            a.position = readVec3(kv.second["position"], Vec3(0, 0, 0));
            a.rotation = readQuat(kv.second["rotation"], Quat());
            a.size = readVec2(kv.second["size"], Vec2(0, 0));
            out->anchors.push_back(std::move(a));
        }
    }

    Loader loader{out, {}};
    const json::Value& nodes = root["nodes"];
    for (size_t i = 0; i < nodes.size(); ++i) {
        const int idx = loader.node(nodes[i], 0);
        if (idx >= 0) out->roots.push_back(idx);
    }
    return true;
}

bool envSpecLoad(const char* text, size_t length, EnvSpec* out, std::string* error) {
    json::ParseError perr;
    const json::Value root = json::parse(text, length, &perr);
    if (!perr.ok) {
        if (error)
            *error = "environment spec JSON error at line " + std::to_string(perr.line) +
                     " column " + std::to_string(perr.column) + ": " + perr.message;
        return false;
    }
    return envSpecParse(root, out, error);
}

void envSpecFlatten(const EnvSpec& spec, std::vector<EnvDrawItem>* out) {
    if (!out) return;
    out->clear();
    struct Frame { int node; Mat4 xf; int material; };
    std::vector<Frame> stack;
    for (auto it = spec.roots.rbegin(); it != spec.roots.rend(); ++it) {
        if (*it >= 0 && size_t(*it) < spec.nodes.size())
            stack.push_back({*it, spec.nodes[size_t(*it)].localMatrix(), -1});
    }
    while (!stack.empty()) {
        const Frame f = stack.back();
        stack.pop_back();
        const EnvNode& n = spec.nodes[size_t(f.node)];
        // Materials inherit down the tree, which keeps the specs terse.
        const int material = n.material >= 0 ? n.material : f.material;
        if (n.mesh >= 0 && size_t(n.mesh) < spec.meshes.size()) {
            EnvDrawItem item;
            item.mesh = n.mesh;
            item.material = material;
            item.transform = f.xf;
            item.bounds = transformAabb(f.xf, spec.meshes[size_t(n.mesh)].bounds());
            out->push_back(item);
        }
        for (auto it = n.children.rbegin(); it != n.children.rend(); ++it) {
            const int c = *it;
            if (c < 0 || size_t(c) >= spec.nodes.size()) continue;
            stack.push_back({c, f.xf * spec.nodes[size_t(c)].localMatrix(), material});
        }
    }
}

Aabb envSpecBounds(const EnvSpec& spec) {
    std::vector<EnvDrawItem> items;
    envSpecFlatten(spec, &items);
    Aabb b;
    for (const EnvDrawItem& i : items) b.add(i.bounds);
    return b;
}

}  // namespace fcxr
