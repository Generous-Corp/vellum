#pragma once

#include <vellum/view/design_ir.hpp>

#include <string>

namespace vellum::authoring {

// Copy every absolute asset path the IR references (manifest entries, node
// asset attributes and bundled fonts) into an `assets/` directory beside
// `output_file`, and rewrite each reference to that relative path. Returns
// false, with the first failure in `error`, when an asset is missing, fails
// its hash check or cannot be copied.
bool localize_ir_assets(vellum::view::DesignIR& ir, const std::string& output_file,
                        std::string* error);

}  // namespace vellum::authoring
