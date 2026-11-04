#include <vellum/value/in_memory_value_feed.hpp>

#include <array>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <thread>
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

using vellum::value::ChannelEvent;
using vellum::value::ChannelShape;
using vellum::value::ChannelSpec;
using vellum::value::InMemoryValueFeed;
using vellum::value::ValueFeed;

enum : std::size_t { gr = 0, level = 1, scope = 2, notes = 3 };

InMemoryValueFeed make_feed() {
    return InMemoryValueFeed({
        {.name = "gr", .unit = "dB", .shape = ChannelShape::scalar, .neutral = 0.0f},
        {.name = "level", .shape = ChannelShape::meter, .neutral = -60.0f, .capacity = 2},
        {.name = "scope", .shape = ChannelShape::vector, .capacity = 8},
        {.name = "notes", .shape = ChannelShape::events, .capacity = 4},
    });
}

void channels_are_described_and_found() {
    auto feed = make_feed();
    const ValueFeed& f = feed;
    CHECK(f.channel_count() == 4);
    CHECK(f.channel(gr).name == "gr");
    CHECK(f.channel(gr).unit == "dB");
    CHECK(f.channel(level).shape == ChannelShape::meter);
    CHECK(f.channel(level).neutral == -60.0f);
    CHECK(f.find("scope", ChannelShape::vector) == scope);
    CHECK(f.find("notes", ChannelShape::events) == notes);
    CHECK(!f.find("scope", ChannelShape::scalar)); // name and shape must both match
    CHECK(!f.find("missing", ChannelShape::scalar));
    CHECK(feed.capacity(gr) == 1);
    CHECK(feed.capacity(scope) == 8);
    CHECK(feed.capacity(99) == 0);
}

void nothing_published_reads_empty() {
    auto feed = make_feed();
    float v = 42.0f;
    CHECK(!feed.read_scalar(gr, v));
    CHECK(v == 42.0f);
    std::array<float, 2> rms{}, peak{};
    CHECK(feed.read_meter(level, rms, peak) == 0);
    std::array<float, 8> samples{};
    CHECK(feed.read_vector(scope, samples) == 0);
    std::array<ChannelEvent, 4> events{};
    CHECK(feed.read_events(notes, events) == 0);
    for (std::size_t i = 0; i < 4; ++i)
        CHECK(feed.publish_count(i) == 0);
}

void latest_wins() {
    auto feed = make_feed();
    for (int i = 1; i <= 5; ++i)
        CHECK(feed.publish_scalar(gr, static_cast<float>(i)));
    float v = 0.0f;
    CHECK(feed.read_scalar(gr, v));
    CHECK(v == 5.0f); // the newest publish, not the oldest unread one
    CHECK(feed.read_scalar(gr, v));
    CHECK(v == 5.0f); // reading again keeps the latest value

    const std::array<float, 3> a{1, 2, 3};
    const std::array<float, 2> b{9, 8};
    CHECK(feed.publish_vector(scope, a));
    CHECK(feed.publish_vector(scope, b));
    std::array<float, 8> samples{};
    CHECK(feed.read_vector(scope, samples) == 2);
    CHECK(samples[0] == 9.0f && samples[1] == 8.0f);

    const std::array<float, 2> rms{0.1f, 0.2f}, peak{0.5f, 0.6f};
    CHECK(feed.publish_meter(level, rms, peak));
    std::array<float, 1> one_rms{}, one_peak{};
    CHECK(feed.read_meter(level, one_rms, one_peak) == 2); // frame carries 2
    CHECK(one_rms[0] == 0.1f && one_peak[0] == 0.5f);

    const std::array<ChannelEvent, 2> e1{{{3, 1.0f}, {7, 0.0f}}};
    const std::array<ChannelEvent, 1> e2{{{11, 0.5f}}};
    CHECK(feed.publish_events(notes, e1));
    CHECK(feed.publish_events(notes, e2));
    std::array<ChannelEvent, 4> events{};
    CHECK(feed.read_events(notes, events) == 1);
    CHECK(events[0].frame_index == 11 && events[0].value == 0.5f);
    CHECK(feed.publish_events(notes, {})); // a block with no occurrences
    CHECK(feed.read_events(notes, events) == 0);
}

void publish_truncates_to_capacity() {
    auto feed = make_feed();
    std::array<float, 12> big{};
    for (std::size_t i = 0; i < big.size(); ++i)
        big[i] = static_cast<float>(i);
    CHECK(feed.publish_vector(scope, big));
    std::array<float, 12> out{};
    CHECK(feed.read_vector(scope, out) == 8);
    CHECK(out[7] == 7.0f && out[8] == 0.0f);
}

void publish_count_tells_stopped_from_steady() {
    auto feed = make_feed();
    float v = 0.0f;

    CHECK(feed.publish_scalar(gr, -3.0f));
    std::uint32_t seen = feed.publish_count(gr);
    CHECK(seen == 1);
    CHECK(feed.read_scalar(gr, v) && v == -3.0f);

    // Steady: the value holds, but the writer keeps publishing.
    CHECK(feed.publish_scalar(gr, -3.0f));
    CHECK(feed.read_scalar(gr, v) && v == -3.0f);
    CHECK(feed.publish_count(gr) != seen);
    seen = feed.publish_count(gr);

    // Stopped: no publish since the last tick; the count does not move.
    CHECK(feed.read_scalar(gr, v) && v == -3.0f);
    CHECK(feed.publish_count(gr) == seen);

    // Counts are per channel, and an unknown index counts nothing.
    CHECK(feed.publish_count(scope) == 0);
    CHECK(feed.publish_count(99) == 0);
}

void shape_mismatch_is_refused() {
    auto feed = make_feed();
    const std::array<float, 2> two{1, 2};
    const std::array<ChannelEvent, 1> ev{{{0, 1.0f}}};

    // Publishing with the wrong shape's call changes nothing.
    CHECK(!feed.publish_scalar(scope, 1.0f));
    CHECK(!feed.publish_vector(gr, two));
    CHECK(!feed.publish_meter(scope, two, two));
    CHECK(!feed.publish_events(level, ev));
    CHECK(!feed.publish_scalar(99, 1.0f));
    const std::array<float, 1> one{1};
    CHECK(!feed.publish_meter(level, two, one)); // rms and peak sizes differ
    for (std::size_t i = 0; i < 4; ++i)
        CHECK(feed.publish_count(i) == 0);

    // Reading with the wrong shape's call returns false or 0 and writes nothing.
    CHECK(feed.publish_scalar(gr, 1.0f));
    CHECK(feed.publish_vector(scope, two));
    CHECK(feed.publish_meter(level, two, two));
    CHECK(feed.publish_events(notes, ev));
    float v = 42.0f;
    CHECK(!feed.read_scalar(scope, v));
    CHECK(v == 42.0f);
    std::array<float, 8> buf{};
    buf.fill(42.0f);
    std::array<float, 8> buf2{};
    buf2.fill(42.0f);
    CHECK(feed.read_vector(gr, buf) == 0);
    CHECK(feed.read_vector(level, buf) == 0);
    CHECK(feed.read_meter(scope, buf, buf2) == 0);
    CHECK(feed.read_meter(99, buf, buf2) == 0);
    CHECK(buf[0] == 42.0f && buf2[0] == 42.0f);
    std::array<ChannelEvent, 2> evs{};
    CHECK(feed.read_events(scope, evs) == 0);
    CHECK(feed.read_events(99, evs) == 0);
}

void construction_rejects_bad_specs() {
    auto throws = [](std::vector<ChannelSpec> specs) {
        try {
            InMemoryValueFeed f(std::move(specs));
        } catch (const std::invalid_argument&) {
            return true;
        }
        return false;
    };
    CHECK(throws({{.name = ""}}));
    CHECK(throws({{.name = "a", .shape = ChannelShape::vector, .capacity = 0}}));
    CHECK(throws({{.name = "a"}, {.name = "a"}}));
    // The same name with a different shape is a different channel.
    CHECK(!throws({{.name = "a"}, {.name = "a", .shape = ChannelShape::meter}}));
    CHECK(!throws({{.name = "a", .capacity = 0}})); // capacity is ignored for a scalar
}

// One producer publishes blocks whose samples all equal the block's sequence
// number; one reader checks every read is a single whole block (no torn mix
// of two publishes) and that blocks only move forward.
void hammer_single_producer_single_reader() {
    constexpr std::size_t block = 256;
    constexpr std::uint32_t publishes = 200'000;
    InMemoryValueFeed feed({
        {.name = "scope", .shape = ChannelShape::vector, .capacity = block},
        {.name = "level", .shape = ChannelShape::meter, .capacity = 2},
    });

    std::atomic<bool> done{false};
    std::thread producer([&] {
        std::vector<float> samples(block);
        for (std::uint32_t i = 1; i <= publishes; ++i) {
            const auto v = static_cast<float>(i);
            for (auto& s : samples)
                s = v;
            feed.publish_vector(0, samples);
            const std::array<float, 2> rms{v, v}, peak{v, v};
            feed.publish_meter(1, rms, peak);
        }
        done.store(true, std::memory_order_release);
    });

    std::vector<float> out(block);
    float last = 0.0f;
    float last_meter = 0.0f;
    std::uint64_t reads = 0;
    std::uint64_t torn = 0;
    std::uint64_t backwards = 0;
    std::uint32_t last_count = 0;
    std::uint64_t count_backwards = 0;
    for (;;) {
        const bool finished = done.load(std::memory_order_acquire);
        const std::uint32_t count = feed.publish_count(0);
        if (count < last_count)
            ++count_backwards;
        last_count = count;
        if (feed.read_vector(0, out) == block) {
            ++reads;
            for (float s : out)
                torn += s != out[0];
            backwards += out[0] < last;
            last = out[0];
        }
        std::array<float, 2> rms{}, peak{};
        if (feed.read_meter(1, rms, peak) == 2) {
            torn += rms[0] != rms[1] || rms[0] != peak[0] || peak[0] != peak[1];
            backwards += rms[0] < last_meter;
            last_meter = rms[0];
        }
        if (finished)
            break;
    }
    producer.join();

    std::fprintf(stderr, "hammer: %llu reads, %llu torn, %llu backwards\n",
                 static_cast<unsigned long long>(reads), static_cast<unsigned long long>(torn),
                 static_cast<unsigned long long>(backwards));
    CHECK(reads > 0);
    CHECK(torn == 0);
    CHECK(backwards == 0);
    CHECK(count_backwards == 0);
    // After the producer stops, the reader sees the very last publish.
    CHECK(feed.read_vector(0, out) == block);
    CHECK(out[0] == static_cast<float>(publishes));
    CHECK(feed.publish_count(0) == publishes);
    CHECK(feed.publish_count(1) == publishes);
}

}  // namespace

int main() {
    channels_are_described_and_found();
    nothing_published_reads_empty();
    latest_wins();
    publish_truncates_to_capacity();
    publish_count_tells_stopped_from_steady();
    shape_mismatch_is_refused();
    construction_rejects_bad_specs();
    hammer_single_producer_single_reader();
    if (failures != 0) {
        std::fprintf(stderr, "%d check(s) failed\n", failures);
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
