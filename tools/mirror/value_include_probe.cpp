// Compiled with a mirrored target's own include directories and definitions
// (VellumMirror.cmake), so it proves a mirrored source can include Vellum's
// value model. It is never linked.

#include <vellum/value/param_model.hpp>

namespace {

[[maybe_unused]] double probe(vellum::value::ParamModel& model) {
    return vellum::value::value_index_to_normalized(1, model.value_count(model.resolve("probe")));
}

}  // namespace
