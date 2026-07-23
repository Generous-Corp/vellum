#pragma once

#include "component_registry.hpp"

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace vellum::app_host {

struct AutomationStep final {
    enum class Kind {
        press, input, key, focus, compose, assert_accessibility, assert_text,
        touch, command, service_result, expected_throw,
    };
    Kind kind;
    std::string node_id;
    std::string value;
};

struct Options final {
    std::filesystem::path bundle;
    std::filesystem::path capture;
    std::filesystem::path state_file;
    std::string service_capabilities;
    std::vector<AutomationStep> steps;
    std::vector<ComponentModuleSpec> components;
    std::optional<std::uint32_t> expected_width;
    std::optional<std::uint32_t> expected_height;
    bool no_window = false;
};

void print_usage();
std::optional<Options> parse_options(int argc, const char* argv[]);

}  // namespace vellum::app_host
