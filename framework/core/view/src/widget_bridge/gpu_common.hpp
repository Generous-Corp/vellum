#pragma once

#include <vellum/view/widget_bridge.hpp>

#include "gpu_error_scopes.hpp"

#ifdef PULP_HAS_SKIA
#include "webgpu/webgpu_cpp.h"
#endif

#include <cstdint>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

namespace vellum::view::detail {

struct WidgetBridgeGpuInfo {
    bool available = false;
    bool native_bridge = false;
    std::string backend;
    std::string backend_type;
    std::string name;
    std::string preferred_canvas_format;
    std::string vendor;
    std::string architecture;
    std::string description;
};

choc::value::Value gpu_adapter_info_to_value(const WidgetBridgeGpuInfo& info);
choc::value::Value gpu_descriptor_to_value(const WidgetBridgeGpuInfo& info);
WidgetBridgeGpuInfo widget_bridge_gpu_info(render::GpuSurface* gpu_surface);
std::string gpu_host_object_update_script(const WidgetBridgeGpuInfo& info);

} // namespace vellum::view::detail

namespace vellum::view {

struct WidgetBridge::NativeGpuBridgeState {
    struct CanvasContextState {
        uint32_t width = 1;
        uint32_t height = 1;
        std::string format = "bgra8unorm";
        uint32_t usage = 0;
        std::string alpha_mode = "opaque";
#ifdef PULP_HAS_SKIA
        wgpu::Texture texture;
#endif
        bool configured = false;
        // True once a pass has written the canvas texture. A canvas that holds
        // nothing yet has nothing for a load operation to keep.
        bool has_content = false;
    };

    struct TextureState {
        uint32_t width = 1;
        uint32_t height = 1;
        uint32_t depth_or_array_layers = 1;
        std::string format = "bgra8unorm";
        uint32_t usage = 0;
        uint32_t mip_level_count = 1;
        uint32_t sample_count = 1;
#ifdef PULP_HAS_SKIA
        wgpu::Texture texture;
#endif
        bool configured = false;
    };

    std::unordered_map<std::string, CanvasContextState> canvases;
    std::unordered_map<std::string, TextureState> textures;
    // Depth textures the buffered draws use, by the script's depth attachment
    // key. A depth texture belongs to its attachment, not to a colour target.
    // Type-erased because the GPU types are visible only where the draw code
    // reads it: a map from key to wgpu::Texture.
    std::shared_ptr<void> pass_depths;
    uint64_t next_texture_id = 1;
    detail::ScriptErrorScopes error_scopes;
};

} // namespace vellum::view
