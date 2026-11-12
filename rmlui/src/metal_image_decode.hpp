#pragma once
// The Metal renderer's image decode, internal to the module and declared here
// so its premultiplication can be tested directly.

#include <cstdint>
#include <vector>

namespace vellum::rmlui::detail {

// Decodes an encoded image (anything ImageIO reads) to premultiplied RGBA8,
// top row first. Premultiplication is round(c * a / 255), that is
// (c * a + 127) / 255 in integers: 0 at alpha 0, c at alpha 255. CoreGraphics
// does it when the image is drawn into a premultiplied RGBA8 bitmap;
// metal_image_decode_test.mm checks every (c, a) pair, so a change in
// CoreGraphics' rounding is caught.
bool decode_premultiplied(const std::vector<std::uint8_t>& encoded, int& width, int& height,
                          std::vector<std::uint8_t>& out);

} // namespace vellum::rmlui::detail
