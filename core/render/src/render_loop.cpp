// RenderLoop platform implementations
// Each platform uses its native vsync mechanism for frame-paced rendering.

#include <pulp/render/render_loop.hpp>
#include <pulp/runtime/log.hpp>
#include <thread>
#include <chrono>

#ifdef __APPLE__
#include <TargetConditionals.h>
#if TARGET_OS_OSX
#include <CoreVideo/CVDisplayLink.h>
#include <dispatch/dispatch.h>
#endif
#endif

namespace pulp::render {

// ── macOS: CVDisplayLink ────────────────────────────────────────────────

#if defined(__APPLE__) && TARGET_OS_OSX

class MacRenderLoop : public RenderLoop {
public:
    ~MacRenderLoop() override { stop(); }

    void start(FrameCallback on_frame) override {
        callback_ = std::move(on_frame);
        running_ = true;
        needs_frame_ = true;

        CVDisplayLinkCreateWithActiveCGDisplays(&display_link_);
        CVDisplayLinkSetOutputCallback(display_link_, &display_link_callback, this);
        CVDisplayLinkStart(display_link_);
    }

    void stop() override {
        running_ = false;
        if (display_link_) {
            CVDisplayLinkStop(display_link_);
            CVDisplayLinkRelease(display_link_);
            display_link_ = nullptr;
        }
    }

    void request_frame() override {
        needs_frame_.store(true, std::memory_order_relaxed);
    }

    bool is_running() const override { return running_.load(); }

private:
    CVDisplayLinkRef display_link_ = nullptr;
    FrameCallback callback_;
    std::atomic<bool> running_{false};
    std::atomic<bool> needs_frame_{false};

    static CVReturn display_link_callback(CVDisplayLinkRef, const CVTimeStamp*,
        const CVTimeStamp*, CVOptionFlags, CVOptionFlags*, void* ctx) {
        auto* self = static_cast<MacRenderLoop*>(ctx);
        if (self->needs_frame_.exchange(false, std::memory_order_relaxed)) {
            dispatch_async(dispatch_get_main_queue(), ^{
                if (self->running_.load() && self->callback_) {
                    self->callback_();
                }
            });
        }
        return kCVReturnSuccess;
    }
};

#endif // macOS

// ── Generic: Timer-based fallback (Windows, Linux, or no-vsync) ─────────

class TimerRenderLoop : public RenderLoop {
public:
    ~TimerRenderLoop() override { stop(); }

    void start(FrameCallback on_frame) override {
        callback_ = std::move(on_frame);
        running_ = true;
        needs_frame_ = true;

        thread_ = std::thread([this]() {
            using namespace std::chrono;
            auto frame_interval = microseconds(16667); // ~60Hz
            while (running_.load()) {
                auto start = steady_clock::now();
                if (needs_frame_.exchange(false, std::memory_order_relaxed)) {
                    if (callback_) callback_();
                }
                auto elapsed = steady_clock::now() - start;
                auto sleep_time = frame_interval - duration_cast<microseconds>(elapsed);
                if (sleep_time.count() > 0) {
                    std::this_thread::sleep_for(sleep_time);
                }
            }
        });
    }

    void stop() override {
        running_ = false;
        if (thread_.joinable()) thread_.join();
    }

    void request_frame() override {
        needs_frame_.store(true, std::memory_order_relaxed);
    }

    bool is_running() const override { return running_.load(); }

private:
    std::thread thread_;
    FrameCallback callback_;
    std::atomic<bool> running_{false};
    std::atomic<bool> needs_frame_{false};
};

// ── Factory ─────────────────────────────────────────────────────────────

std::unique_ptr<RenderLoop> RenderLoop::create(void* native_handle) {
    (void)native_handle;
#if defined(__APPLE__) && TARGET_OS_OSX
    return std::make_unique<MacRenderLoop>();
#else
    return std::make_unique<TimerRenderLoop>();
#endif
}

} // namespace pulp::render
