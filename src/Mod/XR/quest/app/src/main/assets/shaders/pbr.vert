#version 320 es
// SPDX-License-Identifier: LGPL-2.1-or-later
// Instanced vertex stage for environment and document geometry.
// Instance attributes carry the model matrix (locations 3..6) and the
// inverse-transpose normal matrix (locations 7..9) so non-uniform scale in an
// environment spec still shades correctly.

layout(location = 0) in vec3 aPosition;
layout(location = 1) in vec3 aNormal;
layout(location = 2) in vec2 aUv;
layout(location = 3) in vec4 iModel0;
layout(location = 4) in vec4 iModel1;
layout(location = 5) in vec4 iModel2;
layout(location = 6) in vec4 iModel3;
layout(location = 7) in vec3 iNormal0;
layout(location = 8) in vec3 iNormal1;
layout(location = 9) in vec3 iNormal2;

uniform mat4 uViewProj;

out vec3 vWorldPos;
out vec3 vNormal;
out vec2 vUv;

void main() {
    mat4 model = mat4(iModel0, iModel1, iModel2, iModel3);
    vec4 world = model * vec4(aPosition, 1.0);
    vWorldPos = world.xyz;
    vNormal = normalize(mat3(iNormal0, iNormal1, iNormal2) * aNormal);
    vUv = aUv;
    gl_Position = uViewProj * world;
}
