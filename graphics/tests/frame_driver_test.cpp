#include <vellum/graphics/animation.hpp>
#include <vellum/graphics/frame_driver.hpp>
#include <vellum/value/in_memory_value_feed.hpp>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {

using namespace vellum::graphics;

int failures = 0;

void check(bool ok, const std::string& what) {
    std::printf("%s %s\n", ok ? "PASS" : "FAIL", what.c_str());
    if (!ok) ++failures;
}

bool near(double value, double expected, double tolerance = 1.0e-6) {
    return std::fabs(value - expected) <= tolerance;
}

struct Recorder final {
    std::vector<Scene> scenes;
    bool operator()(const Scene& scene, std::string*) {
        scenes.push_back(scene);
        return true;
    }
};

FrameDriver make_driver(Recorder& recorder, FrameDriverOptions options = {}) {
    return FrameDriver([&recorder](const Scene& scene, std::string* error) {
        return recorder(scene, error);
    }, options);
}

SceneNode fade_node() {
    SceneNode node{.id = "fade", .kind = SceneNode::Kind::rectangle,
                   .bounds = {0.0F, 0.0F, 10.0F, 10.0F}, .fill = Color::hex(0xFFFFFF)};
    AnimationTrack track{.property = AnimatedProperty::opacity};
    track.keyframes.push_back({.time = 0.0, .value = 0.0F});
    track.keyframes.push_back({.time = 1.0, .value = 1.0F});
    node.tracks.push_back(std::move(track));
    return node;
}

Scene track_scene() {
    Scene scene;
    scene.width = 40.0F;
    scene.height = 40.0F;
    scene.root = {.id = "root", .kind = SceneNode::Kind::group, .bounds = {0, 0, 40, 40}};
    scene.root.children.push_back(fade_node());
    return scene;
}

SceneNode plot_node() {
    auto spec = std::make_shared<PlotSpec>();
    spec->style = PlotStyle::line;
    spec->channel = "level";
    spec->shape = PlotChannelShape::scalar;
    spec->slot_count = 4;
    spec->value_min = 0.0F;
    spec->value_max = 1.0F;
    SceneNode node{.id = "level-plot", .kind = SceneNode::Kind::plot,
                   .bounds = {0.0F, 0.0F, 40.0F, 20.0F}};
    node.plot = std::move(spec);
    return node;
}

Scene plot_scene() {
    Scene scene;
    scene.width = 40.0F;
    scene.height = 40.0F;
    scene.root = {.id = "root", .kind = SceneNode::Kind::group, .bounds = {0, 0, 40, 40}};
    scene.root.children.push_back(plot_node());
    return scene;
}

float opacity_of(const Scene& scene) {
    const Scene evaluated = evaluate_tracks(scene);
    return evaluated.root.children.at(0).opacity;
}

void tracks_run_then_settle() {
    Recorder recorder;
    auto driver = make_driver(recorder);
    Scene scene = track_scene();
    const double origin = 5000.0;
    const double step = 0.2;

    auto report = driver.frame(scene, origin);
    check(report.resumed && report.rendered && near(report.clock, 1.0 / 60.0),
          "the first frame is a resume that renders at one nominal frame");
    check(report.tracks_running && report.needs_next_frame,
          "a running track asks for the next frame");

    std::vector<double> clocks{report.clock};
    int guard = 0;
    double now = origin;
    while (report.needs_next_frame && ++guard < 100) {
        now += step;
        report = driver.frame(scene, now);
        if (report.rendered) clocks.push_back(report.clock);
    }
    const std::vector<double> expected{1.0 / 60.0, 1.0 / 60.0 + 0.2, 1.0 / 60.0 + 0.4,
                                       1.0 / 60.0 + 0.6, 1.0 / 60.0 + 0.8, 1.0 / 60.0 + 1.0};
    bool clocks_match = clocks.size() == expected.size();
    for (std::size_t i = 0; clocks_match && i < clocks.size(); ++i) {
        clocks_match = near(clocks[i], expected[i]);
    }
    check(clocks_match, "the clock follows the injected timestamps frame by frame");
    check(recorder.scenes.size() == expected.size(), "one render per frame while the track runs");

    bool evaluated_match = recorder.scenes.size() == expected.size();
    for (std::size_t i = 0; evaluated_match && i < expected.size(); ++i) {
        const double want = std::min(1.0, expected[i]);
        evaluated_match = near(opacity_of(recorder.scenes[i]), want, 1.0e-6) &&
                          near(recorder.scenes[i].clock, expected[i]);
    }
    check(evaluated_match, "each frame is handed to the renderer at its evaluated clock");
    check(near(opacity_of(recorder.scenes.back()), 1.0, 1.0e-6),
          "the last frame is the track's end state");
    check(!report.tracks_running && !report.needs_next_frame,
          "a settled track stops requesting frames");

    const std::size_t rendered = recorder.scenes.size();
    for (int i = 0; i < 5; ++i) {
        now += step;
        report = driver.frame(scene, now);
        check(!report.rendered && !report.needs_next_frame,
              "a tick after settling renders nothing and asks for nothing");
    }
    check(recorder.scenes.size() == rendered, "no frame is drawn while idle");
}

void idle_gap_is_not_animation_time() {
    Recorder recorder;
    auto driver = make_driver(recorder);
    Scene scene = track_scene();
    driver.frame(scene, 10.0);
    auto report = driver.frame(scene, 10.1);
    check(!report.resumed && near(report.dt, 0.1), "a short gap is measured raw");
    const double before = driver.clock();
    report = driver.frame(scene, 400.0);
    check(report.resumed && near(report.dt, 1.0 / 60.0) && near(driver.clock(), before + 1.0 / 60.0),
          "a gap over the wake threshold advances one nominal frame, with no catch-up");
    driver.suspend();
    const double suspended_at = driver.clock();
    report = driver.frame(scene, 400.01);
    check(report.resumed && near(driver.clock(), suspended_at + 1.0 / 60.0),
          "a frame after suspend is a resume however short the gap");
    const double now = driver.clock();
    const std::size_t rendered = recorder.scenes.size();
    report = driver.frame(scene, 400.01);
    check(near(report.dt, 0.0) && near(driver.clock(), now) && !report.rendered &&
              recorder.scenes.size() == rendered,
          "a repeated timestamp advances nothing and renders nothing");
    report = driver.frame(scene, 400.0);
    check(near(report.dt, 0.0) && near(driver.clock(), now),
          "a timestamp that goes backwards never rewinds the clock");
}

void reduced_motion_jumps_to_the_end() {
    Recorder recorder;
    FrameDriverOptions options;
    options.reduced_motion = true;
    auto driver = make_driver(recorder, options);
    Scene scene = track_scene();
    const auto report = driver.frame(scene, 1.0);
    check(recorder.scenes.size() == 1 && near(opacity_of(recorder.scenes[0]), 1.0, 1.0e-9),
          "reduced motion draws the end state on the first frame");
    check(near(report.clock, 1.0) && !report.tracks_running && !report.needs_next_frame,
          "reduced motion settles at once and asks for no more frames");

    Recorder full;
    auto animating = make_driver(full);
    Scene second = track_scene();
    animating.frame(second, 1.0);
    animating.set_reduced_motion(true);
    animating.frame(second, 1.1);
    check(near(full.scenes.back().clock, 1.0) && !animating.frame(second, 1.2).rendered,
          "turning reduced motion on mid-animation jumps to the end and stays there");
}

void request_renders_and_wakes() {
    Recorder recorder;
    auto driver = make_driver(recorder);
    int wakes = 0;
    driver.set_wake([&wakes] { ++wakes; });
    Scene scene = plot_scene();
    driver.frame(scene, 1.0);
    check(!driver.frame(scene, 1.016).rendered, "a settled scene renders nothing");
    driver.request_frame();
    driver.request_frame();
    driver.request_frame();
    check(wakes == 1 && driver.frame_requested(),
          "requests made before the frame runs wake the host once");
    const auto report = driver.frame(scene, 1.032);
    check(report.rendered && !driver.frame_requested() && !report.needs_next_frame,
          "a request renders exactly one frame");
    driver.request_frame();
    check(wakes == 2, "a request after the frame ran wakes the host again");
}

void feed_publish_is_one_sample_one_frame() {
    using namespace vellum::value;
    InMemoryValueFeed feed({{.name = "level", .shape = ChannelShape::scalar, .neutral = 0.0F},
                            {.name = "other", .shape = ChannelShape::scalar, .neutral = 0.0F}});
    Recorder recorder;
    FrameDriverOptions options;
    options.feed.stale_after_ticks = 3;
    auto driver = make_driver(recorder, options);
    driver.set_feed(&feed);
    Scene scene = plot_scene();

    auto report = driver.frame(scene, 1.0);
    check(report.rendered && report.sampled, "the first frame renders and samples");
    double now = 1.0;
    for (int i = 0; i < 3; ++i) {
        now += 0.016;
        report = driver.frame(scene, now);
        check(!report.rendered && !report.needs_next_frame && !report.feed_active,
              "with no publish there is no frame and no request");
    }
    const std::size_t before = recorder.scenes.size();

    feed.publish_scalar(0, 0.75F);
    now += 0.016;
    report = driver.frame(scene, now);
    const auto* plot = scene.root.children.at(0).plot.get();
    check(report.rendered && recorder.scenes.size() == before + 1 &&
              report.feed.updated.size() == 1 && plot->samples.size() == 4 &&
              near(plot->samples[0], 0.75) && std::isnan(plot->samples[1]),
          "a publish produces exactly one sample and one frame");
    check(report.feed_active && report.needs_next_frame,
          "after a publish the driver keeps ticking until the channel can go stale");

    now += 0.016;
    report = driver.frame(scene, now);
    check(!report.rendered && report.needs_next_frame && std::isnan(plot->samples[1]),
          "a tick with no publish draws nothing new");
    now += 0.016;
    report = driver.frame(scene, now);
    check(!report.rendered && report.needs_next_frame, "still counting toward stale");
    now += 0.016;
    report = driver.frame(scene, now);
    plot = scene.root.children.at(0).plot.get();
    check(report.rendered && near(plot->samples[1], 0.0) && !report.needs_next_frame &&
              !report.feed_active,
          "the stale tick draws the neutral value, then the driver stops");
    now += 0.016;
    check(!driver.frame(scene, now).rendered, "a silent feed costs no further frames");

    feed.publish_scalar(0, 0.5F);
    now += 0.016;
    report = driver.frame(scene, now);
    check(report.rendered && report.feed.updated.size() == 1, "a later publish wakes it again");

    for (int i = 0; i < 8; ++i) {
        feed.publish_scalar(1, static_cast<float>(i));
        now += 0.016;
        report = driver.frame(scene, now);
        if (i >= 4) {
            check(!report.feed_active, "a channel no plot reads does not keep the driver ticking");
        }
    }
    check(!report.needs_next_frame, "a busy unrelated channel does not hold the display link open");
    feed.publish_scalar(0, 0.5F);
    now += 0.016;
    driver.frame(scene, now);

    const std::size_t rendered = recorder.scenes.size();
    feed.publish_scalar(0, 0.6F);
    feed.publish_scalar(0, 0.7F);
    now += 0.016;
    report = driver.frame(scene, now);
    check(report.rendered && recorder.scenes.size() == rendered + 1 &&
              near(scene.root.children.at(0).plot->samples[3], 0.7),
          "several publishes between ticks are one sample and one frame");
}

void a_replaced_scene_takes_the_driver_clock() {
    Recorder recorder;
    auto driver = make_driver(recorder);
    Scene scene = track_scene();
    driver.frame(scene, 1.0);
    driver.frame(scene, 1.2);
    Scene replaced = track_scene();
    driver.request_frame();
    const auto report = driver.frame(replaced, 1.4);
    check(report.rendered && near(replaced.clock, driver.clock()) && replaced.clock > 0.3,
          "a scene the host replaced is stamped with the running clock, not zero");
}

void render_failure_is_reported() {
    int calls = 0;
    FrameDriver driver([&calls](const Scene&, std::string* error) {
        ++calls;
        if (error != nullptr) *error = "device lost";
        return false;
    });
    Scene scene = plot_scene();
    const auto report = driver.frame(scene, 1.0);
    check(report.rendered && !report.render_ok && report.error == "device lost",
          "a failed render is reported with its error");
    check(report.needs_next_frame, "a failed frame asks for another tick");
    check(driver.frame(scene, 1.016).rendered && calls == 2,
          "a failed frame is retried on the next tick, not forgotten");
}

}  // namespace

int main() {
    tracks_run_then_settle();
    idle_gap_is_not_animation_time();
    reduced_motion_jumps_to_the_end();
    request_renders_and_wakes();
    feed_publish_is_one_sample_one_frame();
    a_replaced_scene_takes_the_driver_clock();
    render_failure_is_reported();
    std::printf("%d failure(s)\n", failures);
    return failures == 0 ? 0 : 1;
}
