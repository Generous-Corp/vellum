#pragma once

// RenderLoop abstraction for frame-paced GPU rendering
// Platform-specific implementations drive the render callback at display refresh rate.
//
// macOS: CVDisplayLink
// Windows: DwmFlush or present-paced via D3D12 swap chain
// Linux: SDL3 event loop with vsync via Vulkan present
// WASM: requestAnimationFrame

#include <functional>
#include <memory>
#include <atomic>

namespace pulp::render {

using FrameCallback = std::function<void()>;

class RenderLoop {
public:
    // Create a platform-appropriate render loop
    // native_handle: window/view handle (NSView*, HWND, SDL_Window*)
    static std::unique_ptr<RenderLoop> create(void* native_handle = nullptr);

    virtual ~RenderLoop() = default;

    // Start the render loop with a frame callback
    // The callback is invoked at (approximately) display refresh rate
    virtual void start(FrameCallback on_frame) = 0;

    // Stop the render loop
    virtual void stop() = 0;

    // Request a frame be rendered (marks dirty, callback fires on next vsync)
    virtual void request_frame() = 0;

    // Is the loop currently running?
    virtual bool is_running() const = 0;
};

} // namespace pulp::render
