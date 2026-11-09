#pragma once

// The open RmlUi entry point (VellumRmlUi::Native). Unlike the sealed
// VellumRmlUi::Core Backend, it links RmlUi publicly and hands RmlUi's own
// API to the caller: any Rml::RenderInterface draws the context (an upstream
// RmlUi backend such as GL3 or Vulkan, an optional renderer from this module,
// or the caller's own), and the caller reaches data models, custom elements,
// events and plugins through Rml::Context directly.
//
// What the module still provides: the shared, reference-counted RmlUi
// initialisation, a per-instance file interface that resolves reads through
// the instance's AssetProvider, and per-instance log attribution and parse
// diagnostics. Windowing, input translation and frame pacing belong to the
// host.
//
// One process uses either Core or Native, never both: each carries its own
// copy of RmlUi, and two copies would each own RmlUi's process globals.
//
// Version contract: Native is built against the RmlUi commit this module pins
// (cmake/VellumRmlUiDependencies.cmake) and exports it as RmlUi::Core and
// RmlUi::Debugger. A consumer that brings its own RmlUi build cannot mix it
// with Native in one binary; it uses its own build directly instead.
//
// System interface: Native forwards time, translation, clipboard, cursor and
// keyboard calls to the Rml::SystemInterface installed before the first
// instance opens (a host's own, for example a frame clock and the platform
// pasteboard). With none installed it falls back to RmlUi's defaults: a steady
// clock and an in-process clipboard. Native only adds per-instance log
// attribution on top.
//
// Threading: RmlUi's state is process-wide and not thread-safe, so calls into
// all instances in a process must never run concurrently. They need not be
// made on the main thread: a host may drive an instance, including render(),
// from a render thread it owns, as long as it serializes every RmlUi call in
// the process. Native itself calls no platform UI API (no AppKit), and per-call
// asset and log routing is thread-local, so it follows whichever thread makes
// the call.

#include <vellum/rmlui/assets.hpp>

#include <RmlUi/Core.h>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string_view>

namespace vellum::rmlui {

class NativeContext {
  public:
    NativeContext(AssetProvider& assets, Rml::RenderInterface& renderer);
    ~NativeContext();
    NativeContext(const NativeContext&) = delete;
    NativeContext& operator=(const NativeContext&) = delete;

    // Acquires the shared RmlUi runtime and creates this instance's context.
    Status open(std::uint32_t width, std::uint32_t height);
    // Loads an RML document through the AssetProvider and shows it, replacing
    // the previous one. Linked style sheets and templates resolve through the
    // same provider.
    Status load_document(std::string_view path);
    void set_dimensions(std::uint32_t width, std::uint32_t height);
    // Context::Update() then Context::Render(), with this instance in scope.
    void update();
    void render();
    void close();

    [[nodiscard]] bool is_open() const noexcept {
        return context_ != nullptr;
    }
    // The RmlUi context, for the caller's own RmlUi calls (input, data
    // models, elements). Null when closed. Wrap calls that may read files or
    // log in a Scope, so they resolve against this instance.
    [[nodiscard]] Rml::Context* context() const noexcept {
        return context_;
    }
    [[nodiscard]] Rml::ElementDocument* document() const noexcept {
        return document_;
    }

    // Puts this instance in scope for the caller's direct RmlUi calls: file
    // reads resolve through its AssetProvider and log messages reach its
    // sink. open(), load_document(), update(), render() and close() enter it
    // themselves.
    class Scope {
      public:
        explicit Scope(NativeContext& context);
        ~Scope();
        Scope(const Scope&) = delete;
        Scope& operator=(const Scope&) = delete;

      private:
        // Holds the module's internal scope object without allocating.
        alignas(void*) unsigned char storage_[8 * sizeof(void*)];
    };

    // Opens RmlUi's visual debugger on this context. RmlUi's debugger is
    // process-wide: it attaches to one context at a time. It draws text, so
    // it needs the FreeType font engine; without it this returns rejected.
    Status attach_debugger();

    // Whether this build carries RmlUi's FreeType font engine.
    [[nodiscard]] static bool fonts_enabled() noexcept;

    void set_log_sink(LogSink sink);
    [[nodiscard]] LoadDiagnostics last_load_diagnostics() const noexcept {
        return last_load_;
    }

    // Number of instances (Native in this binary) holding the RmlUi runtime.
    [[nodiscard]] static std::size_t open_instances() noexcept;

  private:
    struct Runtime;
    void record_log(LogLevel level, std::string_view text);

    AssetProvider& assets_;
    Rml::RenderInterface& renderer_;
    Rml::Context* context_ = nullptr;
    Rml::ElementDocument* document_ = nullptr;
    LogSink log_sink_;
    LoadDiagnostics loading_;
    LoadDiagnostics last_load_;
    bool counting_load_ = false;
    std::unique_ptr<Runtime> runtime_;
};

} // namespace vellum::rmlui
