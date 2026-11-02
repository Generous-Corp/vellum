#pragma once

#include <vellum/rmlui/backend.hpp>

#include <cstddef>
#include <string>
#include <string_view>

namespace vellum::rmlui::detail {

// RmlUi keeps its file, system and font-engine interfaces in process globals
// and allows exactly one Rml::Initialise() before Rml::Shutdown(). Every
// Backend therefore shares one reference-counted initialisation: the first
// acquire() installs the interfaces and initialises RmlUi, the last release()
// shuts it down.
bool acquire_runtime();
void release_runtime() noexcept;
std::size_t runtime_references() noexcept;

// RmlUi reads linked style sheets and templates through its single global
// FileInterface. The installed interface forwards each read to the
// AssetProvider of the Backend currently calling into RmlUi, so one
// instance's document can never read another instance's assets. A read made
// with no Backend in scope fails.
//
// Each Backend also loads its document under a source URL that starts with a
// per-instance prefix. RmlUi caches style sheets and templates process-wide by
// resolved path; the prefix keeps two instances whose skins use the same
// relative names from sharing each other's cached sheets. The prefix is
// removed again before the path reaches the AssetProvider.
class AssetScope {
  public:
    AssetScope(AssetProvider* assets, std::string_view prefix = {}) noexcept;
    ~AssetScope();
    AssetScope(const AssetScope&) = delete;
    AssetScope& operator=(const AssetScope&) = delete;

  private:
    AssetProvider* previous_;
    std::string_view previous_prefix_;
};

std::string_view strip_scope_prefix(std::string_view path, std::string_view prefix) noexcept;

// Keeps font bytes alive while RmlUi holds memory-backed faces. RmlUi faces
// are process-wide, so the storage lives until the last release().
const std::string& retain_font_bytes(std::string bytes);
void drop_last_font_bytes() noexcept;

} // namespace vellum::rmlui::detail
