#pragma once
// RmlUi filter and shader parameters turned into what the Metal renderer
// draws: pure functions, no Metal, so each stage can be checked without a
// GPU. Internal to VellumRmlUi::Metal.
//
// Provenance: written from RmlUi's public RenderInterface documentation and
// headers (MIT) and RmlUi's MIT GL3 backend as a behavioural reference (the
// filter parameters and colour matrices, the blur's pass levels and weights,
// and the gradient parameters). The hue-rotate and saturate matrices follow
// the W3C Filter Effects specification. It contains no code from any
// GPL-licensed project.

#include "metal_shaders.hpp"

#include <RmlUi/Core/Types.h>

namespace vellum::rmlui::metal_detail {

enum class FilterKind { Opacity, Blur, DropShadow, ColourMatrix, MaskImage };

struct Filter {
    FilterKind kind = FilterKind::Opacity;
    float value = 1.0f;                       // opacity factor; blur and shadow sigma
    Rml::Vector2f offset{0.0f, 0.0f};         // drop shadow
    float colour[4] = {0.0f, 0.0f, 0.0f, 0.0f}; // drop shadow, premultiplied
    float matrix[16] = {};                    // colour matrix, column-major
};

// A filter by RmlUi name ("opacity", "blur", "drop-shadow", "brightness",
// "contrast", "invert", "grayscale", "sepia", "hue-rotate", "saturate").
// False for any other name.
bool compile_filter(const Rml::String& name, const Rml::Dictionary& parameters, Filter& out);

// A gradient by RmlUi name ("linear-gradient", "radial-gradient",
// "conic-gradient"; "repeating" selects the repeating form). False for any
// other name, or without a colour-stop list. Stops past kMaxStops are dropped.
bool compile_gradient(const Rml::String& name, const Rml::Dictionary& parameters,
                      GradientUniforms& out);

// The number of half-scalings and the per-pass sigma for a blur of `sigma`
// pixels, so one 7-tap pass never needs a sigma above 3.
void blur_parameters(float sigma, int& level, float& pass_sigma);

// Normalised Gaussian weights for a 7-tap pass, centre first.
void blur_weights(float sigma, float out[4]);

} // namespace vellum::rmlui::metal_detail
