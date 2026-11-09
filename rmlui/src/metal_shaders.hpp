#pragma once
// The Metal Shading Language source for VellumRmlUi::Metal, compiled at
// runtime, and the C++ uniform structs whose layouts must match it. Internal
// to the Metal renderer.
//
// Provenance: written from Apple's Metal Shading Language specification and
// RmlUi's MIT GL3 backend as a behavioural reference (the gradient functions,
// the colour-matrix convention, the 7-tap separable blur and the drop shadow).
// It contains no code from any GPL-licensed project.

#include <cstdint>

namespace vellum::rmlui::metal_detail {

inline constexpr const char* kShaderSource = R"msl(
#include <metal_stdlib>
using namespace metal;

struct VertexIn {
    float2 position [[attribute(0)]];
    float4 colour [[attribute(1)]];
    float2 uv [[attribute(2)]];
};

struct VertexOut {
    float4 position [[position]];
    float4 colour;
    float2 uv;
};

struct Uniforms {
    float4x4 transform;   // projection * RmlUi transform
    float2 translation;
};

vertex VertexOut vellum_rmlui_vertex(VertexIn in [[stage_in]],
                                     constant Uniforms& uniforms [[buffer(1)]]) {
    VertexOut out;
    out.position = uniforms.transform * float4(in.position + uniforms.translation, 0.0, 1.0);
    out.colour = in.colour;
    out.uv = in.uv;
    return out;
}

fragment float4 vellum_rmlui_colour(VertexOut in [[stage_in]]) {
    return in.colour;
}

fragment float4 vellum_rmlui_texture(VertexOut in [[stage_in]],
                                     texture2d<float> texture [[texture(0)]],
                                     sampler texture_sampler [[sampler(0)]]) {
    return texture.sample(texture_sampler, in.uv) * in.colour;
}

// One triangle covering the viewport, generated from the vertex index; uv maps
// the viewport onto a whole layer texture, top row first.
vertex VertexOut vellum_rmlui_fullscreen(uint id [[vertex_id]]) {
    const float2 corner = float2((id << 1) & 2, id & 2);
    VertexOut out;
    out.position = float4(corner * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
    out.colour = float4(1.0);
    out.uv = corner;
    return out;
}

fragment float4 vellum_rmlui_passthrough(VertexOut in [[stage_in]],
                                         texture2d<float> texture [[texture(0)]]) {
    constexpr sampler nearest(filter::nearest, address::clamp_to_edge);
    return texture.sample(nearest, in.uv);
}

// Post-processing: one triangle covering the viewport (the destination
// rectangle), with uv running from uv_min to uv_max across it.
struct RectUniforms {
    float2 uv_min;
    float2 uv_max;
};

vertex VertexOut vellum_rmlui_rect(uint id [[vertex_id]],
                                   constant RectUniforms& rect [[buffer(0)]]) {
    const float2 corner = float2((id << 1) & 2, id & 2);
    VertexOut out;
    out.position = float4(corner * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
    out.colour = float4(1.0);
    out.uv = mix(rect.uv_min, rect.uv_max, corner);
    return out;
}

fragment float4 vellum_rmlui_linear_copy(VertexOut in [[stage_in]],
                                         texture2d<float> texture [[texture(0)]]) {
    constexpr sampler linear(filter::linear, address::clamp_to_edge);
    return texture.sample(linear, in.uv);
}

fragment float4 vellum_rmlui_transparent(VertexOut in [[stage_in]]) {
    return float4(0.0);
}

fragment float4 vellum_rmlui_scale(VertexOut in [[stage_in]],
                                   texture2d<float> texture [[texture(0)]],
                                   constant float& factor [[buffer(0)]]) {
    constexpr sampler nearest(filter::nearest, address::clamp_to_edge);
    return texture.sample(nearest, in.uv) * factor;
}

// An RGB colour matrix applied in premultiplied space: the fourth column is
// the constant term, scaled by alpha; alpha is unchanged.
fragment float4 vellum_rmlui_colour_matrix(VertexOut in [[stage_in]],
                                           texture2d<float> texture [[texture(0)]],
                                           constant float4x4& matrix [[buffer(0)]]) {
    constexpr sampler nearest(filter::nearest, address::clamp_to_edge);
    const float4 colour = texture.sample(nearest, in.uv);
    return float4((matrix * colour).rgb, colour.a);
}

struct BlurUniforms {
    float2 texel_offset;
    float2 uv_min;
    float2 uv_max;
    float2 padding;
    float4 weights;   // centre first
};

// One direction of a 7-tap Gaussian; taps outside [uv_min, uv_max] count as
// transparent, so the blur does not read past its region.
fragment float4 vellum_rmlui_blur(VertexOut in [[stage_in]],
                                  texture2d<float> texture [[texture(0)]],
                                  constant BlurUniforms& blur [[buffer(0)]]) {
    constexpr sampler linear(filter::linear, address::clamp_to_edge);
    float4 colour = float4(0.0);
    for (int i = 0; i < 7; ++i) {
        const float2 uv = in.uv - float(i - 3) * blur.texel_offset;
        const float2 inside = step(blur.uv_min, uv) * step(uv, blur.uv_max);
        colour += texture.sample(linear, uv) * inside.x * inside.y * blur.weights[abs(i - 3)];
    }
    return colour;
}

struct ShadowUniforms {
    float2 uv_min;
    float2 uv_max;
    float2 uv_offset;
    float2 padding;
    float4 colour;    // premultiplied
};

fragment float4 vellum_rmlui_drop_shadow(VertexOut in [[stage_in]],
                                         texture2d<float> texture [[texture(0)]],
                                         constant ShadowUniforms& shadow [[buffer(0)]]) {
    constexpr sampler nearest(filter::nearest, address::clamp_to_edge);
    const float2 uv = in.uv - shadow.uv_offset;
    const float2 inside = step(shadow.uv_min, uv) * step(uv, shadow.uv_max);
    return texture.sample(nearest, uv).a * inside.x * inside.y * shadow.colour;
}

fragment float4 vellum_rmlui_mask(VertexOut in [[stage_in]],
                                  texture2d<float> texture [[texture(0)]],
                                  texture2d<float> mask [[texture(1)]]) {
    constexpr sampler nearest(filter::nearest, address::clamp_to_edge);
    return texture.sample(nearest, in.uv) * mask.sample(nearest, in.uv).a;
}

// Gradients over geometry whose texture coordinates are element positions.
// func: 0 linear, 1 radial, 2 conic, 3-5 their repeating forms.
struct GradientUniforms {
    int func;
    int stop_count;
    float2 p;         // linear: start; radial and conic: centre
    float2 v;         // linear: start to end; radial: inverse radii; conic: unit angle vector
    float2 padding;
    float4 colours[16];   // premultiplied
    float positions[16];  // 0 at p, 1 at the end
};

fragment float4 vellum_rmlui_gradient(VertexOut in [[stage_in]],
                                      constant GradientUniforms& g [[buffer(0)]]) {
    const float2 d = in.uv - g.p;
    float t = 0.0;
    if (g.func == 0 || g.func == 3) {
        t = dot(g.v, d) / dot(g.v, g.v);
    } else if (g.func == 1 || g.func == 4) {
        t = length(g.v * d);
    } else {
        const float2x2 rotate = float2x2(float2(g.v.x, -g.v.y), float2(g.v.y, g.v.x));
        const float2 r = rotate * d;
        t = 0.5 + atan2(-r.x, r.y) / (2.0 * M_PI_F);
    }
    if (g.func >= 3) {
        const float t0 = g.positions[0];
        const float span = g.positions[g.stop_count - 1] - t0;
        if (span > 0.0)
            t = t0 + (t - t0) - span * floor((t - t0) / span);
    }
    float4 colour = g.colours[0];
    for (int i = 1; i < g.stop_count; ++i) {
        const float a = g.positions[i - 1];
        const float b = g.positions[i];
        // Equal positions are a hard stop; smoothstep is undefined there.
        const float w = b > a ? smoothstep(a, b, t) : (t >= b ? 1.0 : 0.0);
        colour = mix(colour, g.colours[i], w);
    }
    return in.colour * colour;
}
)msl";

struct RectUniforms {
    float uv_min[2];
    float uv_max[2];
};

struct BlurUniforms {
    float texel_offset[2];
    float uv_min[2];
    float uv_max[2];
    float padding[2];
    float weights[4];
};

struct ShadowUniforms {
    float uv_min[2];
    float uv_max[2];
    float uv_offset[2];
    float padding[2];
    float colour[4];
};

constexpr int kMaxStops = 16;

struct GradientUniforms {
    std::int32_t func;
    std::int32_t stop_count;
    float p[2];
    float v[2];
    float padding[2];
    float colours[kMaxStops][4];
    float positions[kMaxStops];
};
static_assert(sizeof(GradientUniforms) == 352, "GradientUniforms must match the MSL layout");

} // namespace vellum::rmlui::metal_detail
