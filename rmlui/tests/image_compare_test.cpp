// The renderer comparison every consumer uses to hold a GPU frame to
// SoftRenderer: what counts as within the bound, the mean, and the cases that
// must compare as entirely different.
#include <vellum/rmlui/image_compare.hpp>

#include "test_support.hpp"

#include <cstdint>
#include <vector>

using namespace vellum::rmlui;

int main() {
    std::vector<std::uint8_t> a(4u * 200u, 100u);
    auto b = a;

    auto d = compare_rgba(a, b);
    VELLUM_RMLUI_CHECK(d.mean == 0.0 && d.within == 1.0 && within_renderer_tolerance(d));

    // One pixel off by exactly the bound stays within it; one more is not.
    b[0] = 100u + kPerChannelBound;
    d = compare_rgba(a, b);
    VELLUM_RMLUI_CHECK(d.within == 1.0);
    b[0] = 100u + kPerChannelBound + 1;
    d = compare_rgba(a, b);
    VELLUM_RMLUI_CHECK(d.within == 199.0 / 200.0);
    VELLUM_RMLUI_CHECK(d.mean == 9.0 / 800.0);

    // Two of 200 pixels out of bound is 0.99 within: outside the tolerance
    // even though the mean is tiny.
    b[4] = 100u + kPerChannelBound + 1;
    d = compare_rgba(a, b);
    VELLUM_RMLUI_CHECK(d.mean < kMaxMeanDifference && !within_renderer_tolerance(d));

    // A uniform shift inside the bound still fails on the mean.
    auto shifted = a;
    for (auto& v : shifted)
        v = static_cast<std::uint8_t>(v + 3u);
    d = compare_rgba(a, shifted);
    VELLUM_RMLUI_CHECK(d.within == 1.0 && d.mean == 3.0 && !within_renderer_tolerance(d));

    // Mismatched, empty or partial-pixel buffers are entirely different.
    VELLUM_RMLUI_CHECK(!within_renderer_tolerance(compare_rgba(a, std::vector<std::uint8_t>(4))));
    VELLUM_RMLUI_CHECK(!within_renderer_tolerance(compare_rgba({}, {})));
    VELLUM_RMLUI_CHECK(!within_renderer_tolerance(
        compare_rgba(std::vector<std::uint8_t>(6, 0u), std::vector<std::uint8_t>(6, 0u))));
    return 0;
}
