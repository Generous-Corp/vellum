#pragma once

#include <vellum/authoring/js_application.hpp>

#include <string>
#include <string_view>
#include <vector>

namespace vellum::authoring {

/// Validates a versioned authoring-host render envelope and lowers its retained
/// tree into the renderer-independent scene and semantic host projections.
///
/// An authored node type outside the intrinsic set, or a malformed attributed
/// text-run, fails materialization. `error`, when non-null, is cleared on
/// entry and set only on failure. When `failure_diagnostics` is non-null it
/// receives every diagnostic collected up to and including the one that caused
/// a failure; `output` is left unchanged.
[[nodiscard]] bool materialize_rendered_tree(
    std::string_view rendered_json,
    RenderedApplication& output,
    std::string* error,
    std::vector<vellum::graphics::LoweringDiagnostic>* failure_diagnostics = nullptr);

}  // namespace vellum::authoring
