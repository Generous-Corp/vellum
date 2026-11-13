// Uses Vellum's value model and a framework widget from one translation unit,
// with only Vellum::MirrorView_core named at link time.

#include <vellum/value/in_memory_param_model.hpp>
#include <vellum/view/widgets.hpp>

#include <cmath>
#include <cstdio>
#include <utility>
#include <vector>

int main() {
    std::vector<vellum::value::ParamSpec> specs;
    specs.push_back({.key = "cutoff", .default_normalized = 0.25});
    vellum::value::InMemoryParamModel model(std::move(specs));
    const auto cutoff = model.resolve("cutoff");

    vellum::view::Knob knob;
    knob.set_value(static_cast<float>(model.normalized(cutoff)));
    if (!cutoff.valid() || std::abs(knob.value() - 0.25f) > 1e-6f) {
        std::fprintf(stderr, "mirror value consumer: knob did not take the model value\n");
        return 1;
    }
    std::printf("mirror value consumer: ok\n");
    return 0;
}
