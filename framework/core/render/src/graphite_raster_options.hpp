#pragma once

#include "include/gpu/graphite/ContextOptions.h"

namespace vellum::render::detail {

inline skgpu::graphite::ContextOptions graphite_raster_options() {
    skgpu::graphite::ContextOptions options;
    // Match the legacy editor's internal sample count. Intel Metal can
    // reject Graphite's multisample pipelines in DAW hosts.
    options.fInternalMultisampleCount = skgpu::graphite::SampleCount::k1;
    return options;
}

} // namespace vellum::render::detail
