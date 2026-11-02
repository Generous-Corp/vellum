#pragma once

// The Lua sandbox for RmlUi skins: a safe standard-library subset, execution
// budgets on every entry point (including coroutine resume and close), and a
// refusal of skin-authored __gc finalizers, which Lua runs with hooks
// disabled and so could not be bounded.

extern "C" {
#include <lauxlib.h>
#include <lua.h>
#include <lualib.h>
}

#include <chrono>
#include <cstdint>

namespace vellum::rmlui::lua_sandbox {

// Protect every production script entry point, including a script's initial
// chunk and named callback dispatch. The instruction limit handles tight
// loops deterministically; the monotonic deadline also bounds a single hook
// interval on a heavily loaded UI thread. Lua is only entered from the UI
// thread, never from the audio callback.
class ProtectedCall final {
public:
  static constexpr int kHookInterval = 1000;
  static constexpr std::uint64_t kInstructionBudget = 1000000;
  static constexpr std::chrono::seconds kWallClockBudget{3};

  static int run(lua_State *state, int nargs, int nresults) noexcept {
    if (state == nullptr)
      return LUA_ERRRUN;
    ProtectedCall guard(state);
    if (!guard.charge_parents()) {
      lua_pop(state, nargs + 1);
      lua_pushliteral(state, "lua execution nested budget exceeded");
      return LUA_ERRRUN;
    }
    return lua_pcall(state, nargs, nresults, 0);
  }

  static int resume(lua_State *thread, lua_State *from, int nargs,
                    int *results) noexcept {
    // Coroutine creation copies the parent's hook and extraspace. Never keep
    // a pointer to that stack guard across yield/resume boundaries.
    lua_sethook(thread, nullptr, 0, 0);
    *static_cast<void **>(lua_getextraspace(thread)) = nullptr;
    ProtectedCall guard(thread);
    if (!guard.charge_parents()) {
      lua_pushliteral(thread, "lua execution nested budget exceeded");
      *results = 1;
      return LUA_ERRRUN;
    }
    return lua_resume(thread, from, nargs, results);
  }
  static int close_thread(lua_State *thread, lua_State *from) noexcept {
    lua_sethook(thread, nullptr, 0, 0);
    *static_cast<void **>(lua_getextraspace(thread)) = nullptr;
    ProtectedCall guard(thread);
    if (!guard.charge_parents()) {
      lua_pushliteral(thread, "lua execution nested budget exceeded");
      return LUA_ERRRUN;
    }
    return lua_closethread(thread, from);
  }

  static void destroy(lua_State *state) noexcept {
    if (!state)
      return;
    ProtectedCall guard(state);
    lua_close(state);
    // lua_close runs finalizers before freeing the state. The guard must not
    // restore a hook or extraspace into that freed allocation.
    guard.state_ = nullptr;
  }

  // Can also surround native RmlUi operations whose Lua calls are internal.
  explicit ProtectedCall(lua_State *state) : state_(state) {
    auto *slot = static_cast<void **>(lua_getextraspace(state_));
    previous_ = *slot;
    previous_hook_ = lua_gethook(state_);
    previous_mask_ = lua_gethookmask(state_);
    previous_count_ = lua_gethookcount(state_);
    parent_ = active_;
    active_ = this;
    *slot = this;
    lua_sethook(state_, &hook, LUA_MASKCOUNT, kHookInterval);
    deadline_ = std::chrono::steady_clock::now() + kWallClockBudget;
  }

  ~ProtectedCall() {
    active_ = parent_;
    if (!state_)
      return;
    auto *slot = static_cast<void **>(lua_getextraspace(state_));
    *slot = previous_;
    lua_sethook(state_, previous_hook_, previous_mask_, previous_count_);
  }
  ProtectedCall(const ProtectedCall &) = delete;
  ProtectedCall &operator=(const ProtectedCall &) = delete;

private:
  bool charge_parents() noexcept {
    // Reinstalling a Lua count hook resets its partial instruction counter.
    // Charge that unobserved interval on nested entry so tiny repeated calls
    // cannot indefinitely postpone the enclosing hook.
    for (auto *parent = parent_; parent; parent = parent->parent_) {
      parent->instructions_ += kHookInterval;
      if (parent->instructions_ > kInstructionBudget ||
          std::chrono::steady_clock::now() >= parent->deadline_) {
        return false;
      }
    }
    return true;
  }
  static void hook(lua_State *state, lua_Debug *) {
    auto *guard = static_cast<ProtectedCall *>(
        *static_cast<void **>(lua_getextraspace(state)));
    if (guard == nullptr) {
      luaL_error(state, "lua execution guard unavailable");
      return;
    }
    // Nested protected entry points consume the enclosing operation's budget
    // too, rather than obtaining an unlimited series of fresh budgets.
    for (auto *active = guard; active; active = active->parent_) {
      active->instructions_ += kHookInterval;
      if (active->instructions_ > kInstructionBudget)
        luaL_error(state, "lua execution instruction budget exceeded");
      if (std::chrono::steady_clock::now() >= active->deadline_)
        luaL_error(state, "lua execution time budget exceeded");
    }
  }

  static inline thread_local ProtectedCall *active_ = nullptr;
  lua_State *state_ = nullptr;
  void *previous_ = nullptr;
  lua_Hook previous_hook_ = nullptr;
  int previous_mask_ = 0, previous_count_ = 0;
  ProtectedCall *parent_ = nullptr;
  std::uint64_t instructions_ = 0;
  std::chrono::steady_clock::time_point deadline_{};
};

// Preserve create/resume/yield/wrap/close while rebinding execution budgets on
// every entry. Stock coroutine resume can inherit a dead main-stack hook.
class Coroutines final {
public:
  static void install(lua_State *state) {
    lua_getglobal(state, "coroutine");
    const luaL_Reg replacements[] = {{"create", create},
                                     {"resume", resume},
                                     {"wrap", wrap},
                                     {"close", close},
                                     {nullptr, nullptr}};
    luaL_setfuncs(state, replacements, 0);
    lua_pop(state, 1);
  }

private:
  static lua_State *new_thread(lua_State *state) {
    luaL_checktype(state, 1, LUA_TFUNCTION);
    auto *thread = lua_newthread(state);
    lua_sethook(thread, nullptr, 0, 0);
    *static_cast<void **>(lua_getextraspace(thread)) = nullptr;
    lua_pushvalue(state, 1);
    lua_xmove(state, thread, 1);
    return thread;
  }
  static int create(lua_State *state) {
    new_thread(state);
    return 1;
  }
  static bool running(lua_State *thread) {
    lua_Debug frame;
    return lua_status(thread) == LUA_OK && lua_getstack(thread, 0, &frame);
  }
  static int transfer_resume(lua_State *state, lua_State *thread, int nargs) {
    if (thread == state || running(thread)) {
      lua_pushliteral(state, "cannot resume running coroutine");
      return -1;
    }
    if (!lua_checkstack(thread, nargs)) {
      lua_pushliteral(state, "too many coroutine arguments");
      return -1;
    }
    lua_xmove(state, thread, nargs);
    int results = 0;
    const int status =
        ProtectedCall::resume(thread, state, nargs, &results);
    if (status == LUA_OK || status == LUA_YIELD) {
      if (!lua_checkstack(state, results + 1)) {
        lua_pop(thread, results);
        lua_pushliteral(state, "too many coroutine results");
        return -1;
      }
      lua_xmove(thread, state, results);
      return results;
    }
    lua_xmove(thread, state, 1);
    return -1;
  }
  static int resume(lua_State *state) {
    auto *thread = lua_tothread(state, 1);
    luaL_argcheck(state, thread != nullptr, 1, "thread expected");
    const int results = transfer_resume(state, thread, lua_gettop(state) - 1);
    lua_pushboolean(state, results >= 0);
    lua_insert(state, -(results >= 0 ? results : 1) - 1);
    return results >= 0 ? results + 1 : 2;
  }
  static int wrapped_resume(lua_State *state) {
    auto *thread = lua_tothread(state, lua_upvalueindex(1));
    const int results = transfer_resume(state, thread, lua_gettop(state));
    if (results >= 0)
      return results;
    // Close pending to-be-closed locals under the same bounded policy.
    if (!running(thread))
      (void)ProtectedCall::close_thread(thread, state);
    return lua_error(state);
  }
  static int wrap(lua_State *state) {
    new_thread(state);
    lua_pushcclosure(state, wrapped_resume, 1);
    return 1;
  }
  static int close(lua_State *state) {
    auto *thread = lua_tothread(state, 1);
    luaL_argcheck(state, thread != nullptr, 1, "thread expected");
    if (thread == state || running(thread))
      return luaL_error(state, "cannot close running coroutine");
    const int status = ProtectedCall::close_thread(thread, state);
    lua_pushboolean(state, status == LUA_OK);
    if (status == LUA_OK)
      return 1;
    lua_xmove(thread, state, 1);
    return 2;
  }
};
inline void install_coroutine_guards(lua_State *state) {
  Coroutines::install(state);
}

// Lua disables count hooks while running __gc metamethods. A skin-authored
// finalizer therefore cannot be bounded by the host's execution policy.
// Reject registration explicitly; ordinary metatables and coroutine __close
// remain supported. Native RmlUi userdata finalizers are not changed.
inline int safe_setmetatable(lua_State *state) {
  if (lua_istable(state, 2)) {
    lua_pushliteral(state, "__gc");
    lua_rawget(state, 2);
    const bool finalizer = !lua_isnil(state, -1);
    lua_pop(state, 1);
    if (finalizer)
      return luaL_error(state, "skin __gc finalizers are unsupported");
  }
  lua_pushvalue(state, lua_upvalueindex(1));
  lua_insert(state, 1);
  lua_call(state, lua_gettop(state) - 1, 1);
  return 1;
}

// RmlUi skins depend only on these source-compatible libraries.
// In particular, do not call luaL_openlibs here: it also installs io, os,
// package, and debug, which would let an authored skin reach the host process.
inline void open_safe_libraries(lua_State *state) {
  if (state == nullptr)
    return;

  luaL_requiref(state, LUA_GNAME, luaopen_base, 1);
  lua_pop(state, 1);
  luaL_requiref(state, LUA_COLIBNAME, luaopen_coroutine, 1);
  lua_pop(state, 1);
  luaL_requiref(state, LUA_TABLIBNAME, luaopen_table, 1);
  lua_pop(state, 1);
  luaL_requiref(state, LUA_STRLIBNAME, luaopen_string, 1);
  lua_pop(state, 1);
  luaL_requiref(state, LUA_MATHLIBNAME, luaopen_math, 1);
  lua_pop(state, 1);
  luaL_requiref(state, LUA_UTF8LIBNAME, luaopen_utf8, 1);
  lua_pop(state, 1);

  // These loaders and bytecode helpers are not part of the RmlUi
  // compatibility contract. Removing all of them keeps a skin from
  // manufacturing a precompiled chunk and feeding it back to Lua's loader,
  // bypassing the text-only check applied to host-provided source.
  lua_pushnil(state);
  lua_setglobal(state, "dofile");
  lua_pushnil(state);
  lua_setglobal(state, "loadfile");
  lua_pushnil(state);
  lua_setglobal(state, "load");
  lua_getglobal(state, LUA_STRLIBNAME);
  if (lua_istable(state, -1)) {
    lua_pushnil(state);
    lua_setfield(state, -2, "dump");
  }
  lua_pop(state, 1);
  install_coroutine_guards(state);
  lua_getglobal(state, "setmetatable");
  lua_pushcclosure(state, &safe_setmetatable, 1);
  lua_setglobal(state, "setmetatable");
}

} // namespace vellum::rmlui::lua_sandbox
