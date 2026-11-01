#include <vellum/rmlui/backend.hpp>

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>
#include <system_error>
#include <utility>

#if !defined(_WIN32)
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#else
#define NOMINMAX
#include <windows.h>
#endif

namespace vellum::rmlui {

RootedAssetProvider::RootedAssetProvider(std::filesystem::path root,
                                         std::vector<AssetManifestEntry> manifest,
                                         Sha256Hasher sha256_hex, std::uint64_t max_bytes)
    : root_(std::move(root)), manifest_(std::move(manifest)), sha256_hex_(std::move(sha256_hex)),
      max_bytes_(max_bytes) {}

Status RootedAssetProvider::load(std::string_view path, Asset& out) {
    if (path.empty() || !sha256_hex_)
        return Status::rejected;
    const std::filesystem::path relative(path);
    if (relative.is_absolute())
        return Status::rejected;
    for (const auto& component : relative) {
        if (component == ".." || component == ".")
            return Status::rejected;
    }

    const auto manifest =
        std::find_if(manifest_.begin(), manifest_.end(),
                     [&](const AssetManifestEntry& entry) { return entry.path == path; });
    if (manifest == manifest_.end() || manifest->sha256.size() != 64)
        return Status::rejected;

    std::error_code ec;
    const auto root = std::filesystem::weakly_canonical(root_, ec);
    if (ec)
        return Status::missing_asset;
    const auto candidate = std::filesystem::weakly_canonical(root / relative, ec);
    if (ec)
        return Status::rejected;
    const auto confined = candidate.lexically_relative(root);
    const auto first = confined.begin();
    if (confined.empty() || first == confined.end() || *first == "..")
        return Status::rejected;
    if (!std::filesystem::is_regular_file(candidate, ec) || ec)
        return Status::missing_asset;
    const auto size = std::filesystem::file_size(candidate, ec);
    if (ec)
        return Status::missing_asset;
    if (size > max_bytes_)
        return Status::rejected;

    std::string bytes;
#if !defined(_WIN32)
    // Keep the confinement check and the read on one opened file description.
    // The descriptor pins the inode after open, while O_NOFOLLOW prevents a
    // final symlink swap from bypassing the canonical-path check. Read at most
    // max_bytes_ + 1 bytes so a growth race can never cause an unbounded
    // allocation before the size/digest check below.
    const int fd = ::open(candidate.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0)
        return errno == ENOENT ? Status::missing_asset : Status::rejected;
    struct stat opened_stat{};
    const bool stat_ok = ::fstat(fd, &opened_stat) == 0;
    if (!stat_ok || !S_ISREG(opened_stat.st_mode) || opened_stat.st_size < 0 ||
        static_cast<std::uint64_t>(opened_stat.st_size) > max_bytes_) {
        ::close(fd);
        return stat_ok && opened_stat.st_size > 0 &&
                       static_cast<std::uint64_t>(opened_stat.st_size) > max_bytes_
                   ? Status::rejected
                   : Status::missing_asset;
    }
    const auto opened_size = static_cast<std::size_t>(opened_stat.st_size);
    const auto bounded_limit =
        max_bytes_ == std::numeric_limits<std::size_t>::max() ? max_bytes_ : max_bytes_ + 1;
    bytes.reserve(std::min<std::uint64_t>(max_bytes_, opened_size));
    std::array<char, 8192> chunk{};
    while (bytes.size() <= max_bytes_) {
        const auto remaining = bounded_limit - bytes.size();
        if (remaining == 0)
            break;
        const auto want = std::min<std::size_t>(chunk.size(), remaining);
        const auto count = ::read(fd, chunk.data(), want);
        if (count == 0)
            break;
        if (count < 0) {
            if (errno == EINTR)
                continue;
            ::close(fd);
            return Status::rejected;
        }
        bytes.append(chunk.data(), static_cast<std::size_t>(count));
    }
    ::close(fd);
#else
    // Open the final path as a handle with reparse-point traversal disabled.
    // The handle pins the object for the bounded read, matching the POSIX
    // descriptor path above and preventing a check-then-open replacement race.
    const auto native_path = candidate.native();
    const HANDLE handle =
        ::CreateFileW(native_path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                      FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
    if (handle == INVALID_HANDLE_VALUE)
        return ::GetLastError() == ERROR_FILE_NOT_FOUND ? Status::missing_asset : Status::rejected;
    BY_HANDLE_FILE_INFORMATION information{};
    const bool info_ok = ::GetFileInformationByHandle(handle, &information) != 0;
    const bool regular = info_ok && !(information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT);
    const std::uint64_t opened_size =
        (static_cast<std::uint64_t>(information.nFileSizeHigh) << 32) | information.nFileSizeLow;
    if (!regular || opened_size > max_bytes_) {
        ::CloseHandle(handle);
        return regular && opened_size > max_bytes_ ? Status::rejected : Status::missing_asset;
    }
    const auto bounded_limit =
        max_bytes_ == std::numeric_limits<std::size_t>::max() ? max_bytes_ : max_bytes_ + 1;
    bytes.reserve(static_cast<std::size_t>(opened_size));
    std::array<char, 8192> chunk{};
    while (bytes.size() <= max_bytes_) {
        const auto remaining = bounded_limit - bytes.size();
        if (remaining == 0)
            break;
        const auto want = static_cast<DWORD>(std::min<std::size_t>(chunk.size(), remaining));
        DWORD count = 0;
        if (!::ReadFile(handle, chunk.data(), want, &count, nullptr)) {
            ::CloseHandle(handle);
            return Status::rejected;
        }
        if (count == 0)
            break;
        bytes.append(chunk.data(), count);
    }
    ::CloseHandle(handle);
#endif
    if (bytes.size() != size)
        return Status::rejected;
    std::string digest;
    try {
        digest = sha256_hex_(bytes);
    } catch (...) {
        return Status::rejected;
    }
    if (digest != manifest->sha256)
        return Status::rejected;
    out = Asset{std::string(path), std::move(bytes)};
    return Status::ok;
}

} // namespace vellum::rmlui
