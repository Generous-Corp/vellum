#pragma once

#include <string>
#include <optional>
#include <vector>
#include <cstdint>

namespace pulp::platform {

// Cross-platform clipboard access
class Clipboard {
public:
    // Text clipboard
    static bool set_text(const std::string& text);
    static std::optional<std::string> get_text();
    static bool has_text();

    // Binary data clipboard (custom pasteboard type)
    static bool set_data(const std::string& type, const std::vector<uint8_t>& data);
    static std::optional<std::vector<uint8_t>> get_data(const std::string& type);
};

} // namespace pulp::platform
