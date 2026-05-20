// design_import_internal.hpp — PRIVATE shared declarations for the
// design-import translation units.
//
// Created in the 2026-05 Phase 6 (A3) refactor when design_import.cpp
// was split into design_tokens.cpp + design_codegen.cpp. design_codegen
// needs the motion-provenance vendor key, which is defined (with
// external linkage, at namespace scope) in design_import.cpp; this
// header gives it a single declaration point instead of an ad-hoc
// extern decl.
//
// PRIVATE: lives under core/view/src/, not the public include tree.
// Not part of the installed SDK surface — do not reference from headers
// outside core/view/src/.

#pragma once

#include <pulp/view/design_import.hpp>

namespace pulp::view {

// Phase 9 motion-provenance vendor key — lowercased, slash-friendly
// token matching the import CLI's `source` argument. Stable across
// releases (fixtures depend on these strings). Defined in
// design_import.cpp.
const char* design_source_vendor_key(DesignSource source);

}  // namespace pulp::view
