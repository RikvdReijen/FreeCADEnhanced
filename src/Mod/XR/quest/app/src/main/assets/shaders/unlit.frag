#version 320 es
// SPDX-License-Identifier: LGPL-2.1-or-later
precision highp float;

in vec2 vUv;
in vec4 vColor;

layout(location = 0) out vec4 oColor;

uniform sampler2D uTexture;
uniform int uUseTexture;

void main() {
    vec4 c = vColor;
    if (uUseTexture != 0) c *= texture(uTexture, vUv);
    if (c.a < 0.002) discard;
    oColor = c;
}
