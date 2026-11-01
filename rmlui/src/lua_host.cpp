#include <vellum/rmlui/lua.hpp>

#include "lua_sandbox.hpp"

#include <string>

namespace vellum::rmlui {

struct SandboxedLua::State {
    lua_State* lua = nullptr;
    bool failed = false;

    State() {
        lua = luaL_newstate();
        if (lua)
            lua_sandbox::open_safe_libraries(lua);
        else
            failed = true;
    }
    ~State() {
        lua_sandbox::ProtectedCall::destroy(lua);
    }

    Status reject() noexcept {
        if (lua && lua_gettop(lua) > 0)
            lua_pop(lua, 1);
        failed = true;
        return Status::rejected;
    }
};

SandboxedLua::SandboxedLua() : state_(std::make_unique<State>()) {}

SandboxedLua::~SandboxedLua() = default;

bool SandboxedLua::failed() const noexcept {
    return state_->failed;
}

void SandboxedLua::reset() {
    state_ = std::make_unique<State>();
}

Status SandboxedLua::load(std::string_view source, std::string_view source_name) {
    auto& state = *state_;
    if (state.failed || !state.lua || source.empty() || source_name.empty())
        return Status::rejected;
    const std::string name(source_name);
    // Text chunks only: precompiled bytecode bypasses the compiler's checks.
    if (luaL_loadbufferx(state.lua, source.data(), source.size(), name.c_str(), "t") != LUA_OK)
        return state.reject();
    return lua_sandbox::ProtectedCall::run(state.lua, 0, 0) == LUA_OK ? Status::ok
                                                                       : state.reject();
}

Status SandboxedLua::dispatch(std::string_view callback) {
    auto& state = *state_;
    if (state.failed || !state.lua || callback.empty())
        return Status::rejected;
    std::string name(callback);
    if (const auto paren = name.find('('); paren != std::string::npos)
        name.resize(paren);
    while (!name.empty() && (name.back() == ' ' || name.back() == '\t'))
        name.pop_back();
    if (name.empty())
        return Status::rejected;
    lua_getglobal(state.lua, name.c_str());
    if (!lua_isfunction(state.lua, -1)) {
        lua_pop(state.lua, 1);
        return Status::rejected;
    }
    return lua_sandbox::ProtectedCall::run(state.lua, 0, 0) == LUA_OK ? Status::ok
                                                                       : state.reject();
}

} // namespace vellum::rmlui
