#pragma once

#include <vellum/rmlui/backend.hpp>

#include <memory>
#include <string_view>

namespace vellum::rmlui {

// A sandboxed Lua 5.4 host for RmlUi skins (VellumRmlUi::Lua). Each instance
// owns its own lua_State, so two editors never share script state.
//
// The sandbox opens only base, coroutine, table, string, math and utf8;
// removes dofile, loadfile, load and string.dump; refuses skin-authored __gc
// finalizers; and runs
// every entry point (a loaded chunk, a dispatched callback, and every
// coroutine resume or close) under an instruction and wall-clock budget, so a
// runaway script returns an error instead of hanging the UI thread.
//
// After any script error the host latches failed() and rejects further calls
// until reset(), so a half-initialised skin cannot keep running.
//
// RmlUi's own Lua element bindings (the RmlUi::Lua plugin) are not attached:
// that plugin keeps a single process-wide lua_State, which cannot be shared
// between independent editors.
class SandboxedLua final : public LuaInterface {
  public:
    SandboxedLua();
    ~SandboxedLua() override;
    SandboxedLua(const SandboxedLua&) = delete;
    SandboxedLua& operator=(const SandboxedLua&) = delete;

    // Runs a chunk. Called by Backend::load_document() for a .lua asset.
    Status load(std::string_view source, std::string_view source_name) override;
    // Calls a global function by name; any argument list after the name, as
    // in an RML attribute "on_click()", is ignored.
    Status dispatch(std::string_view callback) override;

    [[nodiscard]] bool failed() const noexcept;
    // Discards the state and starts a fresh sandbox.
    void reset();

  private:
    struct State;
    std::unique_ptr<State> state_;
};

} // namespace vellum::rmlui
