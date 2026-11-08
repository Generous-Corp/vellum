#pragma once

#include <string>
#include <string_view>

namespace vellum::graphics {

/// A renderer-independent report of content that a producer could not carry
/// into a `Scene` exactly as authored.
///
/// The severity and disposition vocabularies match the DesignIR diagnostics
/// model so a host can merge import-time and lowering-time reports. An `error`
/// means the producer refused the input; a `warning` means the scene was still
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
    /// A stable `VELLUM_`-prefixed identifier for the rule that fired.
    std::string code;
    /// The semantic ID of the authored node the report is about.
    std::string node_id;
    /// The authored property the report is about, when there is one.
    std::string property;
    std::string message;
};

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
