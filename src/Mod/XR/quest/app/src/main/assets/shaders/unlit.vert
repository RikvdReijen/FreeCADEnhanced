#version 320 es
// SPDX-License-Identifier: LGPL-2.1-or-later
// UI panels, pointer rays, paint ribbons and the fade overlay.
// When uUseViewProj is 0 the positions are already in clip space, which is how
// the full screen fade quad is drawn.

layout(location = 0) in vec3 aPosition;
layout(location = 1) in vec2 aUv;
layout(location = 2) in vec4 aColor;

uniform mat4 uViewProj;
uniform int uUseViewProj;

out vec2 vUv;
out vec4 vColor;

void main() {
    vUv = aUv;
    vColor = aColor;
    gl_Position = (uUseViewProj != 0) ? uViewProj * vec4(aPosition, 1.0)
                                      : vec4(aPosition, 1.0);
}
