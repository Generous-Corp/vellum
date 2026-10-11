#include <pulp/canvas/canvas.hpp>
#include <pulp/canvas/bidi.hpp>
int main() {
    const auto c = pulp::canvas::Color::from_hsv({210.0f, 1.0f, 1.0f});
    if (c.r8() != 0 || c.g8() != 128 || c.b8() != 255)
        return 1;
    const auto bidi = pulp::canvas::BidiAnalyzer::analyze("hello \xD7\xA9");
    return !bidi.runs.empty() && bidi.used_sheenbidi ? 0 : 2;
}
