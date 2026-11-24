#include <vellum/canvas/canvas.hpp>
#include <vellum/view/screenshot.hpp>
#include <vellum/view/buttons.hpp>
#include <vellum/view/view.hpp>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <string>
#include <utility>
#include <vector>

namespace {

class CenteredLabel final : public vellum::view::View {
public:
    void paint(vellum::canvas::Canvas& canvas) override {
        const auto b = bounds();
        canvas.set_fill_color(vellum::canvas::Color::rgba8(0, 0, 0, 255));
        canvas.fill_rect(0.0f, 0.0f, b.width, b.height);
        canvas.set_fill_color(vellum::canvas::Color::rgba8(255, 255, 255, 255));
        canvas.set_font("system", 14.0f);
        measured = canvas.measure_text(label);
        canvas.fill_text(label, (b.width - measured) / 2.0f, b.height * 0.65f);
    }

    const std::string label = "Retry";
    float measured = 0.0f;
};

} // namespace

int main() {
    CenteredLabel view;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    const auto pixels = vellum::view::render_to_rgba(view, 72, 26, 1.0f, &width, &height);
    if (pixels.empty() || width != 72 || height != 26) {
        std::fprintf(stderr, "centered label did not render\n");
        return 1;
    }

    int left = static_cast<int>(width);
    int right = -1;
    for (std::uint32_t y = 0; y < height; ++y) {
        for (std::uint32_t x = 0; x < width; ++x) {
            const auto offset = (static_cast<std::size_t>(y) * width + x) * 4U;
            if (pixels[offset] < 180 || pixels[offset + 1] < 180 || pixels[offset + 2] < 180)
                continue;
            left = std::min(left, static_cast<int>(x));
            right = std::max(right, static_cast<int>(x));
        }
    }

    if (!(view.measured <= 72.0f && left >= 0 && right >= left && right < 72)) {
        std::fprintf(stderr, "label measured=%g ink=%d..%d\n", view.measured, left, right);
        return 1;
    }
    std::printf("centered label measured=%g ink=%d..%d\n", view.measured, left, right);
    return 0;
}
