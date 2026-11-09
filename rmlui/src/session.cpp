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
thread_local LogTarget* current_log = nullptr;

LogLevel level_of(Rml::Log::Type type) noexcept {
    switch (type) {
    case Rml::Log::LT_ALWAYS:
        return LogLevel::always;
    case Rml::Log::LT_ERROR:
        return LogLevel::error;
    case Rml::Log::LT_ASSERT:
        return LogLevel::assertion;
    case Rml::Log::LT_WARNING:
        return LogLevel::warning;
    case Rml::Log::LT_INFO:
        return LogLevel::info;
    default:
        return LogLevel::debug;
    }
}

// Installed for the lifetime of the shared runtime. It forwards every call to
// the interface that was installed before (a product's own, or RmlUi's
// default behaviour), so it adds log attribution without changing time,
// translation, clipboard, cursor, keyboard or log output.
class ForwardingSystemInterface final : public Rml::SystemInterface {
  public:
    void set_previous(Rml::SystemInterface* previous) noexcept {
        previous_ = previous;
    }
    [[nodiscard]] Rml::SystemInterface* previous() const noexcept {
        return previous_;
    }

    double GetElapsedTime() override {
        return previous_ ? previous_->GetElapsedTime() : Rml::SystemInterface::GetElapsedTime();
    }
    int TranslateString(Rml::String& translated, const Rml::String& input) override {
        return previous_ ? previous_->TranslateString(translated, input)
                         : Rml::SystemInterface::TranslateString(translated, input);
    }
    void JoinPath(Rml::String& translated, const Rml::String& document,
                  const Rml::String& path) override {
        if (previous_)
            previous_->JoinPath(translated, document, path);
        else
            Rml::SystemInterface::JoinPath(translated, document, path);
    }
    bool LogMessage(Rml::Log::Type type, const Rml::String& message) override {
        if (current_log)
            current_log->record_log(level_of(type), message);
        return previous_ ? previous_->LogMessage(type, message)
                         : Rml::SystemInterface::LogMessage(type, message);
    }
    void SetMouseCursor(const Rml::String& name) override {
        if (previous_)
            previous_->SetMouseCursor(name);
        else
            Rml::SystemInterface::SetMouseCursor(name);
    }
    void SetClipboardText(const Rml::String& text) override {
        if (previous_)
            previous_->SetClipboardText(text);
        else
            Rml::SystemInterface::SetClipboardText(text);
    }
    void GetClipboardText(Rml::String& text) override {
        if (previous_)
            previous_->GetClipboardText(text);
        else
            Rml::SystemInterface::GetClipboardText(text);
    }
    void ActivateKeyboard(Rml::Vector2f caret_position, float line_height) override {
        if (previous_)
            previous_->ActivateKeyboard(caret_position, line_height);
        else
            Rml::SystemInterface::ActivateKeyboard(caret_position, line_height);
    }
    void DeactivateKeyboard() override {
        if (previous_)
            previous_->DeactivateKeyboard();
        else
            Rml::SystemInterface::DeactivateKeyboard();
    }

  private:
    Rml::SystemInterface* previous_ = nullptr;
};

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
    ForwardingSystemInterface system;
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

// Hand RmlUi back the system interface it had before the runtime started,
// unless a product replaced ours in the meantime.
void restore_system_interface(ProcessRuntime& runtime) noexcept {
    auto* installed = Rml::GetSystemInterface();
    if (installed == &runtime.system || installed == nullptr)
        Rml::SetSystemInterface(runtime.system.previous());
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
        auto* installed = Rml::GetSystemInterface();
        runtime.system.set_previous(installed == &runtime.system ? nullptr : installed);
        Rml::SetSystemInterface(&runtime.system);
        if (!Rml::Initialise()) {
            restore_system_interface(runtime);
            return false;
        }
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
        restore_system_interface(runtime);
        runtime.font_bytes.clear();
    }
}

std::size_t runtime_references() noexcept {
    auto& runtime = process_runtime();
    std::lock_guard lock(runtime.mutex);
    return runtime.references;
}

void count_load_message(LoadDiagnostics& diagnostics, LogLevel level,
                        std::string_view text) noexcept {
    if (level == LogLevel::error || level == LogLevel::assertion)
        ++diagnostics.errors;
    else if (level == LogLevel::warning)
        ++diagnostics.warnings;
    // RmlUi's style sheet parser reports a declaration it could not parse with
    // this fixed prefix and then drops it.
    if (text.find("Syntax error parsing property declaration") != std::string_view::npos)
        ++diagnostics.dropped_declarations;
}

std::string_view strip_scope_prefix(std::string_view path, std::string_view prefix) noexcept {
    if (!prefix.empty() && path.size() > prefix.size() && path.starts_with(prefix))
        path.remove_prefix(prefix.size());
    return path;
}

AssetScope::AssetScope(AssetProvider* assets, std::string_view prefix, LogTarget* log) noexcept
    : previous_(current_assets), previous_prefix_(current_prefix), previous_log_(current_log) {
    current_assets = assets;
    current_prefix = prefix;
    current_log = log;
}

AssetScope::~AssetScope() {
    current_assets = previous_;
    current_prefix = previous_prefix_;
    current_log = previous_log_;
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
