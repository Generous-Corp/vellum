#include <pulp/canvas/canvas.hpp>

namespace {
// Require the provider when the frontend is loaded, even before a consumer
// calls a re-exported symbol. This adds a binding without copying a definition.
[[gnu::used]] const auto provider_symbol_anchor = &pulp::canvas::Color::from_hsv;
}
