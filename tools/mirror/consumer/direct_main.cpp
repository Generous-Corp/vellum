#include <pulp/canvas/canvas.hpp>

int main() {
    const auto color = pulp::canvas::Color::from_hsv({210.0f, 1.0f, 1.0f});
    return color.r8() == 0 && color.g8() == 128 && color.b8() == 255 ? 0 : 1;
}
