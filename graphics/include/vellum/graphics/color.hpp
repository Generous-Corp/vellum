#pragma once

#include <algorithm>
#include <cstdint>

namespace vellum::graphics {

struct Color final {
    float red = 0.0F;
    float green = 0.0F;
    float blue = 0.0F;
    float alpha = 1.0F;

    [[nodiscard]] static constexpr Color rgba(
        float red, float green, float blue, float alpha = 1.0F) noexcept {
        return {
            std::clamp(red, 0.0F, 1.0F),
            std::clamp(green, 0.0F, 1.0F),
            std::clamp(blue, 0.0F, 1.0F),
            std::clamp(alpha, 0.0F, 1.0F),
        };
    }

    [[nodiscard]] static constexpr Color hex(
        std::uint32_t rgb, float alpha = 1.0F) noexcept {
        return rgba(
            static_cast<float>((rgb >> 16U) & 0xFFU) / 255.0F,
            static_cast<float>((rgb >> 8U) & 0xFFU) / 255.0F,
            static_cast<float>(rgb & 0xFFU) / 255.0F,
            alpha);
    }

    [[nodiscard]] constexpr std::uint8_t red8() const noexcept {
        return static_cast<std::uint8_t>(red * 255.0F + 0.5F);
    }
    [[nodiscard]] constexpr std::uint8_t green8() const noexcept {
        return static_cast<std::uint8_t>(green * 255.0F + 0.5F);
    }
    [[nodiscard]] constexpr std::uint8_t blue8() const noexcept {
        return static_cast<std::uint8_t>(blue * 255.0F + 0.5F);
    }
};

}  // namespace vellum::graphics
