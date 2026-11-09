// Provenance: written from RmlUi's public RenderInterface documentation and
// headers (MIT) and RmlUi's MIT GL3 backend as a behavioural reference (the
// filter parameters and colour matrices, the blur's pass levels and weights,
// and the gradient parameters). The hue-rotate and saturate matrices follow
// the W3C Filter Effects specification. It contains no code from any
// GPL-licensed project.

#include "metal_effects.hpp"

#include <RmlUi/Core/DecorationTypes.h>
#include <RmlUi/Core/Dictionary.h>
#include <RmlUi/Core/Math.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <type_traits>

namespace vellum::rmlui::metal_detail {

namespace {

// Column-major floats, whichever layout RmlUi was built with.
void column_major(const Rml::Matrix4f& matrix, float out[16]) {
    if constexpr (std::is_same_v<Rml::Matrix4f, Rml::RowMajorMatrix4f>)
        std::memcpy(out, matrix.Transpose().data(), sizeof(float) * 16);
    else
        std::memcpy(out, matrix.data(), sizeof(float) * 16);
}

} // namespace

void blur_parameters(float sigma, int& level, float& pass_sigma) {
    constexpr int kMaxLevel = 10;
    constexpr float kMaxPassSigma = 3.0f;
    int n = static_cast<int>(sigma * (2.0f / kMaxPassSigma));
    int log2 = 0;
    while (n > 1) {
        n >>= 1;
        ++log2;
    }
    level = std::clamp(log2, 0, kMaxLevel);
    pass_sigma = std::clamp(sigma / float(1 << level), 0.0f, kMaxPassSigma);
}

void blur_weights(float sigma, float out[4]) {
    float total = 0.0f;
    for (int i = 0; i < 4; ++i) {
        out[i] = std::abs(sigma) < 0.1f
                     ? float(i == 0)
                     : std::exp(-float(i * i) / (2.0f * sigma * sigma)) /
                           (std::sqrt(2.0f * Rml::Math::RMLUI_PI) * sigma);
        total += (i == 0 ? 1.0f : 2.0f) * out[i];
    }
    for (int i = 0; i < 4; ++i)
        out[i] /= total;
}

bool compile_filter(const Rml::String& name, const Rml::Dictionary& parameters, Filter& out) {
    out = Filter{};
    const auto matrix = [&](const Rml::Matrix4f& m) {
        out.kind = FilterKind::ColourMatrix;
        column_major(m, out.matrix);
    };
    const float value = Rml::Get(parameters, "value", 1.0f);
    if (name == "opacity") {
        out.kind = FilterKind::Opacity;
        out.value = value;
    } else if (name == "blur") {
        out.kind = FilterKind::Blur;
        out.value = Rml::Get(parameters, "sigma", 1.0f);
    } else if (name == "drop-shadow") {
        out.kind = FilterKind::DropShadow;
        out.value = Rml::Get(parameters, "sigma", 0.0f);
        out.offset = Rml::Get(parameters, "offset", Rml::Vector2f(0.0f));
        const Rml::ColourbPremultiplied colour =
            Rml::Get(parameters, "color", Rml::Colourb()).ToPremultiplied();
        for (int i = 0; i < 4; ++i)
            out.colour[i] = float(colour[i]) / 255.0f;
    } else if (name == "brightness") {
        matrix(Rml::Matrix4f::Diag(value, value, value, 1.0f));
    } else if (name == "contrast") {
        const float grey = 0.5f - 0.5f * value;
        Rml::Matrix4f m = Rml::Matrix4f::Diag(value, value, value, 1.0f);
        m.SetColumn(3, Rml::Vector4f(grey, grey, grey, 1.0f));
        matrix(m);
    } else if (name == "invert") {
        const float amount = std::clamp(value, 0.0f, 1.0f);
        const float scale = 1.0f - 2.0f * amount;
        Rml::Matrix4f m = Rml::Matrix4f::Diag(scale, scale, scale, 1.0f);
        m.SetColumn(3, Rml::Vector4f(amount, amount, amount, 1.0f));
        matrix(m);
    } else if (name == "grayscale") {
        const float keep = 1.0f - value;
        const Rml::Vector3f g = value * Rml::Vector3f(0.2126f, 0.7152f, 0.0722f);
        matrix(Rml::Matrix4f::FromRows({g.x + keep, g.y, g.z, 0.0f}, {g.x, g.y + keep, g.z, 0.0f},
                                       {g.x, g.y, g.z + keep, 0.0f}, {0.0f, 0.0f, 0.0f, 1.0f}));
    } else if (name == "sepia") {
        const float keep = 1.0f - value;
        const Rml::Vector3f r = value * Rml::Vector3f(0.393f, 0.769f, 0.189f);
        const Rml::Vector3f g = value * Rml::Vector3f(0.349f, 0.686f, 0.168f);
        const Rml::Vector3f b = value * Rml::Vector3f(0.272f, 0.534f, 0.131f);
        matrix(Rml::Matrix4f::FromRows({r.x + keep, r.y, r.z, 0.0f}, {g.x, g.y + keep, g.z, 0.0f},
                                       {b.x, b.y, b.z + keep, 0.0f}, {0.0f, 0.0f, 0.0f, 1.0f}));
    } else if (name == "hue-rotate") {
        // https://www.w3.org/TR/filter-effects-1/#attr-valuedef-type-huerotate
        const float sn = std::sin(value);
        const float cs = std::cos(value);
        matrix(Rml::Matrix4f::FromRows(
            {0.213f + 0.787f * cs - 0.213f * sn, 0.715f - 0.715f * cs - 0.715f * sn,
             0.072f - 0.072f * cs + 0.928f * sn, 0.0f},
            {0.213f - 0.213f * cs + 0.143f * sn, 0.715f + 0.285f * cs + 0.140f * sn,
             0.072f - 0.072f * cs - 0.283f * sn, 0.0f},
            {0.213f - 0.213f * cs - 0.787f * sn, 0.715f - 0.715f * cs + 0.715f * sn,
             0.072f + 0.928f * cs + 0.072f * sn, 0.0f},
            {0.0f, 0.0f, 0.0f, 1.0f}));
    } else if (name == "saturate") {
        matrix(Rml::Matrix4f::FromRows(
            {0.213f + 0.787f * value, 0.715f - 0.715f * value, 0.072f - 0.072f * value, 0.0f},
            {0.213f - 0.213f * value, 0.715f + 0.285f * value, 0.072f - 0.072f * value, 0.0f},
            {0.213f - 0.213f * value, 0.715f - 0.715f * value, 0.072f + 0.928f * value, 0.0f},
            {0.0f, 0.0f, 0.0f, 1.0f}));
    } else {
        return false;
    }
    return true;
}

bool compile_gradient(const Rml::String& name, const Rml::Dictionary& parameters,
                      GradientUniforms& out) {
    out = GradientUniforms{};
    const bool repeating = Rml::Get(parameters, "repeating", false);
    if (name == "linear-gradient") {
        out.func = repeating ? 3 : 0;
        const auto p0 = Rml::Get(parameters, "p0", Rml::Vector2f(0.0f));
        const auto p1 = Rml::Get(parameters, "p1", Rml::Vector2f(0.0f));
        out.p[0] = p0.x;
        out.p[1] = p0.y;
        out.v[0] = p1.x - p0.x;
        out.v[1] = p1.y - p0.y;
    } else if (name == "radial-gradient") {
        out.func = repeating ? 4 : 1;
        const auto centre = Rml::Get(parameters, "center", Rml::Vector2f(0.0f));
        const auto radius = Rml::Get(parameters, "radius", Rml::Vector2f(1.0f));
        out.p[0] = centre.x;
        out.p[1] = centre.y;
        out.v[0] = 1.0f / radius.x;
        out.v[1] = 1.0f / radius.y;
    } else if (name == "conic-gradient") {
        out.func = repeating ? 5 : 2;
        const auto centre = Rml::Get(parameters, "center", Rml::Vector2f(0.0f));
        const float angle = Rml::Get(parameters, "angle", 0.0f);
        out.p[0] = centre.x;
        out.p[1] = centre.y;
        out.v[0] = std::cos(angle);
        out.v[1] = std::sin(angle);
    } else {
        return false;
    }
    const auto stops = parameters.find("color_stop_list");
    if (stops == parameters.end() || stops->second.GetType() != Rml::Variant::COLORSTOPLIST)
        return false;
    const auto& list = stops->second.GetReference<Rml::ColorStopList>();
    out.stop_count = static_cast<std::int32_t>(std::min<std::size_t>(list.size(), kMaxStops));
    if (out.stop_count == 0)
        return false;
    for (int i = 0; i < out.stop_count; ++i) {
        const Rml::ColorStop& stop = list[static_cast<std::size_t>(i)];
        out.positions[i] = stop.position.number;
        for (int c = 0; c < 4; ++c)
            out.colours[i][c] = float(stop.color[c]) / 255.0f;
    }
    return true;
}

} // namespace vellum::rmlui::metal_detail
