#pragma once

#include <vellum/authoring/js_application.hpp>

#include <string>
#include <string_view>

namespace vellum::authoring {

/// Validates a versioned authoring-host render envelope and lowers its retained
/// tree into the renderer-independent scene and semantic host projections.
[[nodiscard]] bool materialize_rendered_tree(
    std::string_view rendered_json,
    RenderedApplication& output,
    std::string* error);

}  // namespace vellum::authoring
