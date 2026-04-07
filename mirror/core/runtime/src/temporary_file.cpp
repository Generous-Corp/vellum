#include <pulp/runtime/temporary_file.hpp>
#include <random>
#include <sstream>
#include <fstream>

namespace pulp::runtime {

TemporaryFile::TemporaryFile(std::string_view extension) {
    auto tmp_dir = std::filesystem::temp_directory_path();

    // Generate a unique filename
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<uint64_t> dist;

    for (int attempt = 0; attempt < 100; ++attempt) {
        std::ostringstream ss;
        ss << "pulp_" << std::hex << dist(gen);
        if (!extension.empty()) {
            if (extension[0] != '.')
                ss << '.';
            ss << extension;
        }

        auto candidate = tmp_dir / ss.str();
        if (!std::filesystem::exists(candidate)) {
            path_ = candidate;
            // Create the file so the path is reserved
            std::ofstream{path_};
            return;
        }
    }

    // Fallback: use a simple timestamp-based name
    std::ostringstream ss;
    ss << "pulp_tmp_" << std::hex << std::random_device{}();
    if (!extension.empty()) {
        if (extension[0] != '.')
            ss << '.';
        ss << extension;
    }
    path_ = tmp_dir / ss.str();
    std::ofstream{path_};
}

TemporaryFile::~TemporaryFile() {
    if (active_ && !path_.empty()) {
        std::error_code ec;
        std::filesystem::remove(path_, ec);
    }
}

TemporaryFile::TemporaryFile(TemporaryFile&& other) noexcept
    : path_(std::move(other.path_)), active_(other.active_) {
    other.active_ = false;
}

TemporaryFile& TemporaryFile::operator=(TemporaryFile&& other) noexcept {
    if (this != &other) {
        if (active_ && !path_.empty()) {
            std::error_code ec;
            std::filesystem::remove(path_, ec);
        }
        path_ = std::move(other.path_);
        active_ = other.active_;
        other.active_ = false;
    }
    return *this;
}

}  // namespace pulp::runtime
