#include "../src/skia_dawn_surface_test_access.hpp"
#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/frame_trace.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#import <QuartzCore/CAMetalLayer.h>

#include <algorithm>
#include <cctype>
#include <clocale>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace harness = vellum::graphics::capture_harness;

constexpr int kSkip = 77;
int failures = 0;

void expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
}

struct TraceEvent final {
    enum class Kind { begin, end, counter, instant };
    Kind kind = Kind::begin;
    std::string name;
    std::string detail;
    double value = 0.0;
};

class RecordingSink final {
public:
    RecordingSink() {
        sink_.slice_begin = [](const char* name, void* self) {
            static_cast<RecordingSink*>(self)->add({TraceEvent::Kind::begin, name, {}, 0.0});
        };
        sink_.slice_end = [](void* self) {
            static_cast<RecordingSink*>(self)->add({TraceEvent::Kind::end, {}, {}, 0.0});
        };
        sink_.counter = [](const char* name, double value, void* self) {
            static_cast<RecordingSink*>(self)->add({TraceEvent::Kind::counter, name, {}, value});
        };
        sink_.instant = [](const char* name, const char* detail, void* self) {
            static_cast<RecordingSink*>(self)->add(
                {TraceEvent::Kind::instant, name, detail != nullptr ? detail : "", 0.0});
        };
        sink_.context = this;
    }
    ~RecordingSink() { clear_frame_trace_sink(); }

    void install() { set_frame_trace_sink(sink_); }

    std::vector<TraceEvent> events() const {
        std::lock_guard lock(mutex_);
        return events_;
    }
    std::size_t size() const {
        std::lock_guard lock(mutex_);
        return events_.size();
    }
    void reset() {
        std::lock_guard lock(mutex_);
        events_.clear();
    }

private:
    void add(TraceEvent event) {
        std::lock_guard lock(mutex_);
        events_.push_back(std::move(event));
    }

    FrameTraceSink sink_;
    mutable std::mutex mutex_;
    std::vector<TraceEvent> events_;
};

std::string short_name(const std::string& name) {
    constexpr std::string_view prefix = "graphics.";
    return name.compare(0, prefix.size(), prefix) == 0 ? name.substr(prefix.size()) : name;
}

// The slices of `events` as nested text, `frame(record,submit(present_wait),present)`.
std::string slice_shape(const std::vector<TraceEvent>& events) {
    std::string out;
    bool first_in_scope = true;
    for (const auto& event : events) {
        if (event.kind == TraceEvent::Kind::begin) {
            if (!first_in_scope) out += ',';
            out += short_name(event.name);
            out += '(';
            first_in_scope = true;
        } else if (event.kind == TraceEvent::Kind::end) {
            if (!out.empty() && out.back() == '(') {
                out.pop_back();
            } else {
                out += ')';
            }
            first_in_scope = false;
        }
    }
    return out;
}

// The slice shape of the frame that begins at the `index`-th `graphics.frame`.
std::string frame_shape(const std::vector<TraceEvent>& events, std::size_t index) {
    std::size_t seen = 0;
    std::size_t begin = events.size();
    int depth = 0;
    for (std::size_t i = 0; i < events.size(); ++i) {
        if (events[i].kind == TraceEvent::Kind::begin && events[i].name == "graphics.frame" &&
            depth == 0) {
            if (seen++ == index) begin = i;
        }
        if (i >= begin && events[i].kind == TraceEvent::Kind::begin) ++depth;
        if (i >= begin && events[i].kind == TraceEvent::Kind::end) {
            if (--depth == 0) {
                return slice_shape({events.begin() + static_cast<std::ptrdiff_t>(begin),
                                    events.begin() + static_cast<std::ptrdiff_t>(i) + 1});
            }
        }
    }
    return {};
}

// Counters recorded inside the `index`-th frame slice.
std::vector<TraceEvent> frame_counters(const std::vector<TraceEvent>& events, std::size_t index) {
    std::vector<TraceEvent> out;
    std::size_t seen = 0;
    int depth = 0;
    bool inside = false;
    for (const auto& event : events) {
        if (event.kind == TraceEvent::Kind::begin) {
            if (depth == 0 && event.name == "graphics.frame") inside = seen++ == index;
            ++depth;
        } else if (event.kind == TraceEvent::Kind::end) {
            --depth;
            if (depth == 0) inside = false;
        } else if (inside) {
            out.push_back(event);
        }
    }
    return out;
}

std::optional<double> counter(const std::vector<TraceEvent>& events, const std::string& name) {
    for (const auto& event : events) {
        if (event.kind == TraceEvent::Kind::counter && event.name == name) return event.value;
    }
    return std::nullopt;
}

std::size_t count_named(const std::vector<TraceEvent>& events, TraceEvent::Kind kind,
                        const std::string& name) {
    return static_cast<std::size_t>(std::count_if(events.begin(), events.end(),
        [&](const TraceEvent& event) { return event.kind == kind && event.name == name; }));
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

Scene base_scene(float width, float height) {
    Scene scene = harness::fixtures::combined(width, height);
    scene.scene_version = kDamageModelSceneVersion;
    scene.root.children.push_back({
        .id = "mover",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {width * 0.08F, height * 0.62F, 36.0F, 24.0F},
        .fill = Color::hex(0xF472B6),
        .corner_radius = 5.0F,
    });
    scene.root.children.push_back({
        .id = "swatch",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {width * 0.80F, height * 0.80F, 28.0F, 28.0F},
        .fill = Color::hex(0x22C55E),
    });
    scene.root.children.push_back({
        .id = "label",
        .kind = SceneNode::Kind::text,
        .bounds = {width * 0.05F, height * 0.90F, width * 0.5F, 16.0F},
        .text_runs = {{.text = "Frame 1",
                       .style = {.font_family = "Inter", .font_size = 13.0F,
                                 .color = Color::hex(0xE5E7EB)}}},
    });
    return scene;
}

struct Frame final {
    const char* name;
    void (*edit)(Scene&);
};

// First frame (a full repaint), a partial move, a partial recolour.
const std::vector<Frame>& three_frames() {
    static const std::vector<Frame> frames = {
        {"first", [](Scene&) {}},
        {"move", [](Scene& scene) {
             node(scene, "mover").bounds.x += 41.0F;
             node(scene, "mover").bounds.y -= 30.0F;
         }},
        {"recolour", [](Scene& scene) { node(scene, "swatch").fill = Color::hex(0x3B82F6); }},
    };
    return frames;
}

constexpr std::uint32_t kWidth = 400;
constexpr std::uint32_t kHeight = 300;

std::unique_ptr<SkiaDawnSurface> offscreen(float scale, bool overlay = false) {
    std::string error;
    auto surface = SkiaDawnSurface::create(
        {.width = kWidth, .height = kHeight, .scale = scale, .damage_overlay = overlay}, &error);
    expect(surface != nullptr, "offscreen surface is created", error);
    return surface;
}

struct NativeSurface final {
    CAMetalLayer* layer = nil;
    std::unique_ptr<SkiaDawnSurface> surface;
};

NativeSurface native(float scale, bool overlay = false) {
    NativeSurface out;
    out.layer = [CAMetalLayer layer];
    out.layer.contentsScale = scale;
    out.layer.drawableSize = CGSizeMake(kWidth * scale, kHeight * scale);
    out.layer.frame = CGRectMake(0, 0, kWidth, kHeight);
    std::string error;
    out.surface = SkiaDawnSurface::create(
        {.width = kWidth, .height = kHeight, .scale = scale,
         .native_surface_handle = (__bridge void*)out.layer, .vsync = false,
         .damage_overlay = overlay},
        &error);
    if (out.surface && !out.surface->evidence().native_surface) out.surface.reset();
    return out;
}

struct Pixels final {
    bool ok = false;
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
};

Pixels capture(SkiaDawnSurface& surface) {
    Pixels out;
    std::string error;
    out.ok = surface.capture_rgba(out.rgba, out.width, out.height, &error);
    if (!out.ok) std::cout << "capture failed: " << error << '\n';
    return out;
}

bool same_bytes(const Pixels& a, const Pixels& b) {
    return a.ok && b.ok && a.width == b.width && a.height == b.height && a.rgba == b.rgba;
}

bool render_frames(SkiaDawnSurface& surface, Scene& scene, std::size_t count) {
    const auto& frames = three_frames();
    for (std::size_t i = 0; i < count; ++i) {
        frames[i].edit(scene);
        std::string error;
        if (!surface.render_incremental(scene, &error)) {
            expect(false, std::string("frame ") + frames[i].name + " renders", error);
            return false;
        }
    }
    return true;
}

bool near(double a, double b) { return std::abs(a - b) < 1e-9; }

void tracing_off_changes_nothing() {
    expect(!frame_trace_enabled(), "no sink is installed by default");
    RecordingSink recorder;

    Scene scene_off = base_scene(kWidth, kHeight);
    auto off = offscreen(1.0F);
    if (!off || !render_frames(*off, scene_off, 3)) return;
    const auto bytes_off = capture(*off);
    expect(recorder.size() == 0, "tracing off produces no events");

    recorder.install();
    expect(frame_trace_enabled(), "installing a sink enables tracing");
    Scene scene_on = base_scene(kWidth, kHeight);
    auto on = offscreen(1.0F);
    if (!on || !render_frames(*on, scene_on, 3)) return;
    const auto bytes_on = capture(*on);
    expect(recorder.size() > 0, "tracing on produces events for the same frames");
    expect(same_bytes(bytes_off, bytes_on), "tracing on leaves the output byte-identical");

    clear_frame_trace_sink();
    expect(!frame_trace_enabled(), "clearing the sink disables tracing");
    recorder.reset();
    Scene scene_again = base_scene(kWidth, kHeight);
    auto again = offscreen(1.0F);
    if (!again || !render_frames(*again, scene_again, 3)) return;
    expect(recorder.size() == 0, "frames after clearing the sink produce no events");
}

void scripted_trace() {
    RecordingSink recorder;
    recorder.install();
    Scene scene = base_scene(kWidth, kHeight);
    auto surface = offscreen(1.0F);
    if (!surface) return;

    std::vector<FrameStats> stats;
    std::vector<RenderTelemetry> telemetry;
    const auto& frames = three_frames();
    for (const auto& frame : frames) {
        frame.edit(scene);
        std::string error;
        if (!surface->render_incremental(scene, &error)) {
            expect(false, std::string("frame ") + frame.name + " renders", error);
            return;
        }
        stats.push_back(surface->frame_stats());
        telemetry.push_back(surface->telemetry());
    }
    std::string error;
    expect(surface->finish(&error), "the surface finishes", error);
    const auto events = recorder.events();

    expect(count_named(events, TraceEvent::Kind::begin, "graphics.frame") == 3,
           "three frames open three frame slices");
    for (std::size_t i = 0; i < 3; ++i) {
        expect(frame_shape(events, i) == "frame(record,submit)",
               std::string("frame ") + frames[i].name + " records then submits",
               frame_shape(events, i));
        const auto counters = frame_counters(events, i);
        const auto value = [&](const char* name) { return counter(counters, name); };
        const auto& s = stats[i];
        const auto& t = telemetry[i];
        expect(value("graphics.damage_pixels") &&
                   near(*value("graphics.damage_pixels"),
                        static_cast<double>(s.damage_area_pixels)),
               std::string("frame ") + frames[i].name + " reports its damage area");
        expect(value("graphics.damage_rects") &&
                   near(*value("graphics.damage_rects"),
                        static_cast<double>(s.damage_rects_after_coalescing)),
               std::string("frame ") + frames[i].name + " reports its rect count");
        expect(value("graphics.paint_commands_recorded") &&
                   near(*value("graphics.paint_commands_recorded"),
                        static_cast<double>(s.paint_commands_recorded)) &&
                   value("graphics.paint_commands_total") &&
                   near(*value("graphics.paint_commands_total"),
                        static_cast<double>(s.paint_commands_total)),
               std::string("frame ") + frames[i].name + " reports its commands");
        expect(value("graphics.cpu_ms") && near(*value("graphics.cpu_ms"), s.cpu_milliseconds) &&
                   *value("graphics.cpu_ms") > 0.0,
               std::string("frame ") + frames[i].name + " reports its cpu time");
        expect(value("graphics.present_wait_ms").has_value(),
               std::string("frame ") + frames[i].name + " reports its present wait");
        expect(value("graphics.layer_count") &&
                   near(*value("graphics.layer_count"), static_cast<double>(t.layer_count)),
               std::string("frame ") + frames[i].name + " reports its layer count");
        expect(value("graphics.cache.paragraphs.bytes") &&
                   near(*value("graphics.cache.paragraphs.bytes"),
                        static_cast<double>(t.paragraphs.bytes)) &&
                   value("graphics.cache.paragraphs.hit_rate") &&
                   near(*value("graphics.cache.paragraphs.hit_rate"),
                        frame_hit_rate(t.paragraphs)) &&
                   value("graphics.cache.gradients.bytes") &&
                   value("graphics.cache.gradients.hit_rate") &&
                   value("graphics.cache.textures.bytes") &&
                   value("graphics.cache.textures.hit_rate"),
               std::string("frame ") + frames[i].name + " reports cache bytes and hit rates");
    }
    expect(counter(frame_counters(events, 0), "graphics.damage_pixels").value_or(0.0) ==
               static_cast<double>(stats[0].frame_area_pixels),
           "the first frame's damage is the whole surface");
    expect(counter(frame_counters(events, 2), "graphics.damage_pixels").value_or(0.0) <
               static_cast<double>(stats[2].frame_area_pixels) / 2.0,
           "a partial frame's damage is less than half the surface");

    std::vector<TraceEvent> repaint_instants;
    for (const auto& event : events) {
        if (event.kind == TraceEvent::Kind::instant && event.name == "graphics.full_repaint") {
            repaint_instants.push_back(event);
        }
    }
    expect(repaint_instants.size() == 1 && repaint_instants[0].detail == "first_frame",
           "only the first frame emits a full-repaint instant, with its reason",
           std::to_string(repaint_instants.size()));

    recorder.reset();
    bool gpu_reported = false;
    for (int attempt = 0; attempt < 20 && !gpu_reported; ++attempt) {
        std::string retry_error;
        node(scene, "mover").bounds.x += 7.0F;
        if (!surface->render_incremental(scene, &retry_error) ||
            !surface->finish(&retry_error)) {
            break;
        }
        gpu_reported = count_named(recorder.events(), TraceEvent::Kind::counter,
                                   "graphics.gpu_ms") >= 1;
    }
    if (gpu_reported) {
        expect(true, "a finished frame reports its gpu time");
    } else {
        std::cout << "SKIP gpu_ms: the device reported no gpu time for these frames\n";
    }

    recorder.reset();
    RenderCacheBudgets budgets;
    budgets.paragraphs = {0, 0};
    surface->set_cache_budgets(budgets);
    const auto trims = recorder.events();
    expect(trims.size() == 1 && trims[0].kind == TraceEvent::Kind::instant &&
               trims[0].name == "graphics.cache_trim" && trims[0].detail == "budget_changed",
           "a budget change emits a cache-trim instant with its reason",
           std::to_string(trims.size()));

    recorder.reset();
    surface->simulate_device_loss();
    std::string loss_error;
    const bool rendered = surface->render_incremental(scene, &loss_error);
    expect(rendered, "the render after a device loss recovers", loss_error);
    const auto lost = recorder.events();
    expect(count_named(lost, TraceEvent::Kind::instant, "graphics.device_lost") == 1,
           "device loss emits one instant");
    expect(count_named(lost, TraceEvent::Kind::instant, "graphics.cache_trim") == 1,
           "device loss emits its cache-trim instant");
    expect(count_named(lost, TraceEvent::Kind::instant, "graphics.device_recovered") == 1,
           "the recovery emits one instant");
    std::size_t depth = 0;
    bool balanced = true;
    for (const auto& event : recorder.events()) {
        if (event.kind == TraceEvent::Kind::begin) ++depth;
        if (event.kind == TraceEvent::Kind::end) {
            if (depth == 0) balanced = false; else --depth;
        }
    }
    expect(balanced && depth == 0, "every slice that opens closes");
}

void native_present_slices() {
    auto target = native(1.0F);
    if (!target.surface) {
        std::cout << "SKIP native present slices: no usable CAMetalLayer surface\n";
        return;
    }
    RecordingSink recorder;
    recorder.install();
    Scene scene = base_scene(kWidth, kHeight);
    if (!render_frames(*target.surface, scene, 2)) return;
    const auto events = recorder.events();
    expect(frame_shape(events, 1) == "frame(record,submit(present_wait),present)",
           "a presented frame nests record, submit with its acquire wait, then present",
           frame_shape(events, 1));
    expect(counter(frame_counters(events, 1), "graphics.present_wait_ms").value_or(-1.0) >= 0.0,
           "a presented frame reports its present wait");
}

struct Region final {
    std::vector<PixelRect> rects;
};

bool inside_any(const std::vector<PixelRect>& rects, std::uint32_t x, std::uint32_t y) {
    return std::any_of(rects.begin(), rects.end(), [&](const PixelRect& rect) {
        return static_cast<std::int64_t>(x) >= rect.left &&
            static_cast<std::int64_t>(x) < rect.right &&
            static_cast<std::int64_t>(y) >= rect.top && static_cast<std::int64_t>(y) < rect.bottom;
    });
}

std::size_t pixel_offset(const Pixels& pixels, std::int64_t x, std::int64_t y) {
    return (static_cast<std::size_t>(y) * pixels.width + static_cast<std::size_t>(x)) * 4U;
}

bool pixel_differs(const Pixels& a, const Pixels& b, std::int64_t x, std::int64_t y) {
    const auto offset = pixel_offset(a, x, y);
    return !std::equal(a.rgba.begin() + static_cast<std::ptrdiff_t>(offset),
                       a.rgba.begin() + static_cast<std::ptrdiff_t>(offset) + 4,
                       b.rgba.begin() + static_cast<std::ptrdiff_t>(offset));
}

void check_overlay_frame(const std::string& label, SkiaDawnSurface& with_overlay,
                         SkiaDawnSurface& without, bool expect_full) {
    const auto rects = SkiaDawnSurfaceTestAccess::damage_overlay_rects(with_overlay);
    const auto shown = capture(with_overlay);
    const auto plain = capture(without);
    if (!shown.ok || !plain.ok) {
        expect(false, label + " captures");
        return;
    }
    if (rects.empty()) {
        expect(same_bytes(shown, plain), label + " has no damage and no outlines");
        return;
    }
    std::size_t outside = 0;
    std::size_t differing = 0;
    for (std::uint32_t y = 0; y < shown.height; ++y) {
        for (std::uint32_t x = 0; x < shown.width; ++x) {
            if (!pixel_differs(shown, plain, x, y)) continue;
            ++differing;
            if (!inside_any(rects, x, y)) ++outside;
        }
    }
    expect(differing > 0, label + " outlines something", std::to_string(differing));
    expect(outside == 0, label + " draws nothing outside the damage rectangles",
           std::to_string(outside) + " pixels outside");

    std::size_t interior_changed = 0;
    std::size_t edges_missing = 0;
    for (const auto& rect : rects) {
        if (rect.width() < 12 || rect.height() < 12) continue;
        for (std::int64_t y = rect.top + 3; y < rect.bottom - 3; ++y) {
            for (std::int64_t x = rect.left + 3; x < rect.right - 3; ++x) {
                if (pixel_differs(shown, plain, x, y)) ++interior_changed;
            }
        }
        const std::int64_t mid_x = (rect.left + rect.right) / 2;
        const std::int64_t mid_y = (rect.top + rect.bottom) / 2;
        for (const auto& [x, y] : std::vector<std::pair<std::int64_t, std::int64_t>>{
                 {rect.left, mid_y}, {rect.right - 1, mid_y}, {mid_x, rect.top},
                 {mid_x, rect.bottom - 1}}) {
            if (!pixel_differs(shown, plain, x, y)) ++edges_missing;
        }
    }
    expect(interior_changed == 0, label + " leaves each rectangle's interior untouched",
           std::to_string(interior_changed));
    expect(edges_missing == 0, label + " outlines all four edges of each rectangle",
           std::to_string(edges_missing));

    const auto& rect = rects.front();
    const auto offset = pixel_offset(shown, rect.left, (rect.top + rect.bottom) / 2);
    const bool orange = shown.rgba[offset + 1] > shown.rgba[offset + 2];
    expect(orange == expect_full, label + " colours a full repaint differently from a partial one");
}

void overlay_sequence(float scale, bool use_native) {
    const std::string label = std::string(use_native ? "native" : "offscreen") + " @" +
        std::to_string(static_cast<int>(scale)) + " ";
    std::unique_ptr<SkiaDawnSurface> shown;
    std::unique_ptr<SkiaDawnSurface> plain;
    std::unique_ptr<SkiaDawnSurface> full;
    NativeSurface native_shown;
    NativeSurface native_plain;
    if (use_native) {
        native_shown = native(scale, true);
        native_plain = native(scale, false);
        if (!native_shown.surface || !native_plain.surface) {
            std::cout << "SKIP " << label << "overlay: no usable CAMetalLayer surface\n";
            return;
        }
    }
    auto& with_overlay = use_native ? *native_shown.surface : *(shown = offscreen(scale, true));
    auto& without = use_native ? *native_plain.surface : *(plain = offscreen(scale, false));
    full = offscreen(scale);
    if (!full) return;
    expect(with_overlay.damage_overlay() && !without.damage_overlay(),
           label + "the overlay follows the surface configuration");

    Scene scene = base_scene(kWidth, kHeight);
    const auto& frames = three_frames();
    for (std::size_t i = 0; i < frames.size(); ++i) {
        frames[i].edit(scene);
        std::string error;
        const auto presented_before = SkiaDawnSurfaceTestAccess::damage_overlay_presented(with_overlay);
        const bool shown_ok = with_overlay.render_incremental(scene, &error);
        const auto presented_after = SkiaDawnSurfaceTestAccess::damage_overlay_presented(with_overlay);
        const bool ok = shown_ok && without.render_incremental(scene, &error) &&
            full->render(scene, &error);
        if (!ok) {
            expect(false, label + frames[i].name + " renders", error);
            return;
        }
        const auto a = with_overlay.frame_stats();
        const auto b = without.frame_stats();
        expect(a.full_repaint == b.full_repaint && a.full_repaint_reason == b.full_repaint_reason &&
                   a.damage_area_pixels == b.damage_area_pixels &&
                   a.damage_rects_after_coalescing == b.damage_rects_after_coalescing &&
                   a.paint_commands_recorded == b.paint_commands_recorded &&
                   a.paint_command_draws == b.paint_command_draws,
               label + frames[i].name + " damage does not feed back from the overlay");
        if (use_native) {
            expect(presented_after == presented_before + 1,
                   label + frames[i].name + " draws the overlay into the presented frame itself",
                   std::to_string(presented_after - presented_before));
        }
        check_overlay_frame(label + frames[i].name + ": ", with_overlay, without, i == 0);
    }

    const auto reference = capture(*full);
    const auto plain_pixels = capture(without);
    expect(same_bytes(reference, plain_pixels),
           label + "with the overlay off, partial redraw matches a full redraw byte for byte");
    with_overlay.set_damage_overlay(false);
    expect(SkiaDawnSurfaceTestAccess::damage_overlay_rects(with_overlay).empty(),
           label + "turning the overlay off drops its rectangles");
    expect(same_bytes(reference, capture(with_overlay)),
           label + "the retained image a surface drew with the overlay on is untouched by it");
}

void environment_switch() {
    ::setenv("VELLUM_DAMAGE_OVERLAY", "1", 1);
    auto on = offscreen(1.0F);
    ::setenv("VELLUM_DAMAGE_OVERLAY", "0", 1);
    auto off = offscreen(1.0F);
    ::unsetenv("VELLUM_DAMAGE_OVERLAY");
    auto unset = offscreen(1.0F);
    if (!on || !off || !unset) return;
    expect(on->damage_overlay(), "VELLUM_DAMAGE_OVERLAY=1 turns the overlay on");
    expect(!off->damage_overlay(), "VELLUM_DAMAGE_OVERLAY=0 leaves the overlay off");
    expect(!unset->damage_overlay(), "the overlay is off by default");
    unset->set_damage_overlay(true);
    expect(unset->damage_overlay(), "set_damage_overlay turns the overlay on");
}

std::size_t occurrences(const std::string& text, const std::string& needle) {
    std::size_t count = 0;
    for (auto at = text.find(needle); at != std::string::npos; at = text.find(needle, at + 1)) {
        ++count;
    }
    return count;
}

std::string read_file(const std::filesystem::path& path) {
    std::ifstream file(path, std::ios::binary);
    std::ostringstream text;
    text << file.rdbuf();
    return text.str();
}

void json_export() {
    FrameTraceRecorder recorder;
    recorder.start();
    expect(frame_trace_enabled(), "starting a recorder enables tracing");
    Scene scene = base_scene(kWidth, kHeight);
    auto surface = offscreen(1.0F);
    if (!surface || !render_frames(*surface, scene, 3)) return;
    recorder.stop();
    expect(!frame_trace_enabled(), "stopping a recorder disables tracing");
    const auto json = recorder.to_json();
    expect(recorder.event_count() > 0 && recorder.dropped_events() == 0,
           "the recorder keeps every event", std::to_string(recorder.event_count()));
    expect(json.rfind("{\"traceEvents\":[", 0) == 0 && json.ends_with("}\n"),
           "the export is a Chrome trace-event document");
    expect(occurrences(json, "\"ph\":\"B\"") == occurrences(json, "\"ph\":\"E\"") &&
               occurrences(json, "\"ph\":\"B\"") == 9,
           "three frames export nine balanced slices",
           std::to_string(occurrences(json, "\"ph\":\"B\"")));
    expect(occurrences(json, "\"name\":\"graphics.frame\"") == 3 &&
               occurrences(json, "\"name\":\"graphics.damage_pixels\"") == 3,
           "the export names the frame slices and a counter once per frame");
    expect(occurrences(json, "\"detail\":\"first_frame\"") == 1,
           "the export carries the full-repaint reason");

    const auto path = std::filesystem::temp_directory_path() / "vellum-frame-debug-export.json";
    std::string error;
    expect(recorder.write(path.string(), &error) && read_file(path) == json,
           "write puts the export on disk", error);
    std::filesystem::remove(path);
    expect(!recorder.write("/nonexistent-vellum-directory/trace.json", &error) && !error.empty(),
           "write reports a path it cannot open");

    FrameTraceRecorder small(4);
    small.start();
    Scene again = base_scene(kWidth, kHeight);
    auto other = offscreen(1.0F);
    if (other) render_frames(*other, again, 1);
    small.stop();
    expect(small.event_count() == 4 && small.dropped_events() > 0,
           "a full recorder drops events and counts them");
}

void sink_ownership() {
    FrameTraceRecorder first;
    FrameTraceRecorder second;
    expect(first.start_if_idle(), "a recorder starts when no sink is installed");
    expect(!second.start_if_idle(), "a recorder does not take over an installed sink");
    second.stop();
    expect(frame_trace_enabled(), "stopping a recorder that never started leaves the sink alone");
    first.stop();
    second.start();
    first.stop();
    expect(frame_trace_enabled(), "stopping one recorder leaves another's sink installed");
    second.stop();
    expect(!frame_trace_enabled(), "stopping the installed recorder removes it");
}

void locale_independent_export() {
    const char* previous = std::setlocale(LC_NUMERIC, nullptr);
    const std::string saved = previous != nullptr ? previous : "C";
    const char* candidates[] = {"de_DE.UTF-8", "fr_FR.UTF-8", "es_ES.UTF-8"};
    bool switched = false;
    for (const char* name : candidates) {
        if (std::setlocale(LC_NUMERIC, name) != nullptr &&
            std::string(std::localeconv()->decimal_point) == ",") {
            switched = true;
            break;
        }
    }
    if (!switched) {
        std::setlocale(LC_NUMERIC, saved.c_str());
        std::cout << "SKIP locale export: no decimal-comma locale installed\n";
        return;
    }
    FrameTraceRecorder recorder;
    recorder.start();
    Scene scene = base_scene(kWidth, kHeight);
    auto surface = offscreen(1.0F);
    if (surface) render_frames(*surface, scene, 1);
    recorder.stop();
    const auto json = recorder.to_json();
    std::setlocale(LC_NUMERIC, saved.c_str());
    bool comma_number = false;
    for (const char* key : {"\"ts\":", "\"value\":"}) {
        for (auto at = json.find(key); at != std::string::npos; at = json.find(key, at + 1)) {
            const auto end = json.find_first_of(",}", at + std::string(key).size());
            if (json[end] == ',' && std::isdigit(static_cast<unsigned char>(json[end + 1])) &&
                json.find_first_of("\"{}", end) > end + 1 &&
                json[json.find_first_of("\"{}", end)] != '"') {
                comma_number = true;
            }
        }
    }
    expect(!comma_number && json.find("\"ts\":") != std::string::npos,
           "the export uses a decimal point under a decimal-comma locale");
}

void environment_trace_defers_to_host_sink() {
    const auto path = std::filesystem::temp_directory_path() / "vellum-frame-debug-host.json";
    std::filesystem::remove(path);
    RecordingSink host;
    host.install();
    ::setenv("VELLUM_FRAME_TRACE", path.c_str(), 1);
    {
        Scene scene = base_scene(kWidth, kHeight);
        auto surface = offscreen(1.0F);
        if (surface) render_frames(*surface, scene, 1);
    }
    ::unsetenv("VELLUM_FRAME_TRACE");
    expect(count_named(host.events(), TraceEvent::Kind::begin, "graphics.frame") == 1,
           "a host's sink keeps receiving frames when VELLUM_FRAME_TRACE is set");
    clear_frame_trace_sink();
    expect(!std::filesystem::exists(path), "VELLUM_FRAME_TRACE writes nothing while a host sink is installed");
    std::filesystem::remove(path);
}

void environment_trace_file() {
    const auto path = std::filesystem::temp_directory_path() / "vellum-frame-debug-env.json";
    std::filesystem::remove(path);
    ::setenv("VELLUM_FRAME_TRACE", path.c_str(), 1);
    {
        Scene scene = base_scene(kWidth, kHeight);
        auto surface = offscreen(1.0F);
        if (surface) render_frames(*surface, scene, 2);
    }
    ::unsetenv("VELLUM_FRAME_TRACE");
    clear_frame_trace_sink();
    const auto json = read_file(path);
    expect(occurrences(json, "\"name\":\"graphics.frame\"") == 2,
           "VELLUM_FRAME_TRACE records the process's frames into the named file",
           std::to_string(occurrences(json, "\"name\":\"graphics.frame\"")));
    std::filesystem::remove(path);
}

}  // namespace

int main() {
    std::string error;
    if (!harness::prepare_device(&error)) {
        std::cerr << "FAIL device bootstrap: " << error << '\n';
        return 1;
    }
    {
        auto probe = offscreen(1.0F);
        if (!probe) return kSkip;
    }
    tracing_off_changes_nothing();
    scripted_trace();
    native_present_slices();
    json_export();
    environment_switch();
    sink_ownership();
    locale_independent_export();
    environment_trace_defers_to_host_sink();
    environment_trace_file();
    for (const float scale : {1.0F, 2.0F}) {
        overlay_sequence(scale, false);
        overlay_sequence(scale, true);
    }
    std::cout << (failures == 0 ? "frame debug checks passed" : "frame debug checks failed")
              << '\n';
    return failures == 0 ? 0 : 1;
}
