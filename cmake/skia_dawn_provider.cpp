#include "modules/svg/include/SkSVGDOM.h"

namespace {
// libsvg.a embeds Skia/resource objects already owned by the other locked
// archives. Keep the SVG factory reachable while ordinary archive selection
// resolves those dependencies without loading their duplicate definitions.
[[gnu::used]] const auto svg_factory = &SkSVGDOM::Builder::make;
}
