// The Metal renderer's effect compilation, checked without a GPU: which
// filter and shader names it accepts, what it refuses (and so counts as
// unsupported), the blur's pass levels and weights, and a colour matrix's
// values.
#include "metal_effects.hpp"

#include <RmlUi/Core/DecorationTypes.h>
#include <RmlUi/Core/Dictionary.h>

#include <cmath>
#include <cstdio>

using namespace vellum::rmlui::metal_detail;

namespace {

int failures = 0;

void expect(bool condition, const char* what) {
    std::printf("%-64s %s\n", what, condition ? "ok" : "WRONG");
    failures += condition ? 0 : 1;
}

bool near(float a, float b, float tolerance = 1e-4f) {
    return std::abs(a - b) <= tolerance;
}

Rml::Dictionary stops(int count) {
    Rml::ColorStopList list;
    for (int i = 0; i < count; ++i) {
        Rml::ColorStop stop;
        stop.color = Rml::Colourb(255, 0, 0, 255).ToPremultiplied();
        stop.position = Rml::NumericValue(float(i) / float(count > 1 ? count - 1 : 1),
                                          Rml::Unit::NUMBER);
        list.push_back(stop);
    }
    Rml::Dictionary parameters;
    parameters["color_stop_list"] = Rml::Variant(std::move(list));
    return parameters;
}

} // namespace

int main() {
    Filter filter;
    const Rml::Dictionary none;
    for (const char* name : {"opacity", "blur", "drop-shadow", "brightness", "contrast", "invert",
                             "grayscale", "sepia", "hue-rotate", "saturate"})
        expect(compile_filter(name, none, filter), name);
    expect(!compile_filter("glow", none, filter), "an unknown filter name is refused");
    expect(!compile_filter("", none, filter), "an empty filter name is refused");

    // grayscale(1) maps pure red to its luminance on every channel.
    Rml::Dictionary one;
    one["value"] = Rml::Variant(1.0f);
    compile_filter("grayscale", one, filter);
    const float* m = filter.matrix; // column-major: column 0 is red's contribution
    expect(filter.kind == FilterKind::ColourMatrix && near(m[0], 0.2126f) && near(m[1], 0.2126f) &&
               near(m[2], 0.2126f) && near(m[3], 0.0f) && near(m[15], 1.0f),
           "grayscale(1) sends red to (0.2126, 0.2126, 0.2126)");

    GradientUniforms g;
    expect(compile_gradient("linear-gradient", stops(2), g) && g.func == 0 && g.stop_count == 2,
           "linear-gradient with two stops");
    Rml::Dictionary repeating = stops(3);
    repeating["repeating"] = Rml::Variant(true);
    expect(compile_gradient("conic-gradient", repeating, g) && g.func == 5,
           "repeating conic-gradient selects the repeating form");
    expect(compile_gradient("radial-gradient", stops(40), g) && g.stop_count == kMaxStops,
           "stops past the limit are dropped");
    expect(!compile_gradient("linear-gradient", none, g), "a gradient without stops is refused");
    expect(!compile_gradient("linear-gradient", stops(0), g), "a gradient with no stops is refused");
    expect(!compile_gradient("shader", stops(2), g), "application-defined shaders are refused");

    int level = -1;
    float pass_sigma = -1.0f;
    blur_parameters(1.0f, level, pass_sigma);
    expect(level == 0 && near(pass_sigma, 1.0f), "sigma 1: one full-resolution pass");
    blur_parameters(4.0f, level, pass_sigma);
    expect(level == 1 && near(pass_sigma, 2.0f), "sigma 4: one half-scaling, pass sigma 2");
    blur_parameters(1.0e6f, level, pass_sigma);
    expect(level == 10 && near(pass_sigma, 3.0f), "a huge sigma stops at ten half-scalings");
    float weights[4];
    blur_weights(2.0f, weights);
    const float total = weights[0] + 2.0f * (weights[1] + weights[2] + weights[3]);
    expect(near(total, 1.0f) && weights[0] > weights[1] && weights[1] > weights[2],
           "blur weights are normalised and fall off");
    blur_weights(0.0f, weights);
    expect(near(weights[0], 1.0f) && near(weights[1], 0.0f), "sigma 0 passes the image through");
    return failures == 0 ? 0 : 1;
}
