// Offline `vellum design` verbs. Each takes the arguments after its verb and
// returns the process exit code.
#pragma once

#include <filesystem>
#include <string>
#include <vector>

namespace vellum::design_tool {

// Whole-file text read; empty when the file cannot be read.
std::string read_text_file(const std::filesystem::path& p);
// Atomic write through a sibling temporary file and a rename.
bool write_text_file(const std::filesystem::path& p, const std::string& content);

int run_lint(const std::vector<std::string>& rest);
int run_diff(const std::vector<std::string>& rest);
int run_compile(const std::vector<std::string>& rest);
int run_lint_adherence(const std::vector<std::string>& rest);
int run_record(const std::vector<std::string>& rest);
int run_export_tokens(const std::vector<std::string>& rest);

}  // namespace vellum::design_tool
