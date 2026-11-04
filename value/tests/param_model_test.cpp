#include <vellum/value/in_memory_param_model.hpp>

#include <cstdio>
#include <cstdlib>
#include <limits>
#include <stdexcept>
#include <string>
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
using vellum::value::ParamHandle;
using vellum::value::ParamModel;
using vellum::value::ParamSpec;
using vellum::value::Subscription;

InMemoryParamModel make_model() {
    std::vector<ParamSpec> specs;
    specs.push_back({.key = "cutoff", .default_normalized = 0.5});
    specs.push_back({.key = "mode",
                     .default_normalized = 0.0,
                     .value_count = 3,
                     .value_labels = {"low", "band", "high"}});
    specs.push_back({.key = "bypass", .default_normalized = 1.0, .value_count = 2});
    specs.push_back({.key = "gain",
                     .default_normalized = 0.25,
                     .format = [](double v) { return std::to_string(static_cast<int>(v * 100)) + " %"; }});
    specs.push_back({.key = "fixed", .default_normalized = 0.7, .value_count = 1});
    return InMemoryParamModel(std::move(specs));
}

void resolve_finds_keys_verbatim() {
    auto model = make_model();
    ParamModel& m = model;
    const ParamHandle cutoff = m.resolve("cutoff");
    CHECK(cutoff.valid());
    CHECK(m.resolve("cutoff") == cutoff);
    CHECK(m.resolve("mode").valid());
    CHECK(m.resolve("mode") != cutoff);
    CHECK(!m.resolve("Cutoff").valid());
    CHECK(!m.resolve("").valid());
    CHECK(!m.resolve("missing").valid());

    // An invalid or foreign handle is ignored by every call.
    const ParamHandle bad{};
    CHECK(m.normalized(bad) == 0.0);
    m.set_normalized(bad, 0.3);
    m.begin(bad);
    m.end(bad);
    CHECK(m.display_text(bad, 0.5).empty());
    CHECK(m.value_count(bad) == 0);
    const ParamHandle foreign{static_cast<std::uint32_t>(model.size())};
    CHECK(m.normalized(foreign) == 0.0);
    CHECK(model.gesture_depth(foreign) == 0);
}

void normalized_round_trips() {
    auto model = make_model();
    ParamModel& m = model;
    const auto cutoff = m.resolve("cutoff");
    CHECK(m.normalized(cutoff) == 0.5);
    for (double v : {0.0, 0.125, 0.3333333333333333, 0.999, 1.0}) {
        m.set_normalized(cutoff, v);
        CHECK(m.normalized(cutoff) == v);
    }
    m.set_normalized(cutoff, -0.5);
    CHECK(m.normalized(cutoff) == 0.0);
    m.set_normalized(cutoff, 7.0);
    CHECK(m.normalized(cutoff) == 1.0);
    m.set_normalized(cutoff, std::numeric_limits<double>::quiet_NaN());
    CHECK(m.normalized(cutoff) == 1.0);

    // A stepped parameter snaps to its values; each value round-trips exactly.
    const auto mode = m.resolve("mode");
    m.set_normalized(mode, 0.4);
    CHECK(m.normalized(mode) == 0.5);
    m.set_normalized(mode, 0.8);
    CHECK(m.normalized(mode) == 1.0);
    for (int i = 0; i < 3; ++i) {
        const double v = vellum::value::value_index_to_normalized(i, 3);
        m.set_normalized(mode, v);
        CHECK(m.normalized(mode) == v);
        CHECK(vellum::value::normalized_to_value_index(m.normalized(mode), 3) == i);
    }
}

void value_count_semantics() {
    auto model = make_model();
    ParamModel& m = model;
    using vellum::value::normalized_to_value_index;
    using vellum::value::value_index_to_normalized;

    CHECK(m.value_count(m.resolve("cutoff")) == 0); // continuous
    CHECK(m.value_count(m.resolve("mode")) == 3);   // values, not intervals
    CHECK(m.value_count(m.resolve("bypass")) == 2);
    CHECK(m.value_count(m.resolve("fixed")) == 1);

    // Values, not intervals: three values sit at 0, 0.5 and 1.
    CHECK(value_index_to_normalized(0, 3) == 0.0);
    CHECK(value_index_to_normalized(1, 3) == 0.5);
    CHECK(value_index_to_normalized(2, 3) == 1.0);
    CHECK(value_index_to_normalized(9, 3) == 1.0);
    CHECK(value_index_to_normalized(-1, 3) == 0.0);
    CHECK(value_index_to_normalized(1, 2) == 1.0);
    // 0 and 1 have no denominator; the helpers never divide by zero.
    CHECK(value_index_to_normalized(0, 0) == 0.0);
    CHECK(value_index_to_normalized(3, 1) == 0.0);
    CHECK(normalized_to_value_index(0.7, 0) == 0);
    CHECK(normalized_to_value_index(0.7, 1) == 0);
    CHECK(normalized_to_value_index(0.24, 3) == 0);
    CHECK(normalized_to_value_index(0.25, 3) == 1);
    CHECK(normalized_to_value_index(0.74, 3) == 1);
    CHECK(normalized_to_value_index(2.0, 3) == 2);
    CHECK(normalized_to_value_index(std::numeric_limits<double>::quiet_NaN(), 3) == 0);

    // A single-valued parameter keeps its clamped default; it is not snapped.
    CHECK(m.normalized(m.resolve("fixed")) == 0.7);
}

void display_text_prefers_format_then_labels() {
    auto model = make_model();
    ParamModel& m = model;
    CHECK(m.display_text(m.resolve("gain"), 0.5) == "50 %");
    CHECK(m.display_text(m.resolve("mode"), 0.0) == "low");
    CHECK(m.display_text(m.resolve("mode"), 0.6) == "band");
    CHECK(m.display_text(m.resolve("mode"), 1.0) == "high");
    CHECK(m.display_text(m.resolve("cutoff"), 0.25) == "0.25");
    // The text is for the value asked about, not the current one.
    CHECK(m.normalized(m.resolve("cutoff")) == 0.5);
}

void gestures_nest_and_balance() {
    auto model = make_model();
    ParamModel& m = model;
    const auto cutoff = m.resolve("cutoff");
    const auto mode = m.resolve("mode");

    std::vector<std::pair<ParamHandle, bool>> host;
    model.set_gesture_observer([&](ParamHandle p, bool begun) { host.emplace_back(p, begun); });

    m.begin(cutoff);
    m.begin(cutoff); // nested: the host still sees one gesture
    CHECK(model.gesture_depth(cutoff) == 2);
    CHECK(host.size() == 1);
    m.begin(mode); // independent per parameter
    CHECK(host.size() == 2);
    m.end(cutoff);
    CHECK(model.gesture_depth(cutoff) == 1);
    CHECK(host.size() == 2);
    m.end(cutoff);
    CHECK(model.gesture_depth(cutoff) == 0);
    m.end(mode);
    CHECK(host.size() == 4);
    if (host.size() == 4) {
        CHECK(host[0] == std::make_pair(cutoff, true));
        CHECK(host[1] == std::make_pair(mode, true));
        CHECK(host[2] == std::make_pair(cutoff, false));
        CHECK(host[3] == std::make_pair(mode, false));
    }

    // An unbalanced end does nothing: depth never goes negative, the host hears
    // nothing, and the next begin still opens a gesture.
    m.end(cutoff);
    m.end(cutoff);
    CHECK(model.gesture_depth(cutoff) == 0);
    CHECK(host.size() == 4);
    m.begin(cutoff);
    CHECK(model.gesture_depth(cutoff) == 1);
    CHECK(host.size() == 5);
    m.end(cutoff);
    CHECK(host.size() == 6);
}

void listeners_hear_changes_until_reset() {
    auto model = make_model();
    ParamModel& m = model;
    const auto cutoff = m.resolve("cutoff");
    const auto mode = m.resolve("mode");

    std::vector<std::pair<ParamHandle, double>> heard_a;
    std::vector<std::pair<ParamHandle, double>> heard_b;
    Subscription a = m.subscribe([&](ParamHandle p, double v) { heard_a.emplace_back(p, v); });
    Subscription b = m.subscribe([&](ParamHandle p, double v) { heard_b.emplace_back(p, v); });
    CHECK(static_cast<bool>(a));
    CHECK(!m.subscribe({}));

    m.set_normalized(cutoff, 0.75);
    m.set_normalized(mode, 0.9); // delivered as the snapped value
    CHECK(heard_a.size() == 2);
    CHECK(heard_b.size() == 2);
    if (heard_a.size() == 2) {
        CHECK(heard_a[0] == std::make_pair(cutoff, 0.75));
        CHECK(heard_a[1] == std::make_pair(mode, 1.0));
    }

    // No change, no delivery: same value, or a value that snaps to it.
    m.set_normalized(cutoff, 0.75);
    m.set_normalized(mode, 0.95);
    CHECK(heard_a.size() == 2);

    a.reset();
    CHECK(!a);
    m.set_normalized(cutoff, 0.1);
    CHECK(heard_a.size() == 2);
    CHECK(heard_b.size() == 3);

    // Moving a subscription keeps one registration.
    Subscription moved = std::move(b);
    m.set_normalized(cutoff, 0.2);
    CHECK(heard_b.size() == 4);
    moved.reset();
    m.set_normalized(cutoff, 0.3);
    CHECK(heard_b.size() == 4);
}

void listener_may_unsubscribe_during_delivery() {
    auto model = make_model();
    ParamModel& m = model;
    const auto cutoff = m.resolve("cutoff");
    int first = 0;
    int second = 0;
    Subscription second_sub;
    Subscription first_sub = m.subscribe([&](ParamHandle, double) {
        ++first;
        second_sub.reset(); // removed mid-delivery: not called afterwards
    });
    second_sub = m.subscribe([&](ParamHandle, double) { ++second; });
    m.set_normalized(cutoff, 0.9);
    CHECK(first == 1);
    CHECK(second == 0);
}

void subscription_may_outlive_model() {
    Subscription sub;
    {
        auto model = make_model();
        sub = model.subscribe([](ParamHandle, double) {});
    }
    sub.reset(); // the model is gone; cancelling is a no-op, not a crash
    CHECK(!sub);
}

void construction_rejects_bad_specs() {
    auto throws = [](std::vector<ParamSpec> specs) {
        try {
            InMemoryParamModel m(std::move(specs));
        } catch (const std::invalid_argument&) {
            return true;
        }
        return false;
    };
    CHECK(throws({{.key = ""}}));
    CHECK(throws({{.key = "a"}, {.key = "a"}}));
    CHECK(throws({{.key = "a", .value_count = -1}}));
    CHECK(!throws({{.key = "a"}, {.key = "b"}}));

    // Defaults are clamped and snapped like set_normalized.
    InMemoryParamModel m({{.key = "x", .default_normalized = 3.0},
                          {.key = "y", .default_normalized = 0.4, .value_count = 2}});
    CHECK(m.normalized(m.resolve("x")) == 1.0);
    CHECK(m.normalized(m.resolve("y")) == 0.0);
}

}  // namespace

int main() {
    resolve_finds_keys_verbatim();
    normalized_round_trips();
    value_count_semantics();
    display_text_prefers_format_then_labels();
    gestures_nest_and_balance();
    listeners_hear_changes_until_reset();
    listener_may_unsubscribe_during_delivery();
    subscription_may_outlive_model();
    construction_rejects_bad_specs();
    if (failures != 0) {
        std::fprintf(stderr, "%d check(s) failed\n", failures);
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
