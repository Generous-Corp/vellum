#if defined(__ANDROID__) && !defined(PULP_RENDER_LOOP_FORCE_TIMER)

#include <vellum/render/render_loop.hpp>
#include "render_loop_state.hpp"

#include <android/choreographer.h>
#include <memory>

namespace vellum::render {

class AndroidRenderLoop final : public RenderLoop {
  public:
    ~AndroidRenderLoop() override { stop(); }

    void start(FrameCallback callback) override {
        if (!state_.start())
            return;
        callback_ = std::move(callback);
        choreographer_ = AChoreographer_getInstance();
        request_vsync();
    }

    void stop() override { state_.stop(); }

    void request_frame() override {
        state_.request_frame();
        request_vsync();
    }

    bool is_running() const override { return state_.is_running(); }
    RenderLoopBackend backend() const override { return RenderLoopBackend::choreographer; }

  private:
    static void on_vsync(long, void* context) {
        auto* self = static_cast<AndroidRenderLoop*>(context);
        if (!self->state_.is_running())
            return;
        if (self->state_.consume_frame_request() && self->callback_)
            self->callback_();
        self->request_vsync();
    }

    void request_vsync() {
        if (choreographer_ && state_.is_running())
            AChoreographer_postFrameCallback(choreographer_, &on_vsync, this);
    }

    AChoreographer* choreographer_ = nullptr;
    FrameCallback callback_;
    RenderLoopState state_;
};

std::unique_ptr<RenderLoop> make_android_render_loop() {
    return std::make_unique<AndroidRenderLoop>();
}

}  // namespace vellum::render

#endif
