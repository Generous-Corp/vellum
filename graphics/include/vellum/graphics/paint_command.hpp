#pragma once

#include <vellum/graphics/scene.hpp>

#include <string>
#include <vector>

namespace vellum::graphics {

/// A renderer-neutral, absolute-position paint operation.
///
/// Scene traversal belongs to the shared C++ graphics core. Platform backends
/// consume this stable command stream instead of independently walking the
/// retained tree and reimplementing parent-coordinate accumulation.
struct PaintCommand final {
    enum class Kind {
        rectangle,
        text,
    };

    Kind kind = Kind::rectangle;
    std::string node_id;
    Rect bounds{};
    Color fill{};
    float corner_radius = 0.0F;
    std::string text;
    float font_size = 14.0F;
};

[[nodiscard]] std::vector<PaintCommand> make_paint_commands(const Scene& scene);

}  // namespace vellum::graphics
