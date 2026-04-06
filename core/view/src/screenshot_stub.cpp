#include <pulp/view/screenshot.hpp>

namespace pulp::view {

std::vector<uint8_t> render_to_png(View&, uint32_t, uint32_t, float, ScreenshotBackend) {
    return {};
}

bool render_to_file(View&, uint32_t, uint32_t, const std::string&, float, ScreenshotBackend) {
    return false;
}

} // namespace pulp::view
