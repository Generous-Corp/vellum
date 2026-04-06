// SDL3 Native Surface Extraction implementation
// Uses SDL3's property system to extract platform-specific handles.

#include <pulp/render/sdl3_surface.hpp>
#include <pulp/runtime/log.hpp>

#ifdef PULP_HAS_SDL3

#include <SDL3/SDL.h>
#include <SDL3/SDL_properties.h>

#ifdef __APPLE__
#include <SDL3/SDL_metal.h>
#endif

namespace pulp::render {

Sdl3SurfaceInfo extract_sdl3_surface(SDL_Window* window) {
    Sdl3SurfaceInfo info;
    if (!window) return info;

    SDL_PropertiesID props = SDL_GetWindowProperties(window);
    if (!props) {
        runtime::log_error("SDL3: failed to get window properties");
        return info;
    }

#ifdef __APPLE__
    // macOS/iOS: Create a Metal layer via SDL3's Metal API
    SDL_MetalView metal_view = SDL_Metal_CreateView(window);
    if (metal_view) {
        info.metal_layer = SDL_Metal_GetLayer(metal_view);
        if (info.metal_layer) {
            info.backend = Sdl3SurfaceInfo::Backend::metal;
            runtime::log_info("SDL3: extracted Metal layer from window");
        }
    }

#elif defined(_WIN32)
    // Windows: HWND directly from properties
    info.hwnd = SDL_GetPointerProperty(props, SDL_PROP_WINDOW_WIN32_HWND_POINTER, nullptr);
    info.hinstance = SDL_GetPointerProperty(props, SDL_PROP_WINDOW_WIN32_INSTANCE_POINTER, nullptr);
    if (info.hwnd) {
        info.backend = Sdl3SurfaceInfo::Backend::d3d12;
        runtime::log_info("SDL3: extracted HWND from window");
    }

#elif defined(__linux__)
    // Try X11 first, then Wayland
    void* x11_display = SDL_GetPointerProperty(props, SDL_PROP_WINDOW_X11_DISPLAY_POINTER, nullptr);
    auto x11_window = static_cast<uint64_t>(SDL_GetNumberProperty(props, SDL_PROP_WINDOW_X11_WINDOW_NUMBER, 0));

    if (x11_display && x11_window) {
        info.x11_display = x11_display;
        info.x11_window = x11_window;
        info.backend = Sdl3SurfaceInfo::Backend::vulkan_x11;
        runtime::log_info("SDL3: extracted X11 display/window");
    } else {
        // Try Wayland
        info.wayland_display = SDL_GetPointerProperty(props, SDL_PROP_WINDOW_WAYLAND_DISPLAY_POINTER, nullptr);
        info.wayland_surface = SDL_GetPointerProperty(props, SDL_PROP_WINDOW_WAYLAND_SURFACE_POINTER, nullptr);
        if (info.wayland_display && info.wayland_surface) {
            info.backend = Sdl3SurfaceInfo::Backend::vulkan_wayland;
            runtime::log_info("SDL3: extracted Wayland display/surface");
        }
    }
#endif

    if (!info.is_valid()) {
        runtime::log_error("SDL3: could not extract native surface from window");
    }

    return info;
}

} // namespace pulp::render

#else // !PULP_HAS_SDL3

namespace pulp::render {

Sdl3SurfaceInfo extract_sdl3_surface(SDL_Window*) {
    return {}; // SDL3 not available
}

} // namespace pulp::render

#endif // PULP_HAS_SDL3
