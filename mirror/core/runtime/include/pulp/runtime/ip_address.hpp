#pragma once

// IP address utilities — parsing, validation, local address queries.

#include <string>
#include <string_view>
#include <vector>
#include <cstdint>

namespace pulp::runtime {

/// Parse and validate an IPv4 address.
bool is_valid_ipv4(std::string_view address);

/// Get all local IPv4 addresses (excluding loopback).
std::vector<std::string> local_ipv4_addresses();

/// Get the primary local IPv4 address (first non-loopback).
std::string local_ipv4_address();

/// Get the hostname.
std::string hostname();

}  // namespace pulp::runtime
