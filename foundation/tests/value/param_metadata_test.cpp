#include <vellum/value/in_memory_param_model.hpp>
#include <vellum/value/param_metadata.hpp>

#include <cmath>
#include <cstdio>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

int failures = 0;

#define CHECK(cond)                                                                    \
    do {                                                                               \
        if (!(cond)) {                                                                 \
            std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                                \
        }                                                                              \
    } while (false)

using vellum::value::InMemoryParamModel;
using vellum::value::metadata_of;
using vellum::value::ParamHandle;
using vellum::value::ParamInfo;
using vellum::value::ParamModel;
using vellum::value::ParamSpec;
using vellum::value::Subscription;

bool near(double a, double b) { return std::abs(a - b) < 1e-9; }

InMemoryParamModel make_model() {
    std::vector<ParamSpec> specs;
    specs.push_back({.key = "cutoff", .plain_min = 20.0, .plain_max = 20000.0, .skew = 3.0,
                     .default_normalized = 0.5});
    specs.push_back({.key = "mode", .plain_min = 1.0, .plain_max = 5.0, .default_normalized = 0.25,
                     .value_count = 5, .value_labels = {"a", "b", "c", "d", "e"}});
    specs.push_back({.key = "depth", .plain_min = 0.0, .plain_max = 10.0, .default_normalized = 0.3});
    specs.push_back({.key = "half", .value_count = 3, .value_labels = {"x", "y"}});
    specs.push_back({.key = "curved", .plain_min = 0.0, .plain_max = 100.0, .skew = 2.0,
                     .value_count = 3});
    specs.push_back({.key = "curved_pair", .plain_min = 0.0, .plain_max = 100.0, .skew = 2.0,
                     .value_count = 2});
    return InMemoryParamModel(std::move(specs));
}

// A ParamModel with no metadata: every call forwards to a model that has it.
class PlainModel final : public ParamModel {
public:
    explicit PlainModel(InMemoryParamModel& inner) : inner_(inner) {}
    ParamHandle resolve(std::string_view key) override { return inner_.resolve(key); }
    double normalized(ParamHandle p) override { return inner_.normalized(p); }
    void set_normalized(ParamHandle p, double n) override { inner_.set_normalized(p, n); }
    void begin(ParamHandle p) override { inner_.begin(p); }
    void end(ParamHandle p) override { inner_.end(p); }
    std::string display_text(ParamHandle p, double n) override { return inner_.display_text(p, n); }
    int value_count(ParamHandle p) override { return inner_.value_count(p); }
    Subscription subscribe(Listener l) override { return inner_.subscribe(std::move(l)); }
    ParamInfo info(ParamHandle p) override { return inner_.info(p); }
    double plain(ParamHandle p, double n) override { return inner_.plain(p, n); }
    double normalized_from_plain(ParamHandle p, double v) override {
        return inner_.normalized_from_plain(p, v);
    }
    std::optional<double> parse_text(ParamHandle p, std::string_view t) override {
        return inner_.parse_text(p, t);
    }
    std::optional<double> modulated_normalized(ParamHandle p) override {
        return inner_.modulated_normalized(p);
    }

private:
    InMemoryParamModel& inner_;
};

void metadata_is_found_only_where_a_model_has_it() {
    auto model = make_model();
    PlainModel plain(model);
    CHECK(metadata_of(model) != nullptr);
    CHECK(metadata_of(plain) == nullptr);
}

void range_describes_the_plain_axis() {
    auto model = make_model();
    auto& meta = *metadata_of(model);
    const auto cutoff = model.resolve("cutoff");
    const auto r = meta.range(cutoff);
    CHECK(r.plain_min == 20.0);
    CHECK(r.plain_max == 20000.0);
    CHECK(r.skew == 3.0);
    CHECK(r.step == 0.0);
    CHECK(!r.symmetric_skew);
    CHECK(near(r.plain_default, model.plain(cutoff, 0.5)));

    CHECK(r.plain_values.empty());

    const auto mode = meta.range(model.resolve("mode"));
    CHECK(mode.step == 1.0);
    CHECK(mode.plain_default == 2.0);
    CHECK(mode.plain_values.empty());

    const auto bad = meta.range(ParamHandle{});
    CHECK(bad.plain_min == 0.0 && bad.plain_max == 1.0 && bad.step == 0.0);
}

// A skewed discrete axis snaps before it curves, so its plain values are
// 0, 25 and 100: no uniform step describes them, and the range must say so.
void a_skewed_discrete_range_lists_its_uneven_values() {
    auto model = make_model();
    auto& meta = *metadata_of(model);
    const auto curved = model.resolve("curved");
    const auto r = meta.range(curved);
    CHECK(r.step == 0.0);
    CHECK(r.plain_values.size() == 3);
    if (r.plain_values.size() == 3) {
        CHECK(near(r.plain_values[0], 0.0));
        CHECK(near(r.plain_values[1], 25.0));
        CHECK(near(r.plain_values[2], 100.0));
        for (int i = 0; i < 3; ++i)
            CHECK(near(r.plain_values[static_cast<std::size_t>(i)],
                       model.plain(curved, i / 2.0)));
    }
    // Every value the model can hold is one the range lists.
    for (double n = 0.0; n <= 1.0; n += 0.05) {
        model.set_normalized(curved, n);
        const double v = meta.plain_value(curved);
        CHECK(near(v, 0.0) || near(v, 25.0) || near(v, 100.0));
    }

    // Two values are evenly spaced whatever the curve.
    const auto pair = meta.range(model.resolve("curved_pair"));
    CHECK(pair.step == 100.0);
    CHECK(pair.plain_values.empty());
}

void value_labels_need_one_per_value() {
    auto model = make_model();
    auto& meta = *metadata_of(model);
    CHECK((meta.value_labels(model.resolve("mode")) == std::vector<std::string>{"a", "b", "c", "d", "e"}));
    CHECK(meta.value_labels(model.resolve("half")).empty());
    CHECK(meta.value_labels(model.resolve("depth")).empty());
}

void a_plain_write_reads_back_exactly() {
    auto model = make_model();
    auto& meta = *metadata_of(model);
    const auto depth = model.resolve("depth");
    for (int i = 0; i <= 100; ++i) {
        const double plain = i * 0.1;
        meta.set_plain_value(depth, plain);
        CHECK(meta.plain_value(depth) == plain);
        CHECK(near(model.normalized(depth), plain / 10.0));
    }
    meta.set_plain_value(depth, 99.0);
    CHECK(meta.plain_value(depth) == 10.0);
    meta.set_plain_value(depth, std::nan(""));
    CHECK(meta.plain_value(depth) == 10.0);
    meta.set_plain_value(ParamHandle{}, 3.0);
    CHECK(meta.plain_value(ParamHandle{}) == 0.0);
}

void a_stepped_plain_write_snaps_and_notifies() {
    auto model = make_model();
    auto& meta = *metadata_of(model);
    const auto mode = model.resolve("mode");
    std::vector<double> heard;
    auto sub = model.subscribe([&](ParamHandle, double n) { heard.push_back(n); });
    meta.set_plain_value(mode, 4.0);
    CHECK(meta.plain_value(mode) == 4.0);
    CHECK(near(model.normalized(mode), 0.75));
    meta.set_plain_value(mode, 2.6);
    CHECK(meta.plain_value(mode) == 3.0);
    CHECK((heard == std::vector<double>{0.75, 0.5}));
}

void a_normalized_write_ends_the_exact_value() {
    auto model = make_model();
    auto& meta = *metadata_of(model);
    const auto cutoff = model.resolve("cutoff");
    meta.set_plain_value(cutoff, 440.0);
    CHECK(meta.plain_value(cutoff) == 440.0);
    model.set_normalized(cutoff, 0.25);
    CHECK(near(meta.plain_value(cutoff), model.plain(cutoff, 0.25)));
}

}  // namespace

int main() {
    metadata_is_found_only_where_a_model_has_it();
    range_describes_the_plain_axis();
    a_skewed_discrete_range_lists_its_uneven_values();
    value_labels_need_one_per_value();
    a_plain_write_reads_back_exactly();
    a_stepped_plain_write_snaps_and_notifies();
    a_normalized_write_ends_the_exact_value();
    if (failures != 0) {
        std::fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("param metadata: all checks passed\n");
    return 0;
}
