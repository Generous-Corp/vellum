#include <pulp/runtime/mac_address.hpp>

#include <array>
#include <cctype>
#include <cstdio>

namespace pulp::runtime {

namespace {

// Parse a single hex digit. Returns -1 on failure.
int hex_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
    if (c >= 'A' && c <= 'F') return 10 + (c - 'A');
    return -1;
}

// Strip surrounding ASCII whitespace.
std::string_view trim(std::string_view s) {
    auto is_ws = [](unsigned char c) {
        return c == ' ' || c == '\t' || c == '\r' || c == '\n';
    };
    while (!s.empty() && is_ws(static_cast<unsigned char>(s.front()))) s.remove_prefix(1);
    while (!s.empty() && is_ws(static_cast<unsigned char>(s.back())))  s.remove_suffix(1);
    return s;
}

// Try to walk `s` consuming 12 hex digits. Group separators allowed
// only when they match `expected_separator`, OR when expected_separator
// is '\0' meaning "no separators allowed" (plain-hex form). For dotted
// Cisco form, separator must appear after every 4 hex digits; for the
// colon/hyphen forms, after every 2 digits.
struct ParseShape {
    char separator;   // ':' '-' '.' or '\0' for plain hex
    int  group_size;  // hex digits per group (2 for : / -, 4 for ., 12 for plain)
};

std::optional<MacAddress> parse_with_shape(std::string_view s, ParseShape shape) {
    std::array<uint8_t, 6> out{};
    size_t hex_seen = 0;
    int group_hex = 0;
    for (size_t i = 0; i < s.size(); ++i) {
        char c = s[i];
        if (c == shape.separator && shape.separator != '\0') {
            if (group_hex != shape.group_size) return std::nullopt;
            group_hex = 0;
            continue;
        }
        int v = hex_value(c);
        if (v < 0) return std::nullopt;
        if (hex_seen >= 12) return std::nullopt;
        auto& octet = out[hex_seen / 2];
        if ((hex_seen % 2) == 0) {
            octet = static_cast<uint8_t>(v << 4);
        } else {
            octet = static_cast<uint8_t>(octet | static_cast<uint8_t>(v));
        }
        ++hex_seen;
        ++group_hex;
    }
    if (hex_seen != 12) return std::nullopt;
    // The final group must also be complete.
    if (shape.separator != '\0' && group_hex != shape.group_size) return std::nullopt;
    return MacAddress(out[0], out[1], out[2], out[3], out[4], out[5]);
}

}  // namespace

std::optional<MacAddress> MacAddress::parse(std::string_view text) {
    auto s = trim(text);
    if (s.empty()) return std::nullopt;
    // Sniff first separator to pick a shape.
    char first_sep = '\0';
    for (char c : s) {
        if (c == ':' || c == '-' || c == '.') {
            first_sep = c;
            break;
        }
    }
    ParseShape shape{};
    if (first_sep == ':' || first_sep == '-') {
        shape = {first_sep, 2};
    } else if (first_sep == '.') {
        shape = {'.', 4};
    } else {
        shape = {'\0', 12};
    }
    return parse_with_shape(s, shape);
}

std::string MacAddress::to_string() const {
    char buf[18];
    std::snprintf(buf, sizeof(buf), "%02x:%02x:%02x:%02x:%02x:%02x",
                  octets_[0], octets_[1], octets_[2],
                  octets_[3], octets_[4], octets_[5]);
    return std::string(buf);
}

std::string MacAddress::to_string_hyphens() const {
    char buf[18];
    std::snprintf(buf, sizeof(buf), "%02x-%02x-%02x-%02x-%02x-%02x",
                  octets_[0], octets_[1], octets_[2],
                  octets_[3], octets_[4], octets_[5]);
    return std::string(buf);
}

bool MacAddress::is_null() const {
    for (auto o : octets_) if (o != 0) return false;
    return true;
}

bool MacAddress::is_broadcast() const {
    for (auto o : octets_) if (o != 0xff) return false;
    return true;
}

}  // namespace pulp::runtime
