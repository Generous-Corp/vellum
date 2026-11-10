#include <vellum/runtime/child_process.hpp>

#include <limits>

namespace vellum::runtime {

std::optional<ProcessResult> run_process(
    std::string_view executable,
    const std::vector<std::string>& args,
    std::string_view working_dir,
    int timeout_ms)
{
    vellum::platform::ProcessOptions options;
    options.working_directory = std::string(working_dir);
    options.timeout_ms = timeout_ms;
    options.max_output_bytes = std::numeric_limits<size_t>::max();

    vellum::platform::ChildProcess process;
    if (!process.start(std::string(executable), args, options))
        return std::nullopt;

    return process.wait();
}

}  // namespace vellum::runtime
