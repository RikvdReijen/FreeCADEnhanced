#version 320 es
// SPDX-License-Identifier: LGPL-2.1-or-later
// Forward Cook-Torrance shading for the §1 / §2 material model
// (base_color, metallic, roughness, emissive, base_color_texture).
//
// There are no shadow maps: environments are lit by a handful of analytic
// lights plus a hemispherical ambient term that stands in for image based
// lighting. That is cheap enough to hold 72 Hz stereo on a Quest 3 and looks
// right for the machine interiors these environments describe.

precision highp float;

in vec3 vWorldPos;
in vec3 vNormal;
in vec2 vUv;

layout(location = 0) out vec4 oColor;

const int kMaxLights = 8;

uniform vec4 uBaseColor;                  // linear RGBA
uniform vec4 uMaterial;                   // x metallic, y roughness, z useTexture, w unused
uniform vec3 uEmissive;
uniform vec3 uAmbient;
uniform vec3 uEyePos;
uniform int uLightCount;
uniform vec4 uLightPosType[kMaxLights];   // xyz position, w type (0 dir, 1 point, 2 spot)
uniform vec4 uLightDirRange[kMaxLights];  // xyz direction (pointing away from the light), w range
uniform vec4 uLightColor[kMaxLights];     // rgb colour * intensity, w cos(cutoff)
uniform sampler2D uBaseTexture;
uniform float uAlpha;

const float kPi = 3.14159265359;

float distributionGGX(float nDotH, float roughness) {
    float a = roughness * roughness;
    float a2 = a * a;
    float d = nDotH * nDotH * (a2 - 1.0) + 1.0;
    return a2 / max(kPi * d * d, 1e-7);
}

float geometrySmith(float nDotV, float nDotL, float roughness) {
    // Schlick-GGX with the direct lighting k.
    float r = roughness + 1.0;
    float k = (r * r) / 8.0;
    float gv = nDotV / (nDotV * (1.0 - k) + k);
    float gl = nDotL / (nDotL * (1.0 - k) + k);
    return gv * gl;
}

vec3 fresnelSchlick(float cosTheta, vec3 f0) {
    return f0 + (1.0 - f0) * pow(clamp(1.0 - cosTheta, 0.0, 1.0), 5.0);
}

vec3 fresnelSchlickRoughness(float cosTheta, vec3 f0, float roughness) {
    vec3 fr = max(vec3(1.0 - roughness), f0);
    return f0 + (fr - f0) * pow(clamp(1.0 - cosTheta, 0.0, 1.0), 5.0);
}

// Narkowicz ACES fit, applied before the sRGB framebuffer encodes the result.
vec3 tonemap(vec3 x) {
    const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}

void main() {
    vec4 base = uBaseColor;
    if (uMaterial.z > 0.5) {
        base *= texture(uBaseTexture, vUv);
    }
    float metallic = clamp(uMaterial.x, 0.0, 1.0);
    float roughness = clamp(uMaterial.y, 0.03, 1.0);

    vec3 n = normalize(vNormal);
    vec3 v = normalize(uEyePos - vWorldPos);
    // Double sided surfaces (thin sheets in the environments) need the normal
    // flipped towards the viewer or they go black from behind.
    if (!gl_FrontFacing) n = -n;
    float nDotV = max(dot(n, v), 1e-4);

    vec3 albedo = base.rgb * (1.0 - metallic);
    vec3 f0 = mix(vec3(0.04), base.rgb, metallic);

    vec3 lit = vec3(0.0);
    for (int i = 0; i < kMaxLights; ++i) {
        if (i >= uLightCount) break;
        float type = uLightPosType[i].w;
        vec3 l;
        float attenuation = 1.0;
        if (type < 0.5) {
            l = normalize(-uLightDirRange[i].xyz);
        } else {
            vec3 toLight = uLightPosType[i].xyz - vWorldPos;
            float dist = length(toLight);
            l = toLight / max(dist, 1e-5);
            float range = max(uLightDirRange[i].w, 1e-3);
            // Inverse square with a smooth cutoff at `range`.
            float falloff = clamp(1.0 - pow(dist / range, 4.0), 0.0, 1.0);
            attenuation = falloff * falloff / (dist * dist + 1e-4);
            if (type > 1.5) {
                float spot = dot(normalize(uLightDirRange[i].xyz), -l);
                float cutoff = uLightColor[i].w;
                attenuation *= smoothstep(cutoff, mix(cutoff, 1.0, 0.25), spot);
            }
        }
        float nDotL = max(dot(n, l), 0.0);
        if (nDotL <= 0.0 || attenuation <= 0.0) continue;

        vec3 h = normalize(v + l);
        float nDotH = max(dot(n, h), 0.0);
        float vDotH = max(dot(v, h), 0.0);

        float ndf = distributionGGX(nDotH, roughness);
        float g = geometrySmith(nDotV, nDotL, roughness);
        vec3 f = fresnelSchlick(vDotH, f0);

        vec3 specular = (ndf * g * f) / max(4.0 * nDotV * nDotL, 1e-5);
        vec3 kd = (vec3(1.0) - f);
        lit += (kd * albedo / kPi + specular) * uLightColor[i].rgb * nDotL * attenuation;
    }

    // Hemispherical ambient standing in for an irradiance probe: brighter from
    // above, tinted slightly cool from below, plus a rough specular lobe.
    float up = n.y * 0.5 + 0.5;
    vec3 ambientDiffuse = uAmbient * mix(vec3(0.55, 0.57, 0.62), vec3(1.25), up) * albedo;
    vec3 fAmbient = fresnelSchlickRoughness(nDotV, f0, roughness);
    vec3 ambientSpecular = uAmbient * fAmbient * (1.0 - roughness * 0.75) * 1.5;

    vec3 color = lit + ambientDiffuse + ambientSpecular + uEmissive;
    oColor = vec4(tonemap(color), base.a * uAlpha);
}
