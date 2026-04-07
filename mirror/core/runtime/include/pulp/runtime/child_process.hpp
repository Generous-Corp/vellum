#pragma once

// Launch and manage child processes with stdout/stderr capture.

#include <string>
#include <string_view>
#include <vector>
#include <optional>

namespace pulp::runtime {

struct ProcessResult {
    int exit_code = -1;
    std::string stdout_output;
    std::string stderr_output;
};

/// Launch a child process and wait for it to complete.
/// Returns nullopt if the process could not be started.
std::optional<ProcessResult> run_process(
    std::string_view executable,
    const std::vector<std::string>& args = {},
    std::string_view working_dir = "",
    int timeout_ms = 0  // 0 = no timeout
);

/// Launch a child process without waiting. Returns the PID, or -1 on failure.
int launch_process(std::string_view executable,
                   const std::vector<std::string>& args = {});

/// Check if a process with the given PID is running.
bool is_process_running(int pid);

}  // namespace pulp::runtime
