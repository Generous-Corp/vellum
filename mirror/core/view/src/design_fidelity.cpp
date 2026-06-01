// core/view/src/design_fidelity.cpp
#include <pulp/view/design_fidelity.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <sstream>

namespace pulp::view {
namespace {

// Read a numeric node attribute (0 if absent / unparsable).
float attr_f(const IRNode& n, const char* key) {
    auto it = n.attributes.find(key);
    return it != n.attributes.end() ? std::strtof(it->second.c_str(), nullptr) : 0.0f;
}

bool is_bleed_sprite(const IRNode& n) {
    return n.style.render_bounds.has_value() ||
           (n.attributes.count("asset_bleed") && n.attributes.at("asset_bleed") == "1");
}

}  // namespace

std::optional<FidelityIssue> check_image_sizing_fidelity(const FidelityContext& ctx) {
    const IRNode& node = ctx.node;
    if (!is_bleed_sprite(node)) return std::nullopt;  // ordinary images fill their box

    const float png_w = attr_f(node, "png_natural_w");
    const float png_h = attr_f(node, "png_natural_h");
    if (png_w <= 0.0f || png_h <= 0.0f) {
        return FidelityIssue{ctx.node_id, node.name, "aspect-unverified",
            "bleed sprite has no source PNG dimensions; aspect could not be verified"};
    }
    if (ctx.emitted_w <= 0.0f || ctx.emitted_h <= 0.0f) return std::nullopt;

    const float png_aspect = png_w / png_h;
    const float emitted_aspect = ctx.emitted_w / ctx.emitted_h;
    const float rel = std::fabs(emitted_aspect - png_aspect) / png_aspect;
    if (rel > 0.05f) {
        std::ostringstream d;
        d << "emitted aspect " << emitted_aspect << " diverges from source PNG aspect "
          << png_aspect << " (" << static_cast<int>(rel * 100.0f + 0.5f)
          << "% off) — sprite skewed";
        return FidelityIssue{ctx.node_id, node.name, "skew", d.str()};
    }
    return std::nullopt;
}

std::optional<FidelityIssue> check_gross_size_divergence(const FidelityContext& ctx) {
    const IRNode& node = ctx.node;
    if (node.layout.width_mode  != SizingMode::fixed) return std::nullopt;
    if (node.layout.height_mode != SizingMode::fixed) return std::nullopt;
    if (node.style.position.has_value() && *node.style.position == "absolute")
        return std::nullopt;
    if (node.layout.display.has_value() && *node.layout.display == "none")
        return std::nullopt;

    const float src_w = node.style.width.value_or(0.0f);
    const float src_h = node.style.height.value_or(0.0f);
    if (src_w <= 0.0f || src_h <= 0.0f) return std::nullopt;
    if (ctx.emitted_w <= 0.0f || ctx.emitted_h <= 0.0f) return std::nullopt;

    auto ratio = [](float a, float b) { return std::max(a, b) / std::min(a, b); };
    const float rw = ratio(ctx.emitted_w, src_w);
    const float rh = ratio(ctx.emitted_h, src_h);
    constexpr float kMaxRatio = 3.0f;
    if (rw > kMaxRatio || rh > kMaxRatio) {
        std::ostringstream d;
        d << "fixed-sized node emitted box diverges from source: "
          << "W " << rw << "x (source " << src_w << " emitted " << ctx.emitted_w << "px), "
          << "H " << rh << "x (source " << src_h << " emitted " << ctx.emitted_h << "px)";
        return FidelityIssue{ctx.node_id, node.name, "gross-size", d.str()};
    }
    return std::nullopt;
}

std::optional<FidelityIssue> check_widget_intrinsic_size(const FidelityContext& ctx) {
    const IRNode& node = ctx.node;
    if (node.audio_widget == AudioWidgetType::none) return std::nullopt;

    // Source intrinsic size: an explicit child-shape size (shape_width/height,
    // stamped by extract_widget_shape_dims) wins, else the node's own box.
    const float attr_w = attr_f(node, "shape_width");
    const float attr_h = attr_f(node, "shape_height");
    const float src_w = attr_w > 0.0f ? attr_w : node.style.width.value_or(0.0f);
    const float src_h = attr_h > 0.0f ? attr_h : node.style.height.value_or(0.0f);
    if (src_w <= 0.0f || src_h <= 0.0f) return std::nullopt;  // heuristic detect, nothing to verify
    if (ctx.emitted_w <= 0.0f || ctx.emitted_h <= 0.0f) return std::nullopt;

    // Native usable minimums codegen clamps up to (kept in sync with the kMin*
    // floors in design_codegen.cpp). A source below these is legitimately
    // enlarged, so that divergence is informational, not a real finding.
    struct NMin { float w, h; };
    auto native_min = [](AudioWidgetType t) -> NMin {
        switch (t) {
            case AudioWidgetType::knob:     return {56.0f, 56.0f};
            case AudioWidgetType::fader:    return {40.0f, 80.0f};
            case AudioWidgetType::meter:    return {20.0f, 80.0f};
            case AudioWidgetType::xy_pad:   return {80.0f, 80.0f};
            case AudioWidgetType::waveform: return {200.0f, 80.0f};
            case AudioWidgetType::spectrum: return {200.0f, 80.0f};
            default:                        return {0.0f, 0.0f};
        }
    };
    const NMin nmin = native_min(node.audio_widget);

    auto ratio = [](float a, float b) { return std::max(a, b) / std::min(a, b); };
    const float rw = ratio(ctx.emitted_w, src_w);
    const float rh = ratio(ctx.emitted_h, src_h);
    constexpr float kMaxRatio = 1.5f;
    if (rw <= kMaxRatio && rh <= kMaxRatio) return std::nullopt;

    // A diverging axis is "expected" only if the source was below the native
    // minimum on that axis (codegen clamps it up) — then warn, don't fail.
    const bool w_div = rw > kMaxRatio, h_div = rh > kMaxRatio;
    const bool w_clamp = src_w < nmin.w && ctx.emitted_w >= src_w;
    const bool h_clamp = src_h < nmin.h && ctx.emitted_h >= src_h;
    const bool only_clamp = (!w_div || w_clamp) && (!h_div || h_clamp);

    std::ostringstream d;
    d << "widget emitted box diverges from source intrinsic: "
      << "W " << rw << "x (src " << src_w << " emit " << ctx.emitted_w << "px), "
      << "H " << rh << "x (src " << src_h << " emit " << ctx.emitted_h << "px)";
    if (only_clamp) {
        d << " — below native minimum, clamped up";
        return FidelityIssue{ctx.node_id, node.name, "widget-undersized", d.str()};
    }
    return FidelityIssue{ctx.node_id, node.name, "widget-size", d.str()};
}

std::optional<FidelityIssue> check_text_vertical_centering(const FidelityContext& ctx) {
    const IRNode& node = ctx.node;
    const float box_h = ctx.emitted_h;            // codegen passes the emitted label box height
    const float font_h = node.style.font_size.value_or(0.0f);
    if (box_h <= 0.0f || font_h <= 0.0f) return std::nullopt;
    const float line_h = node.style.line_height.value_or(font_h * 1.2f);

    // Multi-line wraps (no single-line center to verify); a box with no slack
    // beyond one line has nothing to center within — both self-skip.
    if (box_h > line_h * 1.8f) return std::nullopt;
    if (box_h <= line_h * 1.15f) return std::nullopt;

    // The text call-site stamps whether codegen emitted vertical centering.
    auto it = node.attributes.find("_emitted_vertical_align");
    const std::string va = it != node.attributes.end() ? it->second : "";
    if (va == "center") return std::nullopt;      // centered as expected

    std::ostringstream d;
    d << "single-line text in a " << box_h << "px slot (line ~" << line_h
      << "px) is top-aligned, not vertically centered";
    return FidelityIssue{ctx.node_id, node.name, "text-vcenter", d.str()};
}

// ── Registry: the single place to add a new invariant ────────────────────────
// Each entry pairs a check with the element kind it applies to. A check runs
// ONLY for its element (an image's emitted box legitimately differs from its
// style box, so the container gross-size test must not see images, etc.).
// Adding an invariant = one row here + its function above.
namespace {
using CheckFn = std::optional<FidelityIssue> (*)(const FidelityContext&);
struct RegisteredCheck { FidelityElement applies_to; CheckFn fn; };
constexpr std::array<RegisteredCheck, 4> kChecks = {{
    {FidelityElement::image,     &check_image_sizing_fidelity},
    {FidelityElement::container, &check_gross_size_divergence},
    {FidelityElement::widget,    &check_widget_intrinsic_size},
    {FidelityElement::text,      &check_text_vertical_centering},
}};
}  // namespace

void run_fidelity_checks(const FidelityContext& ctx, std::vector<FidelityIssue>& sink) {
    for (const auto& c : kChecks)
        if (c.applies_to == ctx.element)
            if (auto issue = c.fn(ctx)) sink.push_back(std::move(*issue));
}

}  // namespace pulp::view
