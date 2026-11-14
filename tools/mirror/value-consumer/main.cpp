// Uses Vellum's value model with only Vellum::MirrorView_core named at link
// time: the headers and InMemoryParamModel's definitions reach this consumer
// only through the unit library's INTERFACE link.

#include <vellum/value/in_memory_param_model.hpp>

#include <cmath>
#include <cstdio>
#include <utility>
#include <vector>

int main() {
    std::vector<vellum::value::ParamSpec> specs;
    specs.push_back({.key = "cutoff", .default_normalized = 0.25});
    vellum::value::InMemoryParamModel model(std::move(specs));
    const auto cutoff = model.resolve("cutoff");
    model.set_normalized(cutoff, 0.5);
    if (!cutoff.valid() || std::abs(model.normalized(cutoff) - 0.5) > 1e-9) {
        std::fprintf(stderr, "mirror value consumer: the model did not take the value\n");
        return 1;
    }
    std::printf("mirror value consumer: ok\n");
    return 0;
}
