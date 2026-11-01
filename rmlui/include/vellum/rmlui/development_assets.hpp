#pragma once

#include <vellum/rmlui/backend.hpp>

#include <cstdint>
#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <string_view>

namespace vellum::rmlui {

// Development-only asset provider for hot reload. It serves any regular file
// under a root with no manifest and no digest check, and remembers the bytes
// it served so poll() can report an on-disk edit. Ship RootedAssetProvider;
// this lives in its own target (VellumRmlUi::DevAssets) so a release product
// that does not link it cannot reach it.
//
// Typical loop, once per UI frame or on a file-watcher tick:
//   if (assets.poll()) backend.reload_document();
class DevelopmentAssetProvider final : public AssetProvider {
  public:
    explicit DevelopmentAssetProvider(std::filesystem::path root,
                                      std::uint64_t max_bytes = 16 * 1024 * 1024);

    Status load(std::string_view path, Asset& out) override;

    // True when a file served since the previous poll() now reads differently
    // (edited, replaced or removed). Content is compared, not timestamps, so
    // two edits within one filesystem tick are still seen.
    bool poll();

    // Paths served so far, relative to the root.
    [[nodiscard]] std::size_t tracked_files() const noexcept {
        return served_.size();
    }

  private:
    Status read(std::string_view path, std::string& bytes) const;

    std::filesystem::path root_;
    std::uint64_t max_bytes_;
    // The bytes last served or seen per path; nullopt once a file is gone.
    std::map<std::string, std::optional<std::string>, std::less<>> served_;
};

} // namespace vellum::rmlui
