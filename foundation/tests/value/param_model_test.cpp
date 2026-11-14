#include <vellum/value/in_memory_param_model.hpp>

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <optional>
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

void foreign_handles_are_ignored() {
    InMemoryParamModel first({{.key = "cutoff", .default_normalized = 0.25}});
    InMemoryParamModel second({{.key = "gain", .default_normalized = 0.75}});
    ParamModel& first_model = first;
    ParamModel& second_model = second;
    const ParamHandle cutoff = first_model.resolve("cutoff");
    const ParamHandle gain = second_model.resolve("gain");

    CHECK(second_model.normalized(cutoff) == 0.0);
    CHECK(second_model.display_text(cutoff, 0.5).empty());
    CHECK(second_model.value_count(cutoff) == 0);

    int listener_calls = 0;
    Subscription listener = second_model.subscribe([&](ParamHandle, double) {
        ++listener_calls;
    });
    std::vector<std::pair<ParamHandle, bool>> gestures;
    second.set_gesture_observer([&](ParamHandle param, bool begun) {
        gestures.emplace_back(param, begun);
    });

    second_model.set_normalized(cutoff, 0.1);
    second_model.begin(cutoff);
    second_model.end(cutoff);

    CHECK(second_model.normalized(gain) == 0.75);
    CHECK(second.gesture_depth(gain) == 0);
    CHECK(listener_calls == 0);
    CHECK(gestures.empty());

    second_model.set_normalized(gain, 0.5);
    CHECK(second_model.normalized(gain) == 0.5);
    CHECK(listener_calls == 1);
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

bool near(double a, double b, double tol = 1e-9) { return std::fabs(a - b) <= tol; }

InMemoryParamModel make_ranged_model() {
    std::vector<ParamSpec> specs;
    specs.push_back({.key = "freq", .name = "Frequency", .unit = "Hz", .plain_min = 20.0,
                     .plain_max = 20000.0, .skew = 3.0, .default_normalized = 0.5});
    specs.push_back({.key = "pan", .unit = "%", .plain_min = -50.0, .plain_max = 50.0});
    specs.push_back({.key = "step", .plain_min = 0.0, .plain_max = 4.0, .value_count = 5});
    specs.push_back({.key = "mode", .value_count = 3, .value_labels = {"low", "band", "high"}});
    return InMemoryParamModel(std::move(specs));
}

void plain_mapping_round_trips() {
    auto model = make_ranged_model();
    ParamModel& m = model;
    const auto freq = m.resolve("freq");
    const auto pan = m.resolve("pan");
    const auto step = m.resolve("step");

    // Ends map exactly, for linear, skewed and stepped ranges.
    CHECK(m.plain(pan, 0.0) == -50.0);
    CHECK(m.plain(pan, 1.0) == 50.0);
    CHECK(m.plain(freq, 0.0) == 20.0);
    CHECK(m.plain(freq, 1.0) == 20000.0);
    CHECK(m.plain(step, 0.0) == 0.0);
    CHECK(m.plain(step, 1.0) == 4.0);
    CHECK(m.normalized_from_plain(freq, 20.0) == 0.0);
    CHECK(m.normalized_from_plain(freq, 20000.0) == 1.0);

    // The curve is not linear: skew 3 puts the midpoint low.
    CHECK(near(m.plain(freq, 0.5), 20.0 + 19980.0 * 0.125));
    CHECK(near(m.plain(pan, 0.25), -25.0));

    for (double n : {0.0, 0.01, 0.25, 0.5, 0.77, 1.0}) {
        CHECK(near(m.normalized_from_plain(freq, m.plain(freq, n)), n));
        CHECK(near(m.normalized_from_plain(pan, m.plain(pan, n)), n));
    }
    // A stepped parameter snaps: each index round-trips, an off-step value lands on one.
    for (int i = 0; i < 5; ++i) {
        const double n = vellum::value::value_index_to_normalized(i, 5);
        CHECK(near(m.plain(step, n), static_cast<double>(i)));
        CHECK(near(m.normalized_from_plain(step, static_cast<double>(i)), n));
    }
    CHECK(near(m.plain(step, 0.3), 1.0));
    CHECK(near(m.normalized_from_plain(step, 2.4), 0.5));

    // Out of range clamps; NaN and bad handles give 0.
    CHECK(m.normalized_from_plain(pan, 500.0) == 1.0);
    CHECK(m.normalized_from_plain(pan, -500.0) == 0.0);
    CHECK(m.plain(pan, 4.0) == 50.0);
    CHECK(m.plain(pan, std::numeric_limits<double>::quiet_NaN()) == 0.0);
    CHECK(m.plain({}, 0.5) == 0.0);
    CHECK(m.normalized_from_plain({}, 5.0) == 0.0);
}

void info_describes_parameters() {
    auto model = make_ranged_model();
    ParamModel& m = model;
    const auto freq = m.info(m.resolve("freq"));
    CHECK(freq.name == "Frequency");
    CHECK(freq.unit == "Hz");
    CHECK(freq.default_normalized == 0.5);
    CHECK(freq.value_count == 0);
    CHECK(m.info(m.resolve("pan")).name == "pan"); // key when no name
    CHECK(m.info(m.resolve("mode")).value_count == 3);
    CHECK(m.info({}).name.empty());
}

void text_parse_and_format_round_trip() {
    auto model = make_ranged_model();
    ParamModel& m = model;
    const auto freq = m.resolve("freq");
    const auto pan = m.resolve("pan");
    const auto mode = m.resolve("mode");

    CHECK(m.display_text(pan, 0.75) == "25.00 %");
    for (double n : {0.0, 0.1, 0.5, 0.9, 1.0}) {
        const auto back = m.parse_text(pan, m.display_text(pan, n));
        CHECK(back.has_value() && near(*back, n, 1e-4));
        const auto fback = m.parse_text(freq, m.display_text(freq, n));
        CHECK(fback.has_value() && near(*fback, n, 1e-3));
    }
    // Labels round trip for a labelled parameter.
    for (double n : {0.0, 0.5, 1.0}) {
        const auto back = m.parse_text(mode, m.display_text(mode, n));
        CHECK(back.has_value() && *back == n);
    }
    CHECK(m.parse_text(mode, "BAND") == std::optional<double>(0.5));

    // Unit suffix optional, whitespace trimmed, out-of-range clamps.
    CHECK(m.parse_text(pan, " 25 ") == std::optional<double>(0.75));
    CHECK(m.parse_text(pan, "25%") == std::optional<double>(0.75));
    CHECK(m.parse_text(pan, "999") == std::optional<double>(1.0));

    // Bad input is rejected.
    for (const char* bad : {"", "   ", "abc", "12abc", "25 Hz", "nan", "inf", "1 2", "0x1p3x"})
        CHECK(!m.parse_text(pan, bad).has_value());
    CHECK(!m.parse_text(mode, "medium").has_value());
    CHECK(!m.parse_text({}, "1").has_value());

    // A custom parser takes precedence.
    InMemoryParamModel custom({{.key = "x", .parse = [](std::string_view t) -> std::optional<double> {
        return t == "half" ? std::optional<double>(0.5) : std::nullopt;
    }}});
    CHECK(custom.parse_text(custom.resolve("x"), "half") == std::optional<double>(0.5));
    CHECK(!custom.parse_text(custom.resolve("x"), "0.5").has_value());
}

void modulation_readout_is_base_plus_offset() {
    auto model = make_ranged_model();
    ParamModel& m = model;
    const auto pan = m.resolve("pan");
    CHECK(!m.modulated_normalized(pan).has_value());

    m.set_normalized(pan, 0.4);
    model.set_modulation_offset(pan, 0.25);
    CHECK(near(*m.modulated_normalized(pan), 0.65));
    CHECK(m.normalized(pan) == 0.4); // the base is untouched

    m.set_normalized(pan, 0.5); // follows the base
    CHECK(near(*m.modulated_normalized(pan), 0.75));
    model.set_modulation_offset(pan, 0.9); // clamps
    CHECK(*m.modulated_normalized(pan) == 1.0);
    model.set_modulation_offset(pan, -0.9);
    CHECK(*m.modulated_normalized(pan) == 0.0);

    model.set_modulation_offset(pan, std::numeric_limits<double>::quiet_NaN()); // ignored
    CHECK(*m.modulated_normalized(pan) == 0.0);
    model.set_modulation_offset(pan, std::nullopt);
    CHECK(!m.modulated_normalized(pan).has_value());
    CHECK(!m.modulated_normalized({}).has_value());
}

void gesture_reports_one_grouped_change() {
    auto model = make_ranged_model();
    ParamModel& m = model;
    const auto pan = m.resolve("pan");
    int begins = 0;
    int ends = 0;
    int values = 0;
    model.set_gesture_observer([&](ParamHandle, bool begun) { (begun ? begins : ends)++; });
    Subscription sub = m.subscribe([&](ParamHandle, double) { ++values; });

    m.begin(pan);
    for (int i = 1; i <= 20; ++i)
        m.set_normalized(pan, i / 20.0);
    m.end(pan);
    CHECK(begins == 1);
    CHECK(ends == 1);
    CHECK(values == 20);
    CHECK(model.gesture_depth(pan) == 0);
}

void watch_filters_to_one_parameter() {
    auto model = make_ranged_model();
    ParamModel& m = model;
    const auto pan = m.resolve("pan");
    const auto freq = m.resolve("freq");
    std::vector<double> heard;
    Subscription w = vellum::value::watch(m, pan, [&](double n) { heard.push_back(n); });
    CHECK(static_cast<bool>(w));
    m.set_normalized(freq, 0.9);
    m.set_normalized(pan, 0.3);
    CHECK(heard.size() == 1 && heard[0] == 0.3);
    w.reset();
    m.set_normalized(pan, 0.6);
    CHECK(heard.size() == 1);
    CHECK(!vellum::value::watch(m, {}, [](double) {}));
    CHECK(!vellum::value::watch(m, pan, {}));
}

void construction_rejects_bad_ranges() {
    auto throws = [](ParamSpec spec) {
        try {
            InMemoryParamModel m({std::move(spec)});
        } catch (const std::invalid_argument&) {
            return true;
        }
        return false;
    };
    CHECK(throws({.key = "a", .plain_min = 1.0, .plain_max = 0.0}));
    CHECK(throws({.key = "a", .skew = 0.0}));
    CHECK(throws({.key = "a", .skew = -1.0}));
    CHECK(throws({.key = "a", .plain_max = std::numeric_limits<double>::infinity()}));
    CHECK(!throws({.key = "a", .plain_min = 2.0, .plain_max = 2.0}));
}

}  // namespace

int main() {
    plain_mapping_round_trips();
    info_describes_parameters();
    text_parse_and_format_round_trip();
    modulation_readout_is_base_plus_offset();
    gesture_reports_one_grouped_change();
    watch_filters_to_one_parameter();
    construction_rejects_bad_ranges();
    resolve_finds_keys_verbatim();
    foreign_handles_are_ignored();
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
