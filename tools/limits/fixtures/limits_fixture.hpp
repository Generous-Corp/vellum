#pragma once

#include <cstddef>
#include <cstdint>

namespace vellum::fixture {

// vellum-limits-begin

/// A 32-bit count.
inline constexpr std::uint32_t kFixtureSlots = 4096U;
/// A size_t count.
inline constexpr std::size_t kFixtureDepth = 64U;
/// A float with a fraction.
inline constexpr float kFixtureStroke = 2.5F;
/// A double in exponent form.
inline constexpr double kFixtureHuge = 3.4028234663852886e38;

// vellum-limits-end

}  // namespace vellum::fixture
