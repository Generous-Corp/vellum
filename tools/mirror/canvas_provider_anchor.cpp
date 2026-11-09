// The units are spelled vellum after the framework transfer and pulp before
// it; the consumer builds against whichever the install carries.
#if __has_include(<vellum/canvas/canvas.hpp>)
#include <vellum/canvas/canvas.hpp>
namespace units = vellum;
#else
#include <pulp/canvas/canvas.hpp>
namespace units = pulp;
#endif

namespace {
// Require the provider when the frontend is loaded, even before a consumer
// calls a re-exported symbol. This adds a binding without copying a definition.
[[gnu::used]] const auto provider_symbol_anchor = &units::canvas::Color::from_hsv;
}
