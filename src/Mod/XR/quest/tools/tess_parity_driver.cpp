// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Host-side parity driver for the environment tessellator.
//
// The desktop workbench tessellates environment specs in Python
// (src/Mod/XR/xrenv/spec.py) and the headset does it again in C++
// (app/src/main/cpp/tessellate.cpp).  Both read the same declarative spec, so
// the two must agree or a machine looks different depending on where you view
// it -- and the ways they can disagree (winding, normal direction, a primitive
// pointing down the wrong axis) are exactly the ways that are hard to spot by
// reading either implementation.
//
// This program prints a digest per shape: vertex count, triangle count, the
// sum of the indices, and the summed coordinates of the positions and normals.
// tools/check_tessellator_parity.py builds it, runs the Python reference over
// the same spec, and compares.  Build it by hand with:
//
//   g++ -std=c++17 -I app/src/main/cpp -o driver \
//       quest/tools/tess_parity_driver.cpp \
//       app/src/main/cpp/{tessellate,math3d,json,mesh_data,text_font}.cpp
//
// Values whose magnitude rounds to zero are clamped, because a sum that lands
// on -1e-17 prints as "-0.000" on one side and "0.000" on the other without
// any geometric difference at all.

#include <cstdio>
#include <cstdint>
#include <string>
#include <fstream>
#include <sstream>
#include "json.h"
#include "tessellate.h"
#include "mesh_data.h"

using namespace fcxr;

static void walk(const json::Value& node, int& index) {
    if (node.isObject()) {
        const json::Value& shape = node.find("shape");
        if (shape.isObject()) {
            MeshData mesh;
            std::string error;
            if (!tessellateShape(shape, &mesh, &error)) {
                printf("%d ERROR %s\n", index, error.c_str());
            } else {
                double px = 0, py = 0, pz = 0, nx = 0, ny = 0, nz = 0;
                for (size_t i = 0; i < mesh.positions.size(); ++i) {
                    px += mesh.positions[i].x;
                    py += mesh.positions[i].y;
                    pz += mesh.positions[i].z;
                }
                for (size_t i = 0; i < mesh.normals.size(); ++i) {
                    nx += mesh.normals[i].x;
                    ny += mesh.normals[i].y;
                    nz += mesh.normals[i].z;
                }
                unsigned long long isum = 0;
                for (size_t i = 0; i < mesh.indices.size(); ++i) isum += mesh.indices[i];
                const double eps = 5e-4;
                if (px > -eps && px < eps) px = 0; if (py > -eps && py < eps) py = 0;
                if (pz > -eps && pz < eps) pz = 0; if (nx > -eps && nx < eps) nx = 0;
                if (ny > -eps && ny < eps) ny = 0; if (nz > -eps && nz < eps) nz = 0;
                printf("%d %zu %zu %llu %.3f %.3f %.3f %.3f %.3f %.3f\n",
                       index, mesh.positions.size(), mesh.indices.size() / 3,
                       isum, px, py, pz, nx, ny, nz);
            }
            ++index;
        }
        const json::Value& children = node.find("children");
        if (children.isArray())
            for (size_t i = 0; i < children.size(); ++i) walk(children[i], index);
    }
}

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: driver spec.json\n"); return 2; }
    std::ifstream in(argv[1]);
    std::stringstream buffer; buffer << in.rdbuf();
    std::string text = buffer.str();
    json::ParseError err;
    json::Value root = json::parse(text, &err);
    if (root.isNull()) { fprintf(stderr, "parse failed\n"); return 1; }
    const json::Value& nodes = root.find("nodes");
    if (!nodes.isArray()) { fprintf(stderr, "no nodes\n"); return 1; }
    int index = 0;
    for (size_t i = 0; i < nodes.size(); ++i) walk(nodes[i], index);
    fprintf(stderr, "shapes: %d\n", index);
    return 0;
}
