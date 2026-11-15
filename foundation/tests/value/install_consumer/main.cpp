// Uses the installed Vellum::Value headers and library through the abstract
// interfaces only, the way a widget does.
#include <vellum/value/in_memory_param_model.hpp>
#include <vellum/value/in_memory_value_feed.hpp>
#include <vellum/value/param_metadata.hpp>

#include <array>
#include <cstdio>

int main() {
    using namespace vellum::value;

    InMemoryParamModel params({{.key = "mix", .default_normalized = 0.5}});
    ParamModel& model = params;
    const ParamHandle mix = model.resolve("mix");
    double heard = -1.0;
    Subscription sub = model.subscribe([&](ParamHandle, double v) { heard = v; });
    model.begin(mix);
    model.set_normalized(mix, 0.25);
    model.end(mix);
    if (!mix.valid() || model.normalized(mix) != 0.25 || heard != 0.25 ||
        model.value_count(mix) != 0) {
        std::fprintf(stderr, "ParamModel round trip failed\n");
        return 1;
    }

    // The in-memory model was built by the installed library; its metadata is
    // found through the abstract model.
    ParamMetadata* metadata = metadata_of(model);
    if (metadata == nullptr) {
        std::fprintf(stderr, "metadata_of found no ParamMetadata on InMemoryParamModel\n");
        return 1;
    }
    metadata->set_plain_value(mix, 0.7);
    if (metadata->plain_value(mix) != 0.7 || metadata->range(mix).plain_max != 1.0) {
        std::fprintf(stderr, "ParamMetadata round trip failed\n");
        return 1;
    }

    InMemoryValueFeed values({{.name = "gr", .unit = "dB", .shape = ChannelShape::scalar}});
    ValueFeed& feed = values;
    const auto gr = feed.find("gr", ChannelShape::scalar);
    float v = 0.0f;
    if (!gr || !values.publish_scalar(*gr, -6.0f) || !feed.read_scalar(*gr, v) || v != -6.0f ||
        feed.publish_count(*gr) != 1) {
        std::fprintf(stderr, "ValueFeed round trip failed\n");
        return 1;
    }
    return 0;
}
