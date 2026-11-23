#if defined(__ANDROID__)

#include <vellum/render/render_loop.hpp>
#include "render_loop_state.hpp"

#include <android/choreographer.h>
#include <atomic>
#include <memory>
#include <mutex>
#include <utility>

namespace vellum::render {

class AndroidRenderLoop final : public RenderLoop {
  public:
    ~AndroidRenderLoop() override { stop(); }

    void start(FrameCallback callback) override {
        if (callback_state_ && callback_state_->render_state.is_running())
            return;
        auto state = std::make_shared<CallbackState>(std::move(callback));
        if (!state->render_state.start())
            return;
        state->choreographer = AChoreographer_getInstance();
        if (!state->choreographer) {
            // No Looper on this thread: no frame callback could ever be posted,
            // so do not report a running loop.
            state->render_state.stop();
            return;
        }
        callback_state_ = std::move(state);
        request_vsync(callback_state_);
    }

    void stop() override {
        auto state = callback_state_;
        if (!state)
            return;
        state->render_state.stop();
        state->alive.store(false, std::memory_order_release);
        std::lock_guard lock(state->callback_mutex);
        state->callback = {};
    }

    void request_frame() override {
        auto state = callback_state_;
        if (!state)
            return;
        state->render_state.request_frame();
        request_vsync(std::move(state));
    }

    bool is_running() const override {
        return callback_state_ && callback_state_->render_state.is_running();
    }
    RenderLoopBackend backend() const override { return RenderLoopBackend::choreographer; }

  private:
    struct CallbackState {
        explicit CallbackState(FrameCallback frame_callback)
            : callback(std::move(frame_callback)) {}

        AChoreographer* choreographer = nullptr;
        RenderLoopState render_state;
        std::atomic<bool> alive{true};
        std::atomic<bool> callback_scheduled{false};
        std::mutex callback_mutex;
        FrameCallback callback;
    };

    static void on_vsync(long, void* context) {
        // The heap holder owns one shared reference until this callback runs.
        // AChoreographer has no cancellation API, so this keeps the state
        // alive when stop() or destruction races a queued callback.
        auto* holder = static_cast<std::shared_ptr<CallbackState>*>(context);
        std::shared_ptr<CallbackState> state = std::move(*holder);
        delete holder;
        state->callback_scheduled.store(false, std::memory_order_release);

        if (!state->alive.load(std::memory_order_acquire) ||
            !state->render_state.is_running())
            return;

        FrameCallback callback;
        {
            std::lock_guard lock(state->callback_mutex);
            callback = state->callback;
        }
        if (state->render_state.consume_frame_request() && callback)
            callback();
        request_vsync(std::move(state));
    }

    static void request_vsync(std::shared_ptr<CallbackState> state) {
        if (!state->choreographer || !state->alive.load(std::memory_order_acquire) ||
            !state->render_state.is_running())
            return;
        bool expected = false;
        if (!state->callback_scheduled.compare_exchange_strong(
                expected, true, std::memory_order_acq_rel))
            return;
        auto* holder = new std::shared_ptr<CallbackState>(std::move(state));
        AChoreographer_postFrameCallback(holder->get()->choreographer, &on_vsync, holder);
    }

    std::shared_ptr<CallbackState> callback_state_;
};

std::unique_ptr<RenderLoop> make_android_render_loop() {
    return std::make_unique<AndroidRenderLoop>();
}

}  // namespace vellum::render

#endif
