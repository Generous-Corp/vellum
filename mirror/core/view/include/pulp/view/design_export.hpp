#pragma once

/// @file design_export.hpp
/// Export design tokens to multiple formats: JSON, CSS variables, C++ headers,
/// shader uniforms, OKLCH CSS.

#include <pulp/view/theme.hpp>
#include <string>
#include <unordered_map>

namespace pulp::view {

/// Export a Theme's design tokens to various output formats.
///
/// Design tokens are the structured data behind a plugin's visual identity:
/// colors, dimensions, typography, widget geometry, and rendering styles.
///
/// @code
/// Theme theme = load_theme("dark.json");
/// auto json = DesignExport::to_json(theme);
/// auto css = DesignExport::to_css(theme);
/// auto cpp = DesignExport::to_cpp_header(theme, "my_theme");
/// @endcode
class DesignExport {
public:
    /// Export to JSON (round-trippable with Theme::from_json).
    static std::string to_json(const Theme& theme);

    /// Export to CSS custom properties.
    /// @code
    /// :root {
    ///   --pulp-background: #1a1a2e;
    ///   --pulp-accent: #e94560;
    ///   --pulp-knob-size: 60px;
    /// }
    /// @endcode
    static std::string to_css(const Theme& theme, const std::string& prefix = "pulp");

    /// Export to a C++ header with constexpr color/dimension values.
    /// @code
    /// namespace my_theme {
    ///   constexpr uint32_t kBackground = 0xFF1a1a2e;
    ///   constexpr float kKnobSize = 60.0f;
    /// }
    /// @endcode
    static std::string to_cpp_header(const Theme& theme, const std::string& namespace_name);

    /// Export to OKLCH CSS color format.
    /// @code
    /// --pulp-accent: oklch(0.628 0.258 12.5);
    /// @endcode
    static std::string to_oklch_css(const Theme& theme, const std::string& prefix = "pulp");

    /// Export to GPU shader uniform declarations (WGSL).
    /// @code
    /// struct ThemeUniforms {
    ///   background: vec4<f32>,
    ///   accent: vec4<f32>,
    ///   knob_size: f32,
    /// };
    /// @endcode
    static std::string to_wgsl_uniforms(const Theme& theme, const std::string& struct_name = "ThemeUniforms");
};

// ── Phase 3 (gated on pulp #1307): DESIGN.md emitter ─────────────────────
//
// `export_designmd` is the shared write path behind two upcoming
// surfaces:
//
//   1. `pulp import-design --from <source> --update-design-md` — merges
//      an external re-import (Figma / Stitch / v0 / Pencil / Claude)
//      into the project's DESIGN.md instead of overwriting tokens.json.
//      Round-trip semantic — preserves prose sections and comment
//      placement from the prose_hints argument.
//   2. `pulp design` on save — captures the live editor's Theme state
//      back into DESIGN.md.
//
// This phase is GATED on #1307 (reimport-safe loop). Until anchor-stable
// IDs and 3-way merge semantics land there, the function below throws
// `std::logic_error("export_designmd is gated on pulp #1307")`. The
// signature is fixed so callers can already wire against it; the body
// fills in when the gate clears.

struct DesignMdProseHints {
    // Optional verbatim prose for each canonical section (Overview,
    // Colors, Typography, Layout, Elevation & Depth, Shapes, Components,
    // Do's and Don'ts). Unset keys produce auto-generated stub prose.
    std::unordered_map<std::string, std::string> section_bodies;
    // Optional name override; defaults to the theme's existing "name"
    // string token if present.
    std::string name_override;
};

/// Phase 3 placeholder. Implementation lands when pulp #1307 closes.
std::string export_designmd(const Theme& theme,
                             const DesignMdProseHints& hints = {});

} // namespace pulp::view
