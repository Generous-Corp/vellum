#pragma once

#include <vellum/view/design_ir.hpp>

#include <string>

namespace vellum::authoring {

// Plan every asset path the IR references (manifest entries, node
// asset attributes and bundled fonts) into an `assets/` directory beside
// `output_file`, and rewrite each reference to that relative path. Resolve input
// paths beside the design. A dry run validates and rewrites without copying. Returns
// false, with the first failure in `error`, when an asset is missing, fails
// its hash check or cannot be copied.
bool localize_ir_assets(vellum::view::DesignIR& ir, const std::string& output_file,
                        std::string* error, const std::filesystem::path& input_directory,
                        bool dry_run);

}  // namespace vellum::authoring
