// The render unit's logic that needs no GPU device: when a presentable
// swapchain is reconfigured and when it counts as settled, the idle-trim
// schedule, and the RGBA buffer helpers.

#include "framework_test.hpp"

#include <vellum/render/gpu_surface.hpp>
#include <vellum/render/headless_surface.hpp>
#include <vellum/render/skia_surface.hpp>

#include <chrono>
#include <cstdint>
#include <string>

namespace {

using vellum::render::GpuIdleTrimSchedule;
using vellum::render::HeadlessSurface;
using vellum::render::SwapchainResizeState;

// Drives SwapchainResizeState the way a presentable surface's begin_frame()
// does and counts the configures a frame would issue.
struct FrameLoop {
    SwapchainResizeState state;
    int configures = 0;

    // Returns whether the frame has anything to present.
    bool frame() {
        if (!state.presentable())
            return false;
        const bool configured = state.needs_configure();
        if (configured) {
            ++configures;
            state.mark_configured();
        }
        state.note_frame(configured);
        return true;
    }
};

FRAMEWORK_TEST(resizes_before_one_frame_configure_once) {
    FrameLoop loop;
    loop.state.request(400, 300);
    CHECK(loop.frame());
    CHECK(loop.configures == 1);

    for (uint32_t i = 1; i <= 60; ++i)
        loop.state.request(400 + i, 300 + i);
    CHECK(loop.configures == 1);
    CHECK(loop.state.width() == 460);
    CHECK(loop.state.height() == 360);

    CHECK(loop.frame());
    CHECK(loop.configures == 2);
    CHECK(loop.frame());
    CHECK(loop.configures == 2);
}

FRAMEWORK_TEST(a_resize_to_the_configured_size_configures_nothing) {
    FrameLoop loop;
    loop.state.request(400, 300);
    CHECK(loop.frame());
    const int before = loop.configures;

    loop.state.request(400, 300);
    CHECK(loop.frame());
    CHECK(loop.configures == before);

    // A drag that returns to where it started before the frame is also free.
    loop.state.request(800, 600);
    loop.state.request(400, 300);
    CHECK(loop.frame());
    CHECK(loop.configures == before);
}

FRAMEWORK_TEST(a_zero_size_presents_and_configures_nothing) {
    FrameLoop loop;
    loop.state.request(400, 300);
    CHECK(loop.frame());
    const int before = loop.configures;

    loop.state.request(0, 0);
    CHECK(!loop.frame());
    loop.state.request(400, 0);
    CHECK(!loop.frame());
    CHECK(loop.configures == before);

    // Restored to its old size: the swapchain was never rebuilt.
    loop.state.request(400, 300);
    CHECK(loop.frame());
    CHECK(loop.configures == before);

    // Restored to a new size: one configure.
    loop.state.request(0, 0);
    CHECK(!loop.frame());
    loop.state.request(640, 480);
    CHECK(loop.frame());
    CHECK(loop.configures == before + 1);
}

FRAMEWORK_TEST(a_swapchain_that_starts_at_zero_configures_on_its_first_sized_frame) {
    FrameLoop loop;
    CHECK(!loop.frame());
    CHECK(loop.configures == 0);
    loop.state.request(320, 240);
    CHECK(loop.frame());
    CHECK(loop.configures == 1);
}

FRAMEWORK_TEST(an_invalidated_swapchain_configures_again_at_an_unchanged_size) {
    FrameLoop loop;
    loop.state.request(400, 300);
    CHECK(loop.frame());
    loop.state.invalidate();
    CHECK(loop.frame());
    CHECK(loop.configures == 2);
}

FRAMEWORK_TEST(a_swapchain_settles_after_two_frames_at_an_unchanged_size) {
    FrameLoop loop;
    loop.state.request(400, 300);
    CHECK(!loop.state.settled());
    CHECK(loop.frame()); // configures
    CHECK(!loop.state.settled());
    CHECK(loop.frame());
    CHECK(!loop.state.settled());
    CHECK(loop.frame());
    CHECK(loop.state.settled());
    CHECK(loop.configures == 1);
}

FRAMEWORK_TEST(a_swapchain_is_not_settled_while_a_resize_is_pending_or_a_drag_continues) {
    FrameLoop loop;
    loop.state.request(400, 300);
    for (int i = 0; i < 3; ++i)
        loop.frame();
    CHECK(loop.state.settled());

    loop.state.request(410, 300);
    CHECK(!loop.state.settled()); // pending, before any frame

    // A drag: every frame configures, so it never settles.
    for (uint32_t i = 0; i < 8; ++i) {
        loop.state.request(420 + i, 300);
        CHECK(loop.frame());
        CHECK(!loop.state.settled());
    }

    // Released: two frames at the final size settle it, at exactly that size.
    CHECK(loop.frame());
    CHECK(loop.frame());
    CHECK(loop.state.settled());
    CHECK(loop.state.width() == 427);
    CHECK(loop.state.height() == 300);
}

FRAMEWORK_TEST(a_swapchain_at_zero_size_never_settles) {
    FrameLoop loop;
    loop.state.request(400, 300);
    for (int i = 0; i < 3; ++i)
        loop.frame();
    loop.state.request(0, 0);
    for (int i = 0; i < 3; ++i)
        CHECK(!loop.frame());
    CHECK(!loop.state.settled());
}

using Clock = GpuIdleTrimSchedule::Clock;

FRAMEWORK_TEST(the_idle_trim_is_due_once_per_interval) {
    const Clock::time_point t0{std::chrono::seconds(100)};
    GpuIdleTrimSchedule schedule(t0);
    CHECK(!schedule.due(t0));
    CHECK(!schedule.due(t0 + GpuIdleTrimSchedule::kInterval - std::chrono::nanoseconds(1)));
    CHECK(schedule.due(t0 + GpuIdleTrimSchedule::kInterval));
    // The interval restarts at the trim, not at construction.
    CHECK(!schedule.due(t0 + GpuIdleTrimSchedule::kInterval + std::chrono::milliseconds(999)));
    CHECK(schedule.due(t0 + 2 * GpuIdleTrimSchedule::kInterval));
    CHECK(schedule.trims() == 2);
}

FRAMEWORK_TEST(nothing_is_trimmable_until_enough_frames_have_started) {
    using std::chrono::milliseconds;
    constexpr auto kFrames = GpuIdleTrimSchedule::kUnusedFrames;
    const Clock::time_point t0{std::chrono::seconds(100)};
    GpuIdleTrimSchedule schedule(t0);
    for (std::size_t i = 0; i + 1 < kFrames; ++i)
        schedule.note_frame(t0 + milliseconds(16) * static_cast<int>(i));
    CHECK(!schedule.unused_age(t0 + std::chrono::seconds(60)).has_value());
    schedule.note_frame(t0 + milliseconds(16) * static_cast<int>(kFrames));
    CHECK(schedule.unused_age(t0 + std::chrono::seconds(60)).has_value());
}

FRAMEWORK_TEST(at_frame_pace_the_time_bound_decides_the_unused_age) {
    using std::chrono::milliseconds;
    constexpr auto kFrames = GpuIdleTrimSchedule::kUnusedFrames;
    const Clock::time_point t0{std::chrono::seconds(100)};
    GpuIdleTrimSchedule schedule(t0);
    for (std::size_t i = 0; i < kFrames; ++i)
        schedule.note_frame(t0 + milliseconds(16) * static_cast<int>(i));
    const auto now = t0 + milliseconds(16) * static_cast<int>(kFrames);
    CHECK(schedule.unused_age(now) == GpuIdleTrimSchedule::kUnusedAge);
}

FRAMEWORK_TEST(slow_frames_push_the_unused_age_past_the_time_bound) {
    using std::chrono::milliseconds;
    constexpr auto kFrames = GpuIdleTrimSchedule::kUnusedFrames;
    const Clock::time_point t0{std::chrono::seconds(100)};
    GpuIdleTrimSchedule schedule(t0);
    for (std::size_t i = 0; i < kFrames; ++i)
        schedule.note_frame(t0 + milliseconds(200) * static_cast<int>(i));
    const auto now = t0 + milliseconds(200) * static_cast<int>(kFrames);
    // Back to the oldest of the last kUnusedFrames frame starts: t0.
    CHECK(schedule.unused_age(now) == milliseconds(200) * static_cast<int>(kFrames));
}

FRAMEWORK_TEST(an_idle_gap_between_bursts_does_not_age_the_last_bursts_resources) {
    using std::chrono::milliseconds;
    constexpr auto kFrames = GpuIdleTrimSchedule::kUnusedFrames;
    const Clock::time_point t0{std::chrono::seconds(100)};
    GpuIdleTrimSchedule schedule(t0);
    for (std::size_t i = 0; i < kFrames; ++i)
        schedule.note_frame(t0 + milliseconds(16) * static_cast<int>(i));
    // Ten idle seconds, then the first frame of the next burst.
    const auto resume = t0 + std::chrono::seconds(10);
    schedule.note_frame(resume);
    const auto age = schedule.unused_age(resume);
    CHECK(age.has_value());
    if (!age)
        return;
    // Something last drawn in the previous burst's final frame is younger than
    // the age, so the resume frame's trim keeps it.
    const auto last_burst_frame = t0 + milliseconds(16) * static_cast<int>(kFrames - 1);
    CHECK(resume - last_burst_frame < *age);
}

FRAMEWORK_TEST(encode_png_rejects_empty_and_undersized_input) {
    HeadlessSurface::Rgba empty;
    std::string err;
    auto png = HeadlessSurface::encode_png(empty, &err);
    CHECK(png.empty());
    CHECK(!err.empty());

    HeadlessSurface::Rgba bad;
    bad.width = 4;
    bad.height = 4;
    bad.pixels.assign(8, 0); // needs 64 bytes
    err.clear();
    png = HeadlessSurface::encode_png(bad, &err);
    CHECK(png.empty());
    CHECK(!err.empty());
}

FRAMEWORK_TEST(rgba_fingerprint_is_deterministic_and_sensitive_to_content_and_dimensions) {
    HeadlessSurface::Rgba a;
    a.width = 2;
    a.height = 3;
    a.pixels = {1,  2,  3,  4,  5,  6,  7,  8,  9,  10, 11, 12,
                13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24};

    HeadlessSurface::Rgba same = a;
    CHECK(HeadlessSurface::rgba_fingerprint(a) == HeadlessSurface::rgba_fingerprint(same));

    HeadlessSurface::Rgba changed = a;
    changed.pixels[0] ^= 0xFF;
    CHECK(HeadlessSurface::rgba_fingerprint(a) != HeadlessSurface::rgba_fingerprint(changed));

    HeadlessSurface::Rgba swapped;
    swapped.width = 3;
    swapped.height = 2;
    swapped.pixels = a.pixels;
    CHECK(HeadlessSurface::rgba_fingerprint(a) != HeadlessSurface::rgba_fingerprint(swapped));
}

} // namespace

int main() { return framework_test::run_all(); }
