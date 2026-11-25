#include <vellum/canvas/font_resolver.hpp>
#include <vellum/view/buttons.hpp>
#include <vellum/view/screenshot.hpp>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

int main() {
    // Imported CSS keeps Chromium's reading: `system` is a literal family, and
    // only `system-ui` names the platform UI font.
    const auto system_family = vellum::canvas::platform_family_for_css_generic("system");
    const auto system_ui_family = vellum::canvas::platform_family_for_css_generic("system-ui");
    if (!system_family.empty() || system_ui_family.empty()) {
        std::fprintf(stderr, "CSS generic mapping changed for system / system-ui\n");
        return 1;
    }

    vellum::view::TextButton button("Settings");
    button.set_bounds({0.0f, 0.0f, 72.0f, 36.0f});

    std::uint32_t width = 0;
    std::uint32_t height = 0;
    const auto pixels = vellum::view::render_to_rgba(button, 72, 36, 1.0f, &width, &height);
    if (pixels.empty() || width != 72 || height != 36) {
        std::fprintf(stderr, "text button did not render\n");
        return 1;
    }

    int left = static_cast<int>(width);
    int right = -1;
    for (std::uint32_t y = 0; y < height; ++y) {
        for (std::uint32_t x = 0; x < width; ++x) {
            const auto offset = (static_cast<std::size_t>(y) * width + x) * 4U;
            // The default secondary label is text.primary (220, 220, 230).
            if (pixels[offset] < 180 || pixels[offset + 1] < 180 || pixels[offset + 2] < 180)
                continue;
            left = std::min(left, static_cast<int>(x));
            right = std::max(right, static_cast<int>(x));
        }
    }

    const int right_margin = static_cast<int>(width) - 1 - right;
    if (right < left || std::abs(left - right_margin) > 1) {
        std::fprintf(stderr, "text button ink=%d..%d\n", left, right);
        return 1;
    }
    std::printf("text button ink=%d..%d\n", left, right);
    return 0;
}
