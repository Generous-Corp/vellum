#pragma once

namespace vellum::graphics::testing {

void set_transformed_blur_sigmas(float sigmas) noexcept;
[[nodiscard]] float transformed_blur_sigmas() noexcept;

}  // namespace vellum::graphics::testing
