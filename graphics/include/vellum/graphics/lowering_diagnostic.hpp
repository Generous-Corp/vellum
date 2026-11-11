#pragma once

#include <string>
#include <string_view>

namespace vellum::graphics {

/// A renderer-independent report of content that a producer could not carry
/// into a `Scene` exactly as authored.
///
/// The severity and disposition vocabularies are the ones the DesignIR
/// diagnostics model uses. The record itself is not a DesignIR diagnostic: it
/// identifies content by retained-tree `node_id` rather than a document path,
/// and its codes follow the authoring host's `VELLUM_` form. An `error` means
/// the producer refused the input; a `warning` means the scene was still
/// produced with the described loss.
struct LoweringDiagnostic final {
    enum class Severity {
        info,
        warning,
        error,
    };

    enum class Disposition {
        portable,
        lowered,
        rasterized,
        web_only,
        extension,
        unsupported,
    };

    Severity severity = Severity::warning;
    Disposition disposition = Disposition::unsupported;
    /// The rule that fired, one of the `k...Code` constants below.
    std::string code;
    /// The semantic ID of the authored node the report is about.
    std::string node_id;
    /// The authored property the report is about, when there is one.
    std::string property;
    std::string message;
};

/// An authored node type outside the intrinsic set; always an error.
inline constexpr std::string_view kUnknownNodeTypeCode = "VELLUM_UNKNOWN_NODE_TYPE";
/// An authored style key that the node's type does not consume.
inline constexpr std::string_view kStyleDroppedCode = "VELLUM_STYLE_DROPPED";
/// An intrinsic whose content has no scene kind, so only its box was kept.
inline constexpr std::string_view kNodeContentDroppedCode = "VELLUM_NODE_CONTENT_DROPPED";
/// An attributed text-run with a missing or duplicate id, non-string text,
/// children, or a non-object style; always an error.
inline constexpr std::string_view kInvalidTextRunCode = "VELLUM_INVALID_TEXT_RUN";
/// A path with missing or malformed path data, path data over the path
/// budgets, or an invalid fill rule, fill or stroke; always an error.
inline constexpr std::string_view kInvalidPathCode = "VELLUM_INVALID_PATH";
/// An image with children, an invalid fit, sampling or source rectangle, or a
/// malformed asset table; always an error.
inline constexpr std::string_view kInvalidImageCode = "VELLUM_INVALID_IMAGE";
/// An image node whose asset is not in `Scene::images`; a warning. The
/// renderer draws `kMissingImagePlaceholder` over the node's box instead. The
/// same code warns of a source rectangle that leaves nothing of its image to
/// draw.
inline constexpr std::string_view kImageAssetMissingCode = "VELLUM_IMAGE_ASSET_MISSING";
/// A node of a kind this build does not know, or a field or kind newer than
/// its scene's `scene_version`; always an error. The consumer refuses the
/// whole scene rather than drawing it with the node missing.
inline constexpr std::string_view kSceneVersionMismatchCode = "VELLUM_SCENE_VERSION_MISMATCH";

[[nodiscard]] constexpr std::string_view to_string(
    LoweringDiagnostic::Severity severity) noexcept {
    switch (severity) {
    case LoweringDiagnostic::Severity::info: return "info";
    case LoweringDiagnostic::Severity::warning: return "warning";
    case LoweringDiagnostic::Severity::error: return "error";
    }
    return "error";
}

[[nodiscard]] constexpr std::string_view to_string(
    LoweringDiagnostic::Disposition disposition) noexcept {
    switch (disposition) {
    case LoweringDiagnostic::Disposition::portable: return "portable";
    case LoweringDiagnostic::Disposition::lowered: return "lowered";
    case LoweringDiagnostic::Disposition::rasterized: return "rasterized";
    case LoweringDiagnostic::Disposition::web_only: return "web-only";
    case LoweringDiagnostic::Disposition::extension: return "extension";
    case LoweringDiagnostic::Disposition::unsupported: return "unsupported";
    }
    return "unsupported";
}

}  // namespace vellum::graphics
