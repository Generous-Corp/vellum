#pragma once

#include <iosfwd>

namespace vellum::authoring {

/// `vellum-source-adapter detect [--compat PATH] [--default-compat PATH]
/// [--report-new-format] [--json] INPUT`: detect the design-import source of a
/// file or directory without importing it.
///
/// The manifest is `--compat` when given; otherwise the nearest `compat.json`
/// above the input (then above the working directory); otherwise
/// `--default-compat`. Exit 0 when a source is detected or a new-format report
/// is printed, 2 when no source matches, 1 on a usage or manifest error.
int run_source_detect(int argc, const char* const* argv, std::ostream& out, std::ostream& err);

} // namespace vellum::authoring
