#include "frame_trace.hpp"

#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <locale>
#include <sstream>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace vellum::graphics {
namespace {

std::mutex retired_mutex;

std::vector<std::unique_ptr<FrameTraceSink>>& retired_sinks() {
    static auto* sinks = new std::vector<std::unique_ptr<FrameTraceSink>>();
    return *sinks;
}

}  // namespace

namespace detail {

std::atomic<const FrameTraceSink*> active_frame_trace_sink_storage{nullptr};

}  // namespace detail

void set_frame_trace_sink(const FrameTraceSink& sink) {
    auto copy = std::make_unique<FrameTraceSink>(sink);
    const auto* published = copy.get();
    std::lock_guard lock(retired_mutex);
    retired_sinks().push_back(std::move(copy));
    detail::active_frame_trace_sink_storage.store(published, std::memory_order_release);
}

namespace detail {

bool install_frame_trace_sink_if_none(const FrameTraceSink& sink) {
    auto copy = std::make_unique<FrameTraceSink>(sink);
    const auto* published = copy.get();
    std::lock_guard lock(retired_mutex);
    const FrameTraceSink* expected = nullptr;
    if (!active_frame_trace_sink_storage.compare_exchange_strong(
            expected, published, std::memory_order_acq_rel)) {
        return false;
    }
    retired_sinks().push_back(std::move(copy));
    return true;
}

void clear_frame_trace_sink_for(void* context) noexcept {
    const auto* active = active_frame_trace_sink_storage.load(std::memory_order_acquire);
    if (active == nullptr || active->context != context) return;
    active_frame_trace_sink_storage.compare_exchange_strong(
        active, nullptr, std::memory_order_acq_rel);
}

}  // namespace detail

void clear_frame_trace_sink() noexcept {
    detail::active_frame_trace_sink_storage.store(nullptr, std::memory_order_release);
}

bool frame_trace_enabled() noexcept {
    return detail::active_frame_trace_sink() != nullptr;
}

namespace {

struct RecordedEvent final {
    char phase = 'B';
    std::string name;
    std::string detail;
    double value = 0.0;
    double timestamp_us = 0.0;
    int thread = 0;
};

void append_json_string(std::string& out, const std::string& text) {
    out += '"';
    for (const unsigned char c : text) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char escaped[8];
                    std::snprintf(escaped, sizeof escaped, "\\u%04x", c);
                    out += escaped;
                } else {
                    out += static_cast<char>(c);
                }
        }
    }
    out += '"';
}

}  // namespace

struct FrameTraceRecorder::State final {
    explicit State(std::size_t limit)
        : max_events(limit), origin(std::chrono::steady_clock::now()) {
        sink.slice_begin = [](const char* name, void* self) {
            static_cast<State*>(self)->add('B', name != nullptr ? name : "", {}, 0.0);
        };
        sink.slice_end = [](void* self) { static_cast<State*>(self)->add('E', {}, {}, 0.0); };
        sink.counter = [](const char* name, double value, void* self) {
            static_cast<State*>(self)->add('C', name != nullptr ? name : "", {}, value);
        };
        sink.instant = [](const char* name, const char* detail, void* self) {
            static_cast<State*>(self)->add('i', name != nullptr ? name : "",
                                           detail != nullptr ? detail : "", 0.0);
        };
        sink.context = this;
    }

    void add(char phase, std::string name, std::string detail, double value) {
        const auto now = std::chrono::steady_clock::now();
        std::lock_guard lock(mutex);
        if (events.size() >= max_events) {
            ++dropped;
            return;
        }
        const auto id = std::this_thread::get_id();
        auto found = threads.find(id);
        if (found == threads.end()) {
            found = threads.emplace(id, static_cast<int>(threads.size()) + 1).first;
        }
        events.push_back({phase, std::move(name), std::move(detail), value,
                          std::chrono::duration<double, std::micro>(now - origin).count(),
                          found->second});
    }

    FrameTraceSink sink;
    std::size_t max_events;
    std::chrono::steady_clock::time_point origin;
    mutable std::mutex mutex;
    std::vector<RecordedEvent> events;
    std::map<std::thread::id, int> threads;
    std::size_t dropped = 0;
};

FrameTraceRecorder::FrameTraceRecorder(std::size_t max_events)
    : state_(std::make_unique<State>(max_events)) {}

FrameTraceRecorder::~FrameTraceRecorder() { stop(); }

void FrameTraceRecorder::start() { set_frame_trace_sink(state_->sink); }

bool FrameTraceRecorder::start_if_idle() {
    return detail::install_frame_trace_sink_if_none(state_->sink);
}

void FrameTraceRecorder::stop() noexcept { detail::clear_frame_trace_sink_for(state_.get()); }

std::size_t FrameTraceRecorder::event_count() const {
    std::lock_guard lock(state_->mutex);
    return state_->events.size();
}

std::size_t FrameTraceRecorder::dropped_events() const {
    std::lock_guard lock(state_->mutex);
    return state_->dropped;
}

std::string FrameTraceRecorder::to_json() const {
    std::lock_guard lock(state_->mutex);
    const auto process = std::to_string(static_cast<long>(::getpid()));
    std::string out = "{\"traceEvents\":[\n{\"ph\":\"M\",\"name\":\"process_name\",\"pid\":" +
        process + ",\"args\":{\"name\":\"vellum\"}}";
    const auto format = [](double value, int precision, bool fixed) {
        std::ostringstream text;
        text.imbue(std::locale::classic());
        if (fixed) text << std::fixed;
        text.precision(precision);
        text << value;
        return text.str();
    };
    for (const auto& event : state_->events) {
        out += ",\n{\"ph\":\"";
        out += event.phase;
        out += "\",\"cat\":\"graphics\",\"pid\":" + process +
            ",\"tid\":" + std::to_string(event.thread);
        out += ",\"ts\":" + format(event.timestamp_us, 3, true);
        if (event.phase != 'E') {
            out += ",\"name\":";
            append_json_string(out, event.name);
        }
        if (event.phase == 'C') {
            out += ",\"args\":{\"value\":" + format(event.value, 9, false) + '}';
        } else if (event.phase == 'i') {
            out += ",\"s\":\"t\",\"args\":{\"detail\":";
            append_json_string(out, event.detail);
            out += '}';
        }
        out += '}';
    }
    out += "\n],\"displayTimeUnit\":\"ms\",\"metadata\":{\"dropped_events\":" +
        std::to_string(state_->dropped) + "}}\n";
    return out;
}

bool FrameTraceRecorder::write(const std::string& path, std::string* error) const {
    const auto json = to_json();
    std::ofstream file(path, std::ios::binary | std::ios::trunc);
    file.write(json.data(), static_cast<std::streamsize>(json.size()));
    file.flush();
    if (!file) {
        if (error != nullptr) *error = "could not write " + path;
        return false;
    }
    return true;
}

namespace {

struct EnvironmentTrace final {
    std::mutex mutex;
    FrameTraceRecorder* recorder = nullptr;
    std::string path;
};

EnvironmentTrace& environment_trace() {
    static auto* trace = new EnvironmentTrace();
    return *trace;
}

}  // namespace

namespace detail {

void start_frame_trace_from_environment() {
    const char* raw = std::getenv("VELLUM_FRAME_TRACE");
    if (raw == nullptr || *raw == '\0') return;
    auto& trace = environment_trace();
    std::lock_guard lock(trace.mutex);
    if (trace.recorder != nullptr || active_frame_trace_sink() != nullptr) return;
    auto recorder = std::make_unique<FrameTraceRecorder>();
    if (!recorder->start_if_idle()) return;
    trace.recorder = recorder.release();
    trace.path = raw;
    std::atexit([] { flush_frame_trace_from_environment(); });
}

void flush_frame_trace_from_environment() noexcept {
    auto& trace = environment_trace();
    std::lock_guard lock(trace.mutex);
    if (trace.recorder == nullptr) return;
    std::string error;
    if (!trace.recorder->write(trace.path, &error)) {
        std::fprintf(stderr, "vellum: frame trace not written: %s\n", error.c_str());
    }
}

}  // namespace detail

}  // namespace vellum::graphics
