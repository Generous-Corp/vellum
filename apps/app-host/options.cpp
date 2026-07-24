#include "options.hpp"

#include <algorithm>
#include <iostream>
#include <iterator>
#include <string_view>

namespace vellum::app_host {
namespace {

constexpr std::size_t kMaximumAutomationSteps = 1000U;
constexpr std::size_t kMaximumNodeIdBytes = 1024U;
constexpr std::size_t kMaximumInputBytes = 64U * 1024U;

bool valid_node_id(std::string_view value) {
    return !value.empty() && value.size() <= kMaximumNodeIdBytes &&
           value.find('\0') == std::string_view::npos;
}

bool valid_key(std::string_view value) {
    constexpr std::string_view supported[] = {
        "Enter", "Escape", "Backspace", "Tab", "ArrowUp", "ArrowDown",
        "ArrowLeft", "ArrowRight", "Home", "End", "Delete",
    };
    return std::find(std::begin(supported), std::end(supported), value) !=
           std::end(supported);
}

std::optional<std::uint32_t> positive_dimension(std::string_view text) {
    try {
        const auto value = std::stoul(std::string(text));
        if (value == 0U || value > 16384U) return std::nullopt;
        return static_cast<std::uint32_t>(value);
    } catch (...) {
        return std::nullopt;
    }
}

}  // namespace

void print_usage() {
    std::cerr << "usage: vellum-app-host [--bundle FILE] [--self-test|--no-window] "
                 "[--press NODE_ID] [--input NODE_ID TEXT] [--key NODE_ID KEY] "
                 "[--focus NODE_ID] [--compose NODE_ID TEXT] "
                 "[--assert-accessibility NODE_ID EXPECTED_JSON] "
                 "[--assert-text NODE_ID EXPECTED_TEXT] "
                 "[--touch NODE_ID EVENT_JSON] [--command COMMAND_ID] "
                 "[--service-result NODE_ID RESPONSE_JSON] "
                 "[--expected-throw NODE_ID EXPECTED_TEXT] "
                 "[--service-capabilities JSON] "
                 "[--component ID=DYLIB] [--state-file FILE] "
                 "[--expect-width N --expect-height N] [--capture PNG]\n";
}

std::optional<Options> parse_options(int argc, const char* argv[]) {
    Options options;
    for (int index = 1; index < argc; ++index) {
        const std::string_view argument = argv[index];
        if (argument == "--self-test" || argument == "--no-window") {
            options.no_window = true;
        } else if (argument == "--bundle" || argument == "--capture" ||
                   argument == "--state-file" || argument == "--press" ||
                   argument == "--command" ||
                   argument == "--service-capabilities" ||
                   argument == "--component" ||
                   argument == "--expect-width" ||
                   argument == "--expect-height") {
            if (++index >= argc) return std::nullopt;
            if (argument == "--bundle") options.bundle = argv[index];
            if (argument == "--capture") options.capture = argv[index];
            if (argument == "--state-file") {
                if (argv[index][0] == '\0') return std::nullopt;
                options.state_file = argv[index];
            }
            if (argument == "--service-capabilities") {
                const std::string value = argv[index];
                if (value.empty() || value.size() > kMaximumInputBytes) {
                    return std::nullopt;
                }
                options.service_capabilities = value;
            }
            if (argument == "--press") {
                const std::string node_id = argv[index];
                if (!valid_node_id(node_id)) return std::nullopt;
                options.steps.push_back({
                    AutomationStep::Kind::press, node_id, {},
                });
            }
            if (argument == "--command") {
                const std::string command = argv[index];
                if (!valid_node_id(command)) return std::nullopt;
                options.steps.push_back({
                    AutomationStep::Kind::command, command, {},
                });
            }
            if (argument == "--component") {
                ComponentModuleSpec spec;
                std::string error;
                if (!parse_component_module_spec(argv[index], spec, &error)) {
                    return std::nullopt;
                }
                options.components.push_back(std::move(spec));
            }
            if (argument == "--expect-width") {
                options.expected_width = positive_dimension(argv[index]);
                if (!options.expected_width) return std::nullopt;
            }
            if (argument == "--expect-height") {
                options.expected_height = positive_dimension(argv[index]);
                if (!options.expected_height) return std::nullopt;
            }
        } else if (argument == "--focus") {
            if (++index >= argc || !valid_node_id(argv[index])) return std::nullopt;
            options.steps.push_back({
                AutomationStep::Kind::focus, argv[index], {},
            });
        } else if (argument == "--input" || argument == "--key" ||
                   argument == "--compose" ||
                   argument == "--assert-accessibility" ||
                   argument == "--assert-text" || argument == "--touch" ||
                   argument == "--service-result" ||
                   argument == "--expected-throw") {
            if (index + 2 >= argc) return std::nullopt;
            const std::string node_id = argv[++index];
            const std::string value = argv[++index];
            if (!valid_node_id(node_id) ||
                ((argument == "--input" || argument == "--compose" ||
                  argument == "--assert-accessibility" ||
                  argument == "--assert-text" || argument == "--touch" ||
                  argument == "--service-result" ||
                  argument == "--expected-throw") &&
                 value.size() > kMaximumInputBytes) ||
                (argument == "--key" && !valid_key(value))) {
                return std::nullopt;
            }
            options.steps.push_back({
                argument == "--input" ? AutomationStep::Kind::input :
                argument == "--key" ? AutomationStep::Kind::key :
                argument == "--compose" ? AutomationStep::Kind::compose :
                argument == "--assert-accessibility"
                    ? AutomationStep::Kind::assert_accessibility :
                argument == "--assert-text" ? AutomationStep::Kind::assert_text :
                argument == "--touch" ? AutomationStep::Kind::touch :
                argument == "--service-result"
                    ? AutomationStep::Kind::service_result :
                    AutomationStep::Kind::expected_throw,
                node_id,
                value,
            });
        } else {
            return std::nullopt;
        }
        if (options.steps.size() > kMaximumAutomationSteps) return std::nullopt;
    }
    return options;
}

}  // namespace vellum::app_host
