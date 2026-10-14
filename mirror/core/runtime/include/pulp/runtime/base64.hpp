#pragma once

// Base64 encode/decode

#include <string>
#include <string_view>
#include <vector>
#include <cstdint>
#include <optional>

namespace pulp::runtime {

/// Encode binary data to base64 string
std::string base64_encode(const uint8_t* data, size_t length);
std::string base64_encode(std::string_view input);

/// Decode base64 string to binary data. Returns nullopt on invalid input.
std::optional<std::vector<uint8_t>> base64_decode(std::string_view input);

}  // namespace pulp::runtime
