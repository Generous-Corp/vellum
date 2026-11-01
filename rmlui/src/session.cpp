#include "session.hpp"

#include <RmlUi/Core.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <list>
#include <memory>
#include <mutex>
#include <type_traits>
#include <utility>

namespace vellum::rmlui::detail {

namespace {

thread_local AssetProvider* current_assets = nullptr;
thread_local std::string_view current_prefix;

class ScopedFileInterface final : public Rml::FileInterface {
  public:
    Rml::FileHandle Open(const Rml::String& path) override {
        if (!current_assets)
            return 0;
        Asset asset;
        if (current_assets->load(strip_scope_prefix(path, current_prefix), asset) != Status::ok)
            return 0;
        return reinterpret_cast<Rml::FileHandle>(new Handle{std::move(asset.bytes), 0});
    }

    void Close(Rml::FileHandle file) override {
        delete reinterpret_cast<Handle*>(static_cast<std::uintptr_t>(file));
    }

    size_t Read(void* buffer, size_t size, Rml::FileHandle file) override {
        auto* handle = reinterpret_cast<Handle*>(static_cast<std::uintptr_t>(file));
        if (!handle || !buffer)
            return 0;
        const auto available =
            handle->bytes.size() - std::min(handle->offset, handle->bytes.size());
        const auto count = std::min(size, available);
        std::copy_n(handle->bytes.data() + handle->offset, count, static_cast<char*>(buffer));
        handle->offset += count;
        return count;
    }

    bool Seek(Rml::FileHandle file, long offset, int origin) override {
        auto* handle = reinterpret_cast<Handle*>(static_cast<std::uintptr_t>(file));
        if (!handle)
            return false;
        std::int64_t base = 0;
        if (origin == SEEK_CUR)
            base = static_cast<std::int64_t>(handle->offset);
        else if (origin == SEEK_END)
            base = static_cast<std::int64_t>(handle->bytes.size());
        const auto next = base + offset;
        if (next < 0 || static_cast<std::uint64_t>(next) > handle->bytes.size())
            return false;
        handle->offset = static_cast<std::size_t>(next);
        return true;
    }

    size_t Tell(Rml::FileHandle file) override {
        const auto* handle = reinterpret_cast<const Handle*>(static_cast<std::uintptr_t>(file));
        return handle ? handle->offset : 0;
    }

  private:
    struct Handle {
        std::string bytes;
        std::size_t offset;
    };
};

struct ProcessRuntime {
    std::mutex mutex;
    std::size_t references = 0;
    ScopedFileInterface files;
#if !VELLUM_RMLUI_FONTS_ENABLED
    // The pinned RmlUi core supplies a concrete no-op FontEngineInterface.
    // Guard it so a future pin cannot turn the font-off build into an
    // abstract-type error.
    static_assert(!std::is_abstract_v<Rml::FontEngineInterface>,
                  "RmlUi font-off mode requires a concrete null font engine");
    Rml::FontEngineInterface null_fonts;
#endif
    std::list<std::string> font_bytes;
};

ProcessRuntime& process_runtime() {
    // Never destroyed: a Backend closed during static destruction must still
    // find the runtime it released into.
    static auto* runtime = new ProcessRuntime;
    return *runtime;
}

} // namespace

bool acquire_runtime() {
    auto& runtime = process_runtime();
    std::lock_guard lock(runtime.mutex);
    if (runtime.references == 0) {
        Rml::SetFileInterface(&runtime.files);
#if !VELLUM_RMLUI_FONTS_ENABLED
        Rml::SetFontEngineInterface(&runtime.null_fonts);
#endif
        if (!Rml::Initialise())
            return false;
    }
    ++runtime.references;
    return true;
}

void release_runtime() noexcept {
    auto& runtime = process_runtime();
    std::lock_guard lock(runtime.mutex);
    if (runtime.references == 0)
        return;
    if (--runtime.references == 0) {
        Rml::Shutdown();
        runtime.font_bytes.clear();
    }
}

std::size_t runtime_references() noexcept {
    auto& runtime = process_runtime();
    std::lock_guard lock(runtime.mutex);
    return runtime.references;
}

std::string_view strip_scope_prefix(std::string_view path, std::string_view prefix) noexcept {
    if (!prefix.empty() && path.size() > prefix.size() && path.starts_with(prefix))
        path.remove_prefix(prefix.size());
    return path;
}

AssetScope::AssetScope(AssetProvider* assets, std::string_view prefix) noexcept
    : previous_(current_assets), previous_prefix_(current_prefix) {
    current_assets = assets;
    current_prefix = prefix;
}

AssetScope::~AssetScope() {
    current_assets = previous_;
    current_prefix = previous_prefix_;
}

const std::string& retain_font_bytes(std::string bytes) {
    auto& runtime = process_runtime();
    std::lock_guard lock(runtime.mutex);
    runtime.font_bytes.push_back(std::move(bytes));
    return runtime.font_bytes.back();
}

void drop_last_font_bytes() noexcept {
    auto& runtime = process_runtime();
    std::lock_guard lock(runtime.mutex);
    if (!runtime.font_bytes.empty())
        runtime.font_bytes.pop_back();
}

} // namespace vellum::rmlui::detail
