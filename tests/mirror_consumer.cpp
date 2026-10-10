#include <pulp/canvas/canvas.hpp>
int main() {
    const auto c = pulp::canvas::Color::from_hsv({210.0f, 1.0f, 1.0f});
    return c.r8() == 0 && c.g8() == 128 && c.b8() == 255 ? 0 : 1;
}
