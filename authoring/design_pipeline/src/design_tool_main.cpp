// vellum-design-tool: the native half of `vellum design` for offline verbs.
// The CLI passes the verb and its arguments unchanged.

#include "design_tool_verbs.hpp"

#include <iostream>
#include <string>
#include <string_view>
#include <vector>

namespace {

struct Verb {
    std::string_view name;
    int (*run)(const std::vector<std::string>&);
    std::string_view summary;
};

constexpr Verb kVerbs[] = {
    {"md-lint", vellum::design_tool::run_lint, "lint a DESIGN.md file"},
    {"md-diff", vellum::design_tool::run_diff, "diff the tokens of two DESIGN.md files"},
    {"compile", vellum::design_tool::run_compile, "compile the design manifest and binding prompt"},
    {"lint-adherence", vellum::design_tool::run_lint_adherence,
     "lint UI JS against the design contract"},
    {"record", vellum::design_tool::run_record, "maintain the project design ledger"},
    {"export-tokens", vellum::design_tool::run_export_tokens, "export a theme as design tokens"},
};

void print_usage(std::ostream& out) {
    out << "Usage: vellum-design-tool <verb> [options]\n\nVerbs:\n";
    for (const auto& verb : kVerbs) {
        out << "  " << verb.name << std::string(16 - verb.name.size(), ' ') << verb.summary
            << "\n";
    }
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        print_usage(std::cerr);
        return 2;
    }
    const std::string_view name = argv[1];
    if (name == "-h" || name == "--help") {
        print_usage(std::cout);
        return 0;
    }
    std::vector<std::string> rest(argv + 2, argv + argc);
    for (const auto& verb : kVerbs) {
        if (verb.name == name) return verb.run(rest);
    }
    std::cerr << "vellum-design-tool: unknown verb '" << name << "'\n";
    print_usage(std::cerr);
    return 2;
}
