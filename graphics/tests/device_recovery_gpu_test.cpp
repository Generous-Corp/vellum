#include "../src/skia_dawn_surface_testing.hpp"
#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/layer_cache.hpp>
#include <vellum/graphics/render_telemetry.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <type_traits>
#include <vector>

namespace {

// `&SkiaDawnSurface::create` must name one function, as installed consumers
// take its address.
static_assert(std::is_same_v<decltype(&vellum::graphics::SkiaDawnSurface::create),
                             std::unique_ptr<vellum::graphics::SkiaDawnSurface> (*)(
                                 const vellum::graphics::SkiaDawnSurface::Config&,
                                 std::string*)>);

using namespace vellum::graphics;
namespace harness = vellum::graphics::capture_harness;
namespace hooks = vellum::graphics::testing;

constexpr std::uint32_t kWidth = 320;
constexpr std::uint32_t kHeight = 240;
constexpr float kScale = 2.0F;

int failures = 0;

void expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
}

std::chrono::steady_clock::time_point fake_now;

std::chrono::steady_clock::time_point fake_clock() { return fake_now; }

std::string describe(const FrameStats& stats) {
    std::ostringstream out;
    out << "frame=" << stats.frame << " full=" << stats.full_repaint
        << " reason=" << to_string(stats.full_repaint_reason)
        << " losses=" << stats.device_losses << " recoveries=" << stats.device_recoveries;
    return out.str();
}

std::string describe(const DeviceRecoveryStats& stats) {
    std::ostringstream out;
    out << "generation=" << stats.generation << " losses=" << stats.losses
        << " recreations=" << stats.recreations << " failed=" << stats.failed_attempts
        << " terminal=" << stats.terminal;
    return out.str();
}

std::string describe(const LayerCacheStats& stats) {
    std::ostringstream out;
    out << "layers=" << stats.layers << " builds=" << stats.builds << " hits=" << stats.hits
        << " device_lost=" << stats.invalidated(LayerInvalidation::device_lost);
    return out.str();
}

SceneNode* find_mutable(SceneNode& node, const std::string& id) {
    if (node.id == id) return &node;
    for (auto& child : node.children) {
        if (auto* found = find_mutable(child, id); found != nullptr) return found;
    }
    return nullptr;
}

SceneNode& node(Scene& scene, const std::string& id) {
    auto* found = find_mutable(scene.root, id);
    if (found == nullptr) throw std::runtime_error("fixture node missing: " + id);
    return *found;
}

// Rectangles, a mesh and text from the combined fixture, a node that moves,
// and a faded group the default layer policy caches.
Scene scene_for(float width, float height) {
    Scene scene = harness::fixtures::combined(width, height);
    scene.scene_version = kDamageModelSceneVersion;
    scene.root.children.push_back({
        .id = "mover",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {width * 0.08F, height * 0.62F, 36.0F, 24.0F},
        .fill = Color::hex(0xF472B6),
        .fill_gradients = {{.angle_degrees = 90.0F,
                            .stops = {{.position = 0.0F, .color = Color::hex(0xF472B6)},
                                      {.position = 1.0F, .color = Color::hex(0x6366F1)}}}},
        .corner_radius = 5.0F,
    });
    SceneNode fade{.id = "fade", .bounds = {width * 0.55F, height * 0.60F, 120.0F, 48.0F},
                   .opacity = 0.6F};
    fade.children.push_back({
        .id = "fade/bg",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {0.0F, 0.0F, 120.0F, 48.0F},
        .fill = Color::hex(0x22C55E),
        .box_shadows = {{.offset_y = 3.0F, .blur_radius = 6.0F,
                         .color = Color::rgba(0.0F, 0.0F, 0.0F, 0.5F)}},
        .corner_radius = 10.0F,
    });
    fade.children.push_back({
        .id = "fade/label",
        .kind = SceneNode::Kind::text,
        .bounds = {10.0F, 14.0F, 100.0F, 20.0F},
        .text_runs = {{.text = "Recovered",
                       .style = {.font_family = "Inter", .font_size = 14.0F,
                                 .color = Color::hex(0x111827)}}},
    });
    scene.root.children.push_back(std::move(fade));
    return scene;
}

Scene base_scene() {
    return scene_for(static_cast<float>(kWidth), static_cast<float>(kHeight));
}

std::unique_ptr<SkiaDawnSurface> make_surface(std::shared_ptr<SkiaDawnDevice> device = nullptr) {
    std::string error;
    auto surface = SkiaDawnSurface::create_with_device(
        {.width = kWidth, .height = kHeight, .scale = kScale}, std::move(device), &error);
    expect(surface != nullptr, "offscreen surface is created", error);
    return surface;
}

std::shared_ptr<SkiaDawnDevice> make_device() {
    std::string error;
    auto device = SkiaDawnDevice::create(&error);
    expect(device != nullptr, "shared device is created", error);
    return device;
}

struct Capture final {
    bool ok = false;
    std::string error;
    harness::CaptureResult result;
};

Capture capture(SkiaDawnSurface& surface) {
    Capture out;
    out.result.evidence = surface.evidence();
    out.ok = surface.capture_rgba(out.result.rgba, out.result.pixel_width,
                                  out.result.pixel_height, &out.error);
    out.result.ok = out.ok;
    return out;
}

// What a fresh surface draws for `scene` with a full repaint.
Capture fresh_full(const Scene& scene) {
    auto fresh = make_surface();
    if (!fresh) return {};
    std::string error;
    if (!fresh->render(scene, &error)) {
        expect(false, "a fresh surface renders", error);
        return {};
    }
    return capture(*fresh);
}

void expect_matches_fresh(SkiaDawnSurface& surface, const Scene& scene, const std::string& what) {
    const auto actual = capture(surface);
    const auto expected = fresh_full(scene);
    if (!actual.ok || !expected.ok) {
        expect(false, what + ": captures succeed", actual.error + expected.error);
        return;
    }
    const auto verdict = harness::assert_identical(expected.result, actual.result);
    expect(static_cast<bool>(verdict), what, verdict.message);
}

bool render(SkiaDawnSurface& surface, const Scene& scene, const std::string& what) {
    std::string error;
    const bool ok = surface.render_incremental(scene, &error);
    expect(ok, what, error);
    return ok;
}

// A loss fails captures and `finish` until a render recreates the device;
// that render repaints the whole frame, byte-identical to a fresh surface, and
// rebuilds the caches and layer images the loss dropped.
void recovery_repaints_and_rebuilds() {
    auto surface = make_surface();
    if (!surface) return;
    const Scene scene = base_scene();
    for (int frame = 0; frame < 3; ++frame) {
        if (!render(*surface, scene, "a warm-up frame renders")) return;
    }
    const auto warm = surface->telemetry();
    const auto warm_layers = surface->layer_cache_stats();
    expect(warm_layers.layers == 1U && warm.paragraphs.entries > 0U,
           "the warm surface caches the faded group and its text", describe(warm_layers));

    surface->simulate_device_loss();
    std::string error;
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    expect(!surface->capture_rgba(rgba, width, height, &error) &&
               error.find("lost") != std::string::npos && rgba.empty(),
           "a capture before recovery fails closed", error);
    error.clear();
    expect(!surface->finish(&error) && error.find("lost") != std::string::npos,
           "finish before recovery reports the loss", error);
    const auto dropped = surface->telemetry();
    const auto dropped_layers = surface->layer_cache_stats();
    expect(dropped.paragraphs.entries == 0U && dropped.gradients.entries == 0U &&
               dropped.textures.entries == 0U && dropped.last_trim == CacheTrimReason::device_lost &&
               dropped.device_losses == 1U && dropped.device_recoveries == 0U,
           "the loss drops every cache and is counted once", to_json(dropped));
    expect(dropped_layers.layers == 0U &&
               dropped_layers.invalidated(LayerInvalidation::device_lost) == warm_layers.layers,
           "the loss drops every layer image", describe(dropped_layers));

    if (!render(*surface, scene, "the next render recovers")) return;
    const auto stats = surface->frame_stats();
    expect(stats.full_repaint && stats.full_repaint_reason == FullRepaintReason::device_recovered &&
               stats.device_losses == 1U && stats.device_recoveries == 1U,
           "the recovery frame repaints in full and counts one loss and one recovery",
           describe(stats));
    const auto rebuilt = surface->telemetry();
    const auto rebuilt_layers = surface->layer_cache_stats();
    expect(rebuilt.paragraphs.entries == warm.paragraphs.entries &&
               rebuilt.gradients.entries == warm.gradients.entries &&
               rebuilt.textures.entries == warm.textures.entries &&
               rebuilt.device_losses == 1U && rebuilt.device_recoveries == 1U &&
               rebuilt.full_repaint_reason == "device_recovered",
           "the recovery frame rebuilds the caches", to_json(rebuilt));
    expect(rebuilt_layers.layers == warm_layers.layers && rebuilt_layers.builds == 1U,
           "the recovery frame rebuilds the layer image", describe(rebuilt_layers));
    expect_matches_fresh(*surface, scene,
                         "the recovered frame is byte-identical to a fresh full redraw");
    error.clear();
    expect(surface->finish(&error), "finish succeeds after recovery", error);
}

// Damage tracking restarts with the recovery frame, so the frame after it is
// a partial redraw again and matches a full redraw of its scene.
void partial_redraw_after_recovery() {
    auto surface = make_surface();
    if (!surface) return;
    Scene scene = base_scene();
    if (!render(*surface, scene, "the first frame renders")) return;
    node(scene, "mover").bounds.x += 11.0F;
    if (!render(*surface, scene, "a partial frame renders")) return;
    expect(!surface->frame_stats().full_repaint, "a frame before the loss is partial",
           describe(surface->frame_stats()));

    surface->simulate_device_loss();
    node(scene, "mover").bounds.y -= 7.0F;
    if (!render(*surface, scene, "the frame after the loss recovers")) return;
    expect(surface->frame_stats().full_repaint_reason == FullRepaintReason::device_recovered,
           "the recovery frame repaints in full", describe(surface->frame_stats()));

    for (int step = 1; step <= 3; ++step) {
        node(scene, "mover").bounds.x += 9.5F;
        node(scene, "fade/bg").fill = step % 2 == 0 ? Color::hex(0x22C55E) : Color::hex(0x3B82F6);
        if (!render(*surface, scene, "an incremental frame after recovery renders")) return;
        const auto stats = surface->frame_stats();
        expect(!stats.full_repaint && stats.damage_area_pixels < stats.frame_area_pixels,
               "incremental frame " + std::to_string(step) + " after recovery is partial",
               describe(stats));
        expect_matches_fresh(*surface, scene,
                             "incremental frame " + std::to_string(step) +
                                 " after recovery is byte-identical to a full redraw");
    }
}

// Two surfaces on one device: the loss is counted once, the first surface to
// render creates the one replacement, and the second adopts it.
void shared_device_recovers_once() {
    auto device = make_device();
    if (!device) return;
    auto first = make_surface(device);
    auto second = make_surface(device);
    if (!first || !second) return;
    Scene left = base_scene();
    Scene right = base_scene();
    node(right, "mover").fill = Color::hex(0xFACC15);
    if (!render(*first, left, "the first surface renders") ||
        !render(*second, right, "the second surface renders")) {
        return;
    }
    expect(device->recovery_stats().generation == 1U, "both surfaces share the first device",
           describe(device->recovery_stats()));

    first->simulate_device_loss();
    std::string error;
    expect(!second->finish(&error) && error.find("lost") != std::string::npos,
           "the sibling sees the shared loss", error);
    if (!render(*first, left, "the first surface recovers")) return;
    const auto after_first = device->recovery_stats();
    expect(after_first.generation == 2U && after_first.recreations == 1U &&
               after_first.losses == 1U,
           "the first surface creates one replacement device", describe(after_first));
    error.clear();
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    expect(!second->capture_rgba(rgba, width, height, &error) && rgba.empty(),
           "the sibling does not read back from the lost device", error);

    if (!render(*second, right, "the second surface recovers")) return;
    const auto after_second = device->recovery_stats();
    expect(after_second.generation == 2U && after_second.recreations == 1U &&
               after_second.losses == 1U && after_second.failed_attempts == 0U,
           "the second surface adopts the replacement without another", describe(after_second));
    for (auto* surface : {first.get(), second.get()}) {
        const auto stats = surface->frame_stats();
        expect(stats.device_losses == 1U && stats.device_recoveries == 1U &&
                   stats.full_repaint_reason == FullRepaintReason::device_recovered,
               "each surface counts one loss and one recovery", describe(stats));
    }
    expect_matches_fresh(*first, left, "the first surface draws its scene after recovery");
    expect_matches_fresh(*second, right, "the second surface draws its scene after recovery");

    for (int frame = 0; frame < 3; ++frame) {
        if (!render(*first, left, "the first surface keeps rendering") ||
            !render(*second, right, "the second surface keeps rendering")) {
            return;
        }
    }
    expect(device->recovery_stats().recreations == 1U,
           "later frames create no further device", describe(device->recovery_stats()));
}

// Failed recreations are retried no sooner than a doubling backoff allows and
// at most once per render, then recovery gives up for good.
void bounded_retries() {
    hooks::set_recovery_clock(&fake_clock);
    fake_now = std::chrono::steady_clock::time_point{} + std::chrono::hours(1);
    auto device = make_device();
    auto surface = device ? make_surface(device) : nullptr;
    if (!surface) {
        hooks::set_recovery_clock(nullptr);
        return;
    }
    const Scene scene = base_scene();
    if (!render(*surface, scene, "the frame before the loss renders")) {
        hooks::set_recovery_clock(nullptr);
        return;
    }
    hooks::fail_device_recreations(1000U);
    surface->simulate_device_loss();

    std::string error;
    expect(!surface->render_incremental(scene, &error) &&
               error.find("recreating it failed") != std::string::npos,
           "the first attempt runs on the next render and fails", error);
    expect(device->recovery_stats().failed_attempts == 1U, "one attempt so far",
           describe(device->recovery_stats()));
    for (int frame = 0; frame < 100; ++frame) {
        (void)surface->render_incremental(scene, nullptr);
    }
    expect(device->recovery_stats().failed_attempts == 1U,
           "renders inside the backoff make no attempt", describe(device->recovery_stats()));

    auto wait = hooks::kDeviceRecoveryFirstBackoff;
    for (std::uint32_t attempt = 2; attempt <= hooks::kDeviceRecoveryAttempts; ++attempt) {
        fake_now += wait - std::chrono::milliseconds(1);
        error.clear();
        expect(!surface->render_incremental(scene, &error) &&
                   error.find("next recreation attempt") != std::string::npos &&
                   device->recovery_stats().failed_attempts == attempt - 1U,
               "attempt " + std::to_string(attempt) + " waits " +
                   std::to_string(wait.count()) + " ms",
               error);
        fake_now += std::chrono::milliseconds(1);
        (void)surface->render_incremental(scene, nullptr);
        (void)surface->render_incremental(scene, nullptr);
        expect(device->recovery_stats().failed_attempts == attempt,
               "attempt " + std::to_string(attempt) + " runs once its backoff passes",
               describe(device->recovery_stats()));
        wait = std::min(wait * 2, hooks::kDeviceRecoveryMaxBackoff);
    }
    const auto gave_up = device->recovery_stats();
    expect(gave_up.terminal && gave_up.failed_attempts == hooks::kDeviceRecoveryAttempts &&
               gave_up.recreations == 0U,
           "recovery gives up after its last attempt", describe(gave_up));
    fake_now += std::chrono::hours(1);
    error.clear();
    expect(!surface->render_incremental(scene, &error) &&
               error.find("could not be recreated") != std::string::npos,
           "a render after giving up reports the terminal failure", error);
    for (int frame = 0; frame < 20; ++frame) {
        fake_now += std::chrono::hours(1);
        (void)surface->render_incremental(scene, nullptr);
    }
    expect(device->recovery_stats().failed_attempts == hooks::kDeviceRecoveryAttempts,
           "no attempt follows the terminal failure", describe(device->recovery_stats()));
    hooks::fail_device_recreations(0U);
    error.clear();
    expect(!surface->render_incremental(scene, &error) && !surface->finish(&error),
           "the terminal failure stands even once recreation would succeed", error);

    auto recovering = make_device();
    auto later = recovering ? make_surface(recovering) : nullptr;
    if (later && render(*later, scene, "a surface on a second device renders")) {
        hooks::fail_device_recreations(2U);
        later->simulate_device_loss();
        (void)later->render_incremental(scene, nullptr);
        fake_now += hooks::kDeviceRecoveryFirstBackoff;
        (void)later->render_incremental(scene, nullptr);
        fake_now += hooks::kDeviceRecoveryFirstBackoff * 2;
        if (render(*later, scene, "the third attempt recovers")) {
            const auto stats = recovering->recovery_stats();
            expect(stats.recreations == 1U && stats.failed_attempts == 2U && !stats.terminal,
                   "two failures then a recreation", describe(stats));
            later->simulate_device_loss();
            expect(render(*later, scene, "a later loss recovers on the next render") &&
                       recovering->recovery_stats().recreations == 2U,
                   "a frame on the new device restarts the attempts",
                   describe(recovering->recovery_stats()));
        }
    }
    hooks::fail_device_recreations(0U);
    hooks::set_recovery_clock(nullptr);
}

// A replacement lost as soon as it is created, before it is installed, is
// counted as a failed attempt rather than handed to the surface, and devices
// that keep failing that way still end recovery after the attempt limit.
void replacement_lost_on_creation() {
    hooks::set_recovery_clock(&fake_clock);
    fake_now = std::chrono::steady_clock::time_point{} + std::chrono::hours(100);
    auto device = make_device();
    auto surface = device ? make_surface(device) : nullptr;
    const Scene scene = base_scene();
    if (!surface || !render(*surface, scene, "the frame before the loss renders")) {
        hooks::set_recovery_clock(nullptr);
        return;
    }
    hooks::lose_recreated_devices(1U);
    surface->simulate_device_loss();
    std::string error;
    expect(!surface->render_incremental(scene, &error) &&
               error.find("lost as soon as it was created") != std::string::npos,
           "a replacement lost on creation is not drawn on", error);
    const auto lost = device->recovery_stats();
    expect(lost.generation == 2U && lost.losses == 2U && lost.recreations == 0U &&
               lost.failed_attempts == 1U,
           "the lost replacement counts as a loss and a failed attempt", describe(lost));
    fake_now += hooks::kDeviceRecoveryFirstBackoff;
    if (render(*surface, scene, "the next attempt after the backoff recovers")) {
        const auto stats = device->recovery_stats();
        expect(stats.generation == 3U && stats.recreations == 1U && !stats.terminal,
               "the second replacement is installed", describe(stats));
        expect_matches_fresh(*surface, scene, "the surface draws on the second replacement");
    }

    hooks::lose_recreated_devices(1000U);
    surface->simulate_device_loss();
    for (std::uint32_t attempt = 0; attempt < hooks::kDeviceRecoveryAttempts + 4U; ++attempt) {
        (void)surface->render_incremental(scene, nullptr);
        fake_now += std::chrono::hours(1);
    }
    const auto ended = device->recovery_stats();
    expect(ended.terminal && ended.recreations == 1U &&
               ended.failed_attempts == 1U + hooks::kDeviceRecoveryAttempts,
           "replacements that are always lost end recovery after the attempt limit",
           describe(ended));
    hooks::lose_recreated_devices(0U);
    hooks::set_recovery_clock(nullptr);
}

// A surface that cannot rebuild on the replacement leaves the shared device
// alone: its sibling adopts the replacement and no further device is made.
// The failing surface retries on its own backoff and gives up after the
// attempt limit, while its sibling keeps drawing.
void failed_rebuild_keeps_the_shared_device() {
    hooks::set_recovery_clock(&fake_clock);
    fake_now = std::chrono::steady_clock::time_point{} + std::chrono::hours(1000);
    auto device = make_device();
    auto first = device ? make_surface(device) : nullptr;
    auto second = device ? make_surface(device) : nullptr;
    const Scene scene = base_scene();
    if (!first || !second || !render(*first, scene, "the first surface renders") ||
        !render(*second, scene, "the second surface renders")) {
        hooks::set_recovery_clock(nullptr);
        return;
    }
    first->simulate_device_loss();
    hooks::fail_surface_rebuilds(1U);
    std::string error;
    expect(!first->render_incremental(scene, &error) &&
               error.find("rebuild failed") != std::string::npos,
           "the first surface fails to rebuild", error);
    expect(device->recovery_stats().recreations == 1U, "one replacement device is created",
           describe(device->recovery_stats()));
    if (!render(*second, scene, "the sibling adopts the replacement")) {
        hooks::set_recovery_clock(nullptr);
        return;
    }
    error.clear();
    expect(!first->render_incremental(scene, &error) &&
               error.find("next rebuild attempt") != std::string::npos,
           "the first surface waits out its rebuild backoff", error);
    fake_now += hooks::kDeviceRecoveryFirstBackoff;
    if (!render(*first, scene, "the first surface rebuilds after its backoff")) {
        hooks::set_recovery_clock(nullptr);
        return;
    }
    const auto stats = device->recovery_stats();
    expect(stats.generation == 2U && stats.recreations == 1U && stats.losses == 1U &&
               stats.failed_attempts == 0U,
           "the failed rebuild did not cost the sibling its device", describe(stats));
    expect_matches_fresh(*first, scene, "the first surface draws after its rebuild");
    expect_matches_fresh(*second, scene, "the sibling draws on the replacement");

    second->simulate_device_loss();
    if (!render(*second, scene, "the sibling recovers a second loss")) {
        hooks::set_recovery_clock(nullptr);
        return;
    }
    hooks::fail_surface_rebuilds(1000U);
    std::vector<std::string> errors;
    for (int frame = 0; frame < 200; ++frame) {
        error.clear();
        (void)first->render_incremental(scene, &error);
        errors.push_back(error);
        if (!second->render_incremental(scene, &error)) {
            expect(false, "the sibling keeps drawing while the other surface cannot", error);
            break;
        }
        fake_now += std::chrono::milliseconds(20);
    }
    const auto attempts = std::count_if(errors.begin(), errors.end(), [](const auto& text) {
        return text.find("rebuild failed") != std::string::npos;
    });
    expect(attempts == static_cast<long>(hooks::kDeviceRecoveryAttempts),
           "rebuild attempts follow the backoff and stop at the limit",
           std::to_string(attempts) + " attempts in 200 renders");
    expect(errors.back().find("could not be rebuilt") != std::string::npos,
           "the surface reports that it gave up", errors.back());
    expect(device->recovery_stats().recreations == 2U && !device->recovery_stats().terminal,
           "the failing surface creates no device", describe(device->recovery_stats()));
    hooks::fail_surface_rebuilds(0U);
    hooks::set_recovery_clock(nullptr);
}

// A loss while a frame is submitted fails that frame, and the next render
// recovers with a full repaint.
void loss_during_a_frame() {
    auto surface = make_surface();
    const Scene scene = base_scene();
    if (!surface || !render(*surface, scene, "the frame before the loss renders")) return;
    const auto before = surface->frame_stats().frame;
    hooks::lose_device_during_frames(1U);
    std::string error;
    expect(!surface->render_incremental(scene, &error) &&
               error.find("lost while the frame was submitted") != std::string::npos &&
               surface->frame_stats().frame == before,
           "a frame whose device is lost during submission fails", error);
    if (!render(*surface, scene, "the next render recovers")) return;
    expect(surface->frame_stats().full_repaint_reason == FullRepaintReason::device_recovered,
           "the recovery frame repaints in full", describe(surface->frame_stats()));
    expect_matches_fresh(*surface, scene, "the frame after an in-flight loss is complete");
}

// The loss callback only records the loss, so a loss raised on another thread
// is recovered by the render thread, and a render on another thread does not
// recreate the device.
void only_the_render_thread_recreates() {
    auto device = make_device();
    auto surface = device ? make_surface(device) : nullptr;
    if (!surface) return;
    const Scene scene = base_scene();
    if (!render(*surface, scene, "the render thread draws a frame")) return;

    std::thread([&] { surface->simulate_device_loss(); }).join();
    expect(device->recovery_stats().recreations == 0U,
           "a loss raised on another thread recreates nothing",
           describe(device->recovery_stats()));
    std::string error;
    bool foreign = true;
    std::thread([&] { foreign = surface->render_incremental(scene, &error); }).join();
    expect(!foreign && error.find("render thread") != std::string::npos &&
               device->recovery_stats().recreations == 0U,
           "a render on another thread does not recreate the device", error);
    if (!render(*surface, scene, "the render thread recovers")) return;
    expect(device->recovery_stats().recreations == 1U && device->recovery_stats().losses == 1U,
           "the render thread creates the one replacement", describe(device->recovery_stats()));
    expect_matches_fresh(*surface, scene, "the render thread's recovered frame is complete");
}

}  // namespace

int main() {
    try {
        std::string error;
        if (!harness::prepare_device(&error)) {
            std::cerr << "FAIL Dawn bootstrap: " << error << '\n';
            return 1;
        }
        recovery_repaints_and_rebuilds();
        partial_redraw_after_recovery();
        shared_device_recovers_once();
        bounded_retries();
        replacement_lost_on_creation();
        failed_rebuild_keeps_the_shared_device();
        loss_during_a_frame();
        only_the_render_thread_recreates();
    } catch (const std::exception& exception) {
        std::cout << "FAIL " << exception.what() << '\n';
        return 1;
    }
    std::cout << "device recovery gpu " << (failures == 0 ? "passed" : "failed") << '\n';
    return failures == 0 ? 0 : 1;
}
