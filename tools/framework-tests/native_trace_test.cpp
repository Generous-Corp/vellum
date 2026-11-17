// One native Perfetto session carries the framework's slices and the renderer's.
// Built only with VELLUM_ENABLE_TRACING. The test starts the process session,
// installs the frame trace adapter, renders three frames inside a framework
// slice, stops the session, and reads the trace file it wrote.

#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/frame_trace.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>
#include <vellum/runtime/trace.hpp>
#include <vellum/runtime/trace_session.hpp>

#include <perfetto.h>

#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <set>
#include <string_view>
#include <vector>

namespace {

namespace harness = vellum::graphics::capture_harness;
using namespace vellum::graphics;
using vellum::runtime::Tracing;

constexpr int kSkip = 77;
int failures = 0;

void expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
}

struct Reader final {
    const std::uint8_t* data;
    std::size_t size;
    std::size_t at = 0;

    bool done() const { return at >= size; }

    bool varint(std::uint64_t& out) {
        out = 0;
        for (int shift = 0; shift < 64; shift += 7) {
            if (at >= size) return false;
            const std::uint8_t byte = data[at++];
            out |= static_cast<std::uint64_t>(byte & 0x7F) << shift;
            if ((byte & 0x80) == 0) return true;
        }
        return false;
    }

    // One field: its number, wire type, and the bytes of a length-delimited value.
    bool field(std::uint64_t& number, std::uint64_t& wire, std::uint64_t& value, Reader& body) {
        std::uint64_t key = 0;
        if (!varint(key)) return false;
        number = key >> 3;
        wire = key & 7;
        switch (wire) {
        case 0:
            return varint(value);
        case 1:
            if (size - at < 8) return false;
            at += 8;
            return true;
        case 5:
            if (size - at < 4) return false;
            at += 4;
            return true;
        case 2: {
            std::uint64_t length = 0;
            if (!varint(length) || length > size - at) return false;
            body = Reader{data + at, static_cast<std::size_t>(length)};
            at += static_cast<std::size_t>(length);
            return true;
        }
        default:
            return false;
        }
    }
};

struct TraceShape final {
    bool well_formed = true;
    std::size_t packets = 0;
    std::size_t track_events = 0;
    std::size_t slice_begins = 0;
    std::size_t slice_ends = 0;
    std::size_t instants = 0;
    std::size_t counters = 0;
    std::set<std::string> slice_names;
    std::set<std::string> instant_names;
    std::set<std::string> counter_tracks;
    std::set<std::string> interned_names;
};

std::string text(const Reader& body) {
    return std::string(reinterpret_cast<const char*>(body.data), body.size);
}

// A Perfetto trace is a sequence of TracePacket messages (field 1 of the file).
// Packet field 11 is a TrackEvent: field 9 is its type (1 begin, 2 end, 3
// instant, 4 counter) and field 23 a name held in the event itself. Field 12 is
// interned data, whose field 2 lists event names; field 60 is a track
// descriptor, whose field 2 names a track (a counter's track is named for the
// counter).
TraceShape read_shape(const std::string& bytes) {
    TraceShape shape;
    Reader file{reinterpret_cast<const std::uint8_t*>(bytes.data()), bytes.size()};
    while (!file.done()) {
        std::uint64_t number = 0, wire = 0, value = 0;
        Reader packet{nullptr, 0};
        if (!file.field(number, wire, value, packet) || number != 1 || wire != 2) {
            shape.well_formed = false;
            return shape;
        }
        ++shape.packets;
        while (!packet.done()) {
            Reader body{nullptr, 0};
            if (!packet.field(number, wire, value, body)) {
                shape.well_formed = false;
                return shape;
            }
            if (wire != 2) continue;
            const auto packet_field = number;
            if (packet_field == 11) {
                ++shape.track_events;
                std::uint64_t type = 0;
                std::string name;
                while (!body.done()) {
                    Reader inner{nullptr, 0};
                    if (!body.field(number, wire, value, inner)) {
                        shape.well_formed = false;
                        return shape;
                    }
                    if (number == 9 && wire == 0) type = value;
                    if (number == 23 && wire == 2) name = text(inner);
                }
                shape.slice_begins += type == 1;
                shape.slice_ends += type == 2;
                shape.instants += type == 3;
                shape.counters += type == 4;
                if (type == 1 && !name.empty()) shape.slice_names.insert(name);
                if (type == 3 && !name.empty()) shape.instant_names.insert(name);
            } else if (packet_field == 12 || packet_field == 60) {
                while (!body.done()) {
                    Reader inner{nullptr, 0};
                    if (!body.field(number, wire, value, inner)) {
                        shape.well_formed = false;
                        return shape;
                    }
                    if (wire != 2) continue;
                    if (packet_field == 60 && number == 2) shape.counter_tracks.insert(text(inner));
                    if (packet_field == 12 && number == 2) {
                        while (!inner.done()) {
                            Reader event_name{nullptr, 0};
                            if (!inner.field(number, wire, value, event_name)) {
                                shape.well_formed = false;
                                return shape;
                            }
                            if (number == 2 && wire == 2) shape.interned_names.insert(text(event_name));
                        }
                    }
                }
            }
        }
    }
    return shape;
}

}  // namespace

int main() {
    if (!vellum::runtime::kTracingEnabled) {
        std::cerr << "FAIL this test is built only with VELLUM_ENABLE_TRACING, but the runtime "
                     "was compiled with its trace macros off\n";
        return 1;
    }
    std::string error;
    if (!harness::prepare_device(&error)) {
        std::cerr << "FAIL device bootstrap: " << error << '\n';
        return 1;
    }
    auto surface = SkiaDawnSurface::create({.width = 400, .height = 300, .scale = 1.0F}, &error);
    if (!surface) return kSkip;

    const auto path = std::filesystem::temp_directory_path() /
                      ("vellum-native-trace-" + std::to_string(std::rand()) + ".pftrace");
    std::filesystem::remove(path);

    expect(!Tracing::active(), "no session is active at start");
    expect(Tracing::start({}, path.string(), 16u * 1024u), "the native session starts");
    expect(install_native_frame_trace(), "the frame trace adapter installs");
    expect(native_frame_trace_installed(), "the adapter's sink is the installed one");

    Scene scene = harness::fixtures::combined(400.0F, 300.0F);
    scene.scene_version = kDamageModelSceneVersion;
    scene.root.children.push_back({
        .id = "mover",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {32.0F, 186.0F, 36.0F, 24.0F},
        .fill = Color::hex(0xF472B6),
    });
    {
        TRACE_EVENT("render", "framework.native_trace_test");
        for (int frame = 0; frame < 3; ++frame) {
            if (frame > 0) scene.root.children.back().bounds.x += 41.0F;
            std::string frame_error;
            if (!surface->render_incremental(scene, &frame_error)) {
                expect(false, "frame renders", frame_error);
                break;
            }
        }
        TRACE_EVENT_INSTANT("render", "framework.native_trace_test.done");
    }

    remove_native_frame_trace();
    expect(!native_frame_trace_installed(), "removing the adapter clears its sink");
    expect(install_native_frame_trace(), "the adapter installs again");
    int host_sink_tag = 0;
    FrameTraceSink host_sink;
    host_sink.context = &host_sink_tag;
    set_frame_trace_sink(host_sink);
    expect(!native_frame_trace_installed(),
           "a sink installed after the adapter replaces it");
    remove_native_frame_trace();
    expect(frame_trace_sink_context() == &host_sink_tag,
           "removing the adapter leaves a host's sink installed");
    clear_frame_trace_sink();

    const auto stopped = Tracing::stop();
    expect(stopped.ok, "the session stops and writes a file", stopped.path);
    expect(stopped.trace_bytes > 0, "the trace file has bytes");
    expect(!Tracing::active(), "no session is active after stop");

    std::ifstream in(stopped.path, std::ios::binary);
    const std::string bytes((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    expect(!bytes.empty(), "the trace file reads back");

    const TraceShape shape = read_shape(bytes);
    expect(shape.well_formed, "every packet and track event parses as protobuf");
    expect(shape.track_events > 0, "the trace holds track events",
           std::to_string(shape.track_events));
    expect(shape.slice_begins > 0 && shape.slice_begins == shape.slice_ends,
           "slice begins and ends pair up",
           std::to_string(shape.slice_begins) + " begins, " + std::to_string(shape.slice_ends) + " ends");
    expect(shape.counters > 0, "the trace holds counter samples", std::to_string(shape.counters));
    expect(shape.instants > 0, "the trace holds instants", std::to_string(shape.instants));

    expect(shape.interned_names.count("framework.native_trace_test") == 1 ||
               shape.slice_names.count("framework.native_trace_test") == 1,
           "the framework slice is in the trace");
    expect(shape.interned_names.count("framework.native_trace_test.done") == 1 ||
               shape.instant_names.count("framework.native_trace_test.done") == 1,
           "the framework instant is in the trace");
    for (const char* name : {"graphics.frame", "graphics.record", "graphics.submit"})
        expect(shape.slice_names.count(name) == 1, std::string("graphics slice ") + name + " is in the trace");
    for (const char* name : {"graphics.damage_pixels", "graphics.cpu_ms", "graphics.layer_count"})
        expect(shape.counter_tracks.count(name) == 1,
               std::string("graphics counter track ") + name + " is in the trace");
    expect(shape.instant_names.count("graphics.full_repaint") == 1,
           "the first frame's full repaint instant is in the trace");

    std::filesystem::remove(stopped.path);
    std::cout << (failures == 0 ? "native trace checks passed" : "native trace checks failed") << '\n';
    return failures == 0 ? 0 : 1;
}
