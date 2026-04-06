#pragma once

// SDL3 Native Surface Extraction
// Extracts platform-specific window handles from SDL3 windows for Dawn/WebGPU
// surface creation. Each platform provides the appropriate handle type:
//
// macOS: NSWindow → NSView → CAMetalLayer
// Windows: HWND (direct, Dawn creates swap chain)
// Linux/X11: Display* + Window
// Linux/Wayland: wl_display* + wl_surface*

#include <cstdint>

// Forward declare SDL types to avoid SDL3 header dependency in public API
struct SDL_Window;

namespace pulp::render {

// Platform-specific surface info extracted from an SDL3 window
struct Sdl3SurfaceInfo {
    enum class Backend { none, metal, d3d12, vulkan_x11, vulkan_wayland };

    Backend backend = Backend::none;

    // Metal (macOS/iOS)
    void* metal_layer = nullptr;     // CAMetalLayer*

    // D3D12 (Windows)
    void* hwnd = nullptr;            // HWND
    void* hinstance = nullptr;       // HINSTANCE

    // Vulkan/X11 (Linux)
    void* x11_display = nullptr;     // Display*
    uint64_t x11_window = 0;        // Window (X11 Window is unsigned long)

    // Vulkan/Wayland (Linux)
    void* wayland_display = nullptr; // wl_display*
    void* wayland_surface = nullptr; // wl_surface*

    bool is_valid() const { return backend != Backend::none; }
};

// Extract native surface info from an SDL3 window.
// The window must already be created and visible.
// Returns info with backend==none if extraction fails.
Sdl3SurfaceInfo extract_sdl3_surface(SDL_Window* window);

} // namespace pulp::render
