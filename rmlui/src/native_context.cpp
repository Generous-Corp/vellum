#include <vellum/rmlui/native.hpp>

#include "session.hpp"

#include <RmlUi/Debugger.h>

#include <atomic>
#include <climits>
#include <new>
#include <string>
#include <utility>

namespace vellum::rmlui {

namespace {

std::atomic_uint64_t next_native_context_id{1};

// RmlUi's debugger is process-wide and lives in the context that hosts it.
// UI thread only, like every other RmlUi call.
NativeContext* debugger_owner = nullptr;

} // namespace

struct NativeContext::Runtime final : detail::LogTarget {
    explicit Runtime(NativeContext& owner)
        : owner(owner),
          context_name("vellum-rmlui-native-" + std::to_string(next_native_context_id.fetch_add(1))),
          scope_prefix(context_name + "/") {}

    void record_log(LogLevel level, std::string_view text) override {
        owner.record_log(level, text);
    }

    NativeContext& owner;
    std::string context_name;
    std::string scope_prefix;
    bool holds_runtime = false;
};

static_assert(sizeof(detail::AssetScope) <= 8 * sizeof(void*));
static_assert(alignof(detail::AssetScope) <= alignof(void*));

NativeContext::Scope::Scope(NativeContext& context) {
    auto* runtime = context.runtime_.get();
    if (runtime)
        new (storage_) detail::AssetScope(&context.assets_, runtime->scope_prefix, runtime);
    else
        new (storage_) detail::AssetScope(nullptr);
}

NativeContext::Scope::~Scope() {
    std::launder(reinterpret_cast<detail::AssetScope*>(storage_))->~AssetScope();
}

NativeContext::NativeContext(AssetProvider& assets, Rml::RenderInterface& renderer)
    : assets_(assets), renderer_(renderer) {}

NativeContext::~NativeContext() {
    close();
}

bool NativeContext::fonts_enabled() noexcept {
    return VELLUM_RMLUI_FONTS_ENABLED != 0;
}

std::size_t NativeContext::open_instances() noexcept {
    return detail::runtime_references();
}

void NativeContext::set_log_sink(LogSink sink) {
    log_sink_ = std::move(sink);
}

void NativeContext::record_log(LogLevel level, std::string_view text) {
    if (counting_load_)
        detail::count_load_message(loading_, level, text);
    if (log_sink_)
        log_sink_(LogMessage{level, text});
}

Status NativeContext::open(std::uint32_t width, std::uint32_t height) {
    if (context_)
        return Status::already_open;
    if (width == 0 || height == 0 || width > INT_MAX || height > INT_MAX)
        return Status::rejected;
    auto runtime = std::make_unique<Runtime>(*this);
    if (!detail::acquire_runtime())
        return Status::rejected;
    runtime->holds_runtime = true;
    runtime_ = std::move(runtime);
    Scope scope(*this);
    context_ = Rml::CreateContext(runtime_->context_name,
                                  {static_cast<int>(width), static_cast<int>(height)}, &renderer_);
    if (!context_) {
        close();
        return Status::rejected;
    }
    return Status::ok;
}

Status NativeContext::load_document(std::string_view path) {
    if (!context_)
        return Status::not_open;
    Asset asset;
    const auto status = assets_.load(path, asset);
    if (status != Status::ok)
        return status;
    Scope scope(*this);
    // Count what RmlUi reports while it parses the document and its style
    // sheets; a rejected declaration is otherwise invisible.
    loading_ = {};
    counting_load_ = true;
    auto* document = context_->LoadDocumentFromMemory(asset.bytes, runtime_->scope_prefix + asset.path);
    counting_load_ = false;
    last_load_ = loading_;
    if (!document)
        return Status::rejected;
    if (document_)
        context_->UnloadDocument(document_);
    document_ = document;
    document_->Show();
    return Status::ok;
}

void NativeContext::set_dimensions(std::uint32_t width, std::uint32_t height) {
    if (!context_ || width == 0 || height == 0 || width > INT_MAX || height > INT_MAX)
        return;
    Scope scope(*this);
    context_->SetDimensions({static_cast<int>(width), static_cast<int>(height)});
}

void NativeContext::update() {
    if (!context_)
        return;
    Scope scope(*this);
    context_->Update();
}

void NativeContext::render() {
    if (!context_)
        return;
    Scope scope(*this);
    context_->Render();
}

Status NativeContext::attach_debugger() {
    if (!context_)
        return Status::not_open;
    if (!fonts_enabled())
        return Status::rejected;
    Scope scope(*this);
    if (debugger_owner == nullptr) {
        if (!Rml::Debugger::Initialise(context_))
            return Status::rejected;
    } else if (debugger_owner != this) {
        // The debugger's own documents live in the context that initialised
        // it; move it here rather than debugging across instances.
        Rml::Debugger::Shutdown();
        if (!Rml::Debugger::Initialise(context_)) {
            debugger_owner = nullptr;
            return Status::rejected;
        }
    }
    debugger_owner = this;
    Rml::Debugger::SetContext(context_);
    Rml::Debugger::SetVisible(true);
    return Status::ok;
}

void NativeContext::close() {
    if (runtime_) {
        if (context_) {
            Scope scope(*this);
            if (debugger_owner == this) {
                Rml::Debugger::Shutdown();
                debugger_owner = nullptr;
            }
            Rml::RemoveContext(runtime_->context_name);
            context_ = nullptr;
            document_ = nullptr;
        }
        if (runtime_->holds_runtime) {
            // While other instances keep RmlUi alive, drop this instance's
            // render manager now: it holds a pointer to this instance's
            // renderer, which the caller may destroy after close().
            if (detail::runtime_references() > 1) {
                detail::AssetScope scope(nullptr);
                Rml::ReleaseRenderManagers();
            }
            detail::release_runtime();
            runtime_->holds_runtime = false;
        }
        runtime_.reset();
    }
}

} // namespace vellum::rmlui
