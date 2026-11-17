// The frame-level render pass manager: pass ordering, per-frame resets, the
// time budget and the frame-level GPU render time. Header-only, so no device
// is created and this runs on every host that builds the render unit.

#include "framework_test.hpp"

#include <vellum/render/render_pass.hpp>

using namespace vellum::render;

FRAMEWORK_TEST(passes_are_recorded_in_submission_order_with_their_stats) {
    RenderPassManager manager;
    manager.begin_frame();
    manager.begin_pass(RenderPassType::background);
    manager.end_pass(1.0f, 3);
    manager.begin_pass(RenderPassType::content);
    manager.end_pass(2.5f, 40);
    manager.begin_pass(RenderPassType::overlay);
    manager.end_pass(0.5f, 2);
    manager.end_frame();

    const auto& passes = manager.passes();
    CHECK(passes.size() == 3);
    CHECK(passes[0].type == RenderPassType::background);
    CHECK(passes[1].type == RenderPassType::content);
    CHECK(passes[2].type == RenderPassType::overlay);
    CHECK(passes[1].draw_calls == 40);
    CHECK(passes[1].cpu_time_ms() == 2.5f);
    CHECK(manager.total_time_ms() == 4.0f);
    CHECK(manager.current_pass() == RenderPassType::overlay);
}

FRAMEWORK_TEST(begin_frame_clears_the_previous_frame_and_counts_frames) {
    RenderPassManager manager;
    manager.set_budget(1.0f);
    manager.begin_frame();
    manager.begin_pass(RenderPassType::effects);
    manager.end_pass(5.0f, 1);
    manager.end_frame();
    CHECK(manager.over_budget());
    CHECK(manager.frame_count() == 1);

    manager.begin_frame();
    CHECK(manager.passes().empty());
    CHECK(manager.total_time_ms() == 0.0f);
    CHECK(!manager.over_budget());
    CHECK(manager.current_pass() == RenderPassType::background);
    CHECK(manager.frame_count() == 2);
}

FRAMEWORK_TEST(the_budget_is_exceeded_only_when_total_time_passes_it) {
    RenderPassManager manager;
    CHECK(manager.budget() == 16.67f);
    manager.set_budget(10.0f);

    manager.begin_frame();
    manager.begin_pass(RenderPassType::content);
    manager.end_pass(10.0f, 1);
    manager.end_frame();
    CHECK(!manager.over_budget());

    manager.begin_frame();
    manager.begin_pass(RenderPassType::content);
    manager.end_pass(6.0f, 1);
    manager.begin_pass(RenderPassType::post_effects);
    manager.end_pass(4.5f, 1);
    manager.end_frame();
    CHECK(manager.over_budget());

    manager.set_budget(0.0f);
    manager.end_frame();
    CHECK(!manager.over_budget());
}

FRAMEWORK_TEST(end_pass_with_no_open_pass_changes_nothing) {
    RenderPassManager manager;
    manager.begin_frame();
    manager.end_pass(3.0f, 9);
    CHECK(manager.passes().empty());
    CHECK(manager.total_time_ms() == 0.0f);
}

FRAMEWORK_TEST(gpu_render_time_is_frame_level_and_invalid_samples_are_refused) {
    RenderPassManager manager;
    CHECK(!manager.gpu_render_timing_available());

    manager.begin_frame();
    manager.set_gpu_render_time_ms(3.25f, true);
    CHECK(manager.gpu_render_timing_available());
    CHECK(manager.gpu_render_time_ms() == 3.25f);

    // A new frame drops the sample's validity but keeps the last value.
    manager.begin_frame();
    CHECK(!manager.gpu_render_timing_available());
    CHECK(manager.gpu_render_time_ms() == 3.25f);

    // Neither an unavailable clock nor a negative duration is a sample.
    manager.set_gpu_render_time_ms(9.0f, false);
    CHECK(!manager.gpu_render_timing_available());
    manager.set_gpu_render_time_ms(-1.0f, true);
    CHECK(!manager.gpu_render_timing_available());
    CHECK(manager.gpu_render_time_ms() == 3.25f);

    manager.set_gpu_render_time_ms(0.0f, true);
    CHECK(manager.gpu_render_timing_available());
    CHECK(manager.gpu_render_time_ms() == 0.0f);
}

int main() { return framework_test::run_all(); }
