#include <vellum/rmlui/development_assets.hpp>

#include <fstream>
#include <iterator>
#include <optional>
#include <system_error>
#include <utility>

namespace vellum::rmlui {

DevelopmentAssetProvider::DevelopmentAssetProvider(std::filesystem::path root,
                                                   std::uint64_t max_bytes)
    : root_(std::move(root)), max_bytes_(max_bytes) {}

Status DevelopmentAssetProvider::read(std::string_view path, std::string& bytes) const {
    if (path.empty())
        return Status::rejected;
    const std::filesystem::path relative(path);
    if (relative.is_absolute())
        return Status::rejected;
    for (const auto& component : relative)
        if (component == ".." || component == ".")
            return Status::rejected;

    std::error_code ec;
    const auto root = std::filesystem::weakly_canonical(root_, ec);
    if (ec)
        return Status::missing_asset;
    const auto candidate = std::filesystem::weakly_canonical(root / relative, ec);
    if (ec)
        return Status::rejected;
    const auto confined = candidate.lexically_relative(root);
    if (confined.empty() || *confined.begin() == "..")
        return Status::rejected;
    if (!std::filesystem::is_regular_file(candidate, ec) || ec)
        return Status::missing_asset;
    const auto size = std::filesystem::file_size(candidate, ec);
    if (ec)
        return Status::missing_asset;
    if (size > max_bytes_)
        return Status::rejected;
    std::ifstream stream(candidate, std::ios::binary);
    if (!stream)
        return Status::missing_asset;
    bytes.assign(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
    if (bytes.size() > max_bytes_)
        return Status::rejected;
    return Status::ok;
}

Status DevelopmentAssetProvider::load(std::string_view path, Asset& out) {
    std::string bytes;
    const auto status = read(path, bytes);
    if (status != Status::ok)
        return status;
    served_.insert_or_assign(std::string(path), bytes);
    out = Asset{std::string(path), std::move(bytes)};
    return Status::ok;
}

bool DevelopmentAssetProvider::poll() {
    bool changed = false;
    for (auto& [path, served] : served_) {
        std::string current;
        std::optional<std::string> now;
        if (read(path, current) == Status::ok)
            now = std::move(current);
        if (now != served) {
            changed = true;
            served = std::move(now);
        }
    }
    return changed;
}

} // namespace vellum::rmlui
