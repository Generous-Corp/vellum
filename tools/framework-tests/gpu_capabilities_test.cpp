// The pure parts of the render unit's device description: the format table,
// tier selection and WGSL diagnostic formatting. No device is created, so
// this runs on every host that builds the render unit.

#include "framework_test.hpp"

#include <vellum/render/gpu_capabilities.hpp>
#include <vellum/render/wgsl_diagnostics.hpp>

#include <algorithm>
#include <cstdint>
#include <vector>

namespace {

using namespace vellum::render;

GpuDeviceFacts hardware_facts() {
    GpuDeviceFacts facts;
    facts.available = true;
    facts.adapter = GpuAdapterKind::integrated;
    facts.max_texture_dimension_2d = 8192;
    facts.max_color_attachments = 8;
    facts.max_storage_textures_per_shader_stage = 4;
    facts.max_compute_invocations_per_workgroup = 256;
    return facts;
}

}  // namespace

FRAMEWORK_TEST(no_device_and_the_null_backend_render_nothing) {
    GpuDeviceFacts none;
    CHECK(pick_tier(none) == GpuRenderTier::none);
    auto null_backend = hardware_facts();
    null_backend.null_backend = true;
    CHECK(pick_tier(null_backend) == GpuRenderTier::none);
}

FRAMEWORK_TEST(a_cpu_adapter_is_basic_whatever_its_limits) {
    auto cpu = hardware_facts();
    cpu.adapter = GpuAdapterKind::cpu;
    cpu.max_texture_dimension_2d = 16384;
    CHECK(pick_tier(cpu) == GpuRenderTier::basic);
}

FRAMEWORK_TEST(hardware_tiers_follow_limits_and_adapter_kind) {
    CHECK(pick_tier(hardware_facts()) == GpuRenderTier::standard);

    auto low = hardware_facts();
    low.max_storage_textures_per_shader_stage = 2;
    CHECK(pick_tier(low) == GpuRenderTier::basic);
    low = hardware_facts();
    low.max_color_attachments = 2;
    CHECK(pick_tier(low) == GpuRenderTier::basic);

    auto discrete = hardware_facts();
    discrete.adapter = GpuAdapterKind::discrete;
    CHECK(pick_tier(discrete) == GpuRenderTier::high);

    auto large = hardware_facts();
    large.max_texture_dimension_2d = 16384;
    CHECK(pick_tier(large) == GpuRenderTier::standard);
    large.features.float32_blendable = true;
    CHECK(pick_tier(large) == GpuRenderTier::high);

    CHECK(gpu_render_tier_name(GpuRenderTier::standard) == "standard");
}

FRAMEWORK_TEST(the_format_table_follows_webgpu_and_its_optional_features) {
    GpuDeviceFeatures base;
    CHECK(gpu_format_support(GpuFormat::rgba8_unorm, base).storage);
    CHECK(gpu_format_support(GpuFormat::rgba8_unorm, base).multisample);
    CHECK(!gpu_format_support(GpuFormat::bgra8_unorm, base).storage);
    CHECK(!gpu_format_support(GpuFormat::rg11b10_ufloat, base).renderable);
    CHECK(!gpu_format_support(GpuFormat::rgba32_float, base).multisample);
    CHECK(!gpu_format_support(GpuFormat::r32_float, base).blendable);
    CHECK(!gpu_format_support(GpuFormat::r32_uint, base).blendable);

    GpuDeviceFeatures extended;
    extended.bgra8_unorm_storage = true;
    extended.rg11b10_ufloat_renderable = true;
    extended.float32_blendable = true;
    extended.float32_filterable = true;
    CHECK(gpu_format_support(GpuFormat::bgra8_unorm, extended).storage);
    CHECK(gpu_format_support(GpuFormat::rg11b10_ufloat, extended).renderable);
    CHECK(gpu_format_support(GpuFormat::r32_float, extended).blendable);
    CHECK(gpu_format_support(GpuFormat::rgba32_float, extended).filterable);
}

FRAMEWORK_TEST(a_filled_capability_table_answers_the_format_queries) {
    GpuDeviceCapabilities caps;
    fill_format_table(caps);
    CHECK(caps.formats.size() == all_gpu_formats().size());
    CHECK((caps.sample_counts(GpuFormat::rgba8_unorm) == std::vector<std::uint32_t>{1, 4}));
    CHECK((caps.sample_counts(GpuFormat::rgba32_float) == std::vector<std::uint32_t>{1}));
    CHECK(caps.sample_counts(GpuFormat::rg11b10_ufloat).empty());
    for (auto format : caps.renderable_color_formats())
        CHECK(!gpu_format_is_depth(format));
    const auto storage = caps.storage_formats();
    CHECK(std::find(storage.begin(), storage.end(), GpuFormat::rgba8_unorm) != storage.end());
    CHECK(std::find(storage.begin(), storage.end(), GpuFormat::bgra8_unorm) == storage.end());

    CHECK(gpu_format_name(GpuFormat::depth24_plus_stencil8) == "depth24plus-stencil8");
    CHECK(gpu_format_bytes_per_pixel(GpuFormat::rgba16_float) == 8);
    CHECK(gpu_format_bytes_per_pixel(GpuFormat::depth32_float) == 0);
}

FRAMEWORK_TEST(wgsl_diagnostics_format_as_file_line_column) {
    WgslDiagnostic positioned;
    positioned.line = 3;
    positioned.column = 14;
    positioned.message = "unresolved identifier 'colour'";
    CHECK(format_wgsl_diagnostic("glow.wgsl", positioned) ==
          "glow.wgsl:3:14: error: unresolved identifier 'colour'");

    WgslDiagnostic unpositioned;
    unpositioned.severity = WgslDiagnostic::Severity::warning;
    unpositioned.message = "unused";
    CHECK(format_wgsl_diagnostic("", unpositioned) == "<wgsl>: warning: unused");

    CHECK(has_wgsl_errors({positioned, unpositioned}));
    CHECK(!has_wgsl_errors({unpositioned}));
    CHECK(format_wgsl_diagnostics("a.wgsl", {positioned, unpositioned}) ==
          "a.wgsl:3:14: error: unresolved identifier 'colour'\na.wgsl: warning: unused");
}

int main() { return framework_test::run_all(); }
