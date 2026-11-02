// The Lua sandbox: library subset, execution budgets on every entry point,
// coroutine rebinding and __gc refusal, then the SandboxedLua host driven
// through a Backend.
#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/lua.hpp>

#include "lua_sandbox.hpp"
#include "test_support.hpp"

#include <cstring>
#include <string>

using namespace vellum::rmlui;
using lua_sandbox::open_safe_libraries;
using lua_sandbox::ProtectedCall;

namespace {
void prior_hook(lua_State*, lua_Debug*) {}
int nested_call(lua_State* state) {
    if (luaL_loadstring(state, "return 42") != LUA_OK)
        return lua_error(state);
    if (ProtectedCall::run(state, 0, 1) != LUA_OK)
        return lua_error(state);
    return 1;
}
const char* top_error(lua_State* state) {
    return lua_gettop(state) == 0 ? "" : lua_tostring(state, -1);
}

void test_sandbox_guards() {

  lua_State *state = luaL_newstate();
  VELLUM_RMLUI_CHECK(state != nullptr);
  open_safe_libraries(state);

  // The documented source-compatible libraries remain available.
  VELLUM_RMLUI_CHECK(luaL_dostring(state, "assert(type(coroutine.create) == 'function'); "
                              "assert(table.concat({ 'a', 'b' }) == 'ab'); "
                              "assert(string.upper('skin') == 'SKIN'); "
                              "assert(math.floor(2.9) == 2); "
                              "assert(utf8.len('Skin') == 4); "
                              "assert(type(params) == 'nil')") == LUA_OK);

  // Host file/process/debug/dynamic-code loaders are absent even though base is present.
  VELLUM_RMLUI_CHECK(luaL_dostring(state,
                       "assert(io == nil and os == nil and package == nil and "
                       "debug == nil); "
                       "assert(dofile == nil and loadfile == nil)") == LUA_OK);
  VELLUM_RMLUI_CHECK(luaL_dostring(
                       state,
                       "assert(load == nil and string.dump == nil); "
                       "local ok, error = pcall(function() "
                       "return load(string.dump(function() return 1 end), "
                       "'chunk', 'b') end); "
                       "assert(not ok and type(error) == 'string')") == LUA_OK);
  VELLUM_RMLUI_CHECK(luaL_loadstring(state, "return 42") == LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 1) ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(lua_tointeger(state, -1) == 42);
  lua_pop(state, 1);

  // An unbounded callback must return a Lua error and leave the state usable.
  VELLUM_RMLUI_CHECK(luaL_loadstring(state, "while true do end") == LUA_OK);
  const int result =
      ProtectedCall::run(state, 0, 0);
  VELLUM_RMLUI_CHECK(result != LUA_OK);
  const char *error = top_error(state);
  VELLUM_RMLUI_CHECK(error != nullptr && std::strstr(error, "budget") != nullptr);
  lua_pop(state, 1);
  VELLUM_RMLUI_CHECK(luaL_dostring(state, "assert(6 * 7 == 42)") == LUA_OK);

  // A nested call must restore the outer guard; otherwise the following
  // infinite loop hangs. CTest's timeout is an independent outer safety bound.
  lua_pushcfunction(state, nested_call);
  lua_setglobal(state, "nested_call");
  lua_sethook(state, prior_hook, LUA_MASKCOUNT, 37);
  VELLUM_RMLUI_CHECK(luaL_loadstring(state,
                         "assert(nested_call() == 42); while true do end") ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 0) !=
         LUA_OK);
  VELLUM_RMLUI_CHECK(lua_gethook(state) == prior_hook);
  VELLUM_RMLUI_CHECK(lua_gethookmask(state) == LUA_MASKCOUNT &&
         lua_gethookcount(state) == 37);
  lua_pop(state, 1);
  lua_sethook(state, nullptr, 0, 0);
  // Short nested chunks reset Lua's hook counter. They must not be able to
  // evade the enclosing budget by never reaching one full hook interval.
  VELLUM_RMLUI_CHECK(luaL_loadstring(state, "while true do nested_call() end") == LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 0) !=
         LUA_OK);
  VELLUM_RMLUI_CHECK(std::strstr(top_error(state), "budget") != nullptr);
  lua_pop(state, 1);
  // A coroutine survives the guard that created it, then yields and resumes
  // through separate guarded host calls. Its extraspace must never retain a
  // pointer to the earlier stack guard.
  VELLUM_RMLUI_CHECK(luaL_loadstring(state,
                         "co=coroutine.create(function(v) "
                         "coroutine.yield(v+1); return 42 end)") == LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 0) ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(luaL_loadstring(
             state, "local ok,v=coroutine.resume(co,4);assert(ok and v==5)") ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 0) ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(luaL_loadstring(state,
                         "local ok,v=coroutine.resume(co);assert(ok and "
                         "v==42);assert(coroutine.close(co))") == LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 0) ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(luaL_loadstring(
             state, "local f=coroutine.wrap(function() coroutine.yield(9); "
                    "return 10 end);assert(f()==9 and f()==10)") == LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 0) ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(luaL_loadstring(
             state, "co=coroutine.create(function() while true do end end)") ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 0) ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(luaL_loadstring(state, "local ok,e=coroutine.resume(co);assert(not ok "
                                "and string.find(e,'budget'))") == LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 0) ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(luaL_loadstring(
             state,
             "local ok,e=pcall(coroutine.wrap(function() while true do "
             "end end));assert(not ok and string.find(e,'budget'))") == LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 0) ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(luaL_loadstring(state,
                         "while true do local co=coroutine.create(function() "
                         "return 1 end);coroutine.resume(co) end") == LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 0) !=
         LUA_OK);
  lua_pop(state, 1);
  VELLUM_RMLUI_CHECK(luaL_loadstring(
             state,
             "co=coroutine.create(function() local x <close> = "
             "setmetatable({}, {__close=function() while true do end "
             "end});coroutine.yield() end);assert(coroutine.resume(co))") ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 0) ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(luaL_loadstring(state, "local ok,e=coroutine.close(co);assert(not ok "
                                "and string.find(e,'budget'))") == LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 0) ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(luaL_loadstring(
             state,
             "local ok,e=pcall(setmetatable,{}, {__gc=function() while true do "
             "end end});assert(not ok and string.find(e,'unsupported'))") ==
         LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 0) ==
         LUA_OK);
  // False is nonnil and would mark a table for later __gc mutation too.
  VELLUM_RMLUI_CHECK(luaL_loadstring(
             state,
             "assert(not pcall(setmetatable,{}, {__gc=false})); local mt={}; "
             "late=setmetatable({},mt);mt.__gc=function() error('late gc ran') "
             "end; local dynamic=setmetatable({}, {__index=function(_,k) if "
             "k=='__gc' then return function() error('lookup gc ran') end end "
             "end});looked_up=setmetatable({},dynamic)") == LUA_OK);
  VELLUM_RMLUI_CHECK(ProtectedCall::run(state, 0, 0) ==
         LUA_OK);
  ProtectedCall::destroy(state);
}

void test_host_through_backend() {
    HeadlessRecorder assets;
    assets.add_asset("skin.lua", "count = 0\n"
                                 "function on_click() count = count + 1 end\n"
                                 "function spin() while true do end end\n"
                                 "function escape() return io.open('/etc/hosts') end\n");
    SandboxedLua lua;
    Backend backend(assets, assets, lua);
    VELLUM_RMLUI_CHECK(backend.open(16, 16) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.load_document("skin.lua") == Status::ok);
    VELLUM_RMLUI_CHECK(backend.dispatch_lua("on_click()") == Status::ok);
    VELLUM_RMLUI_CHECK(backend.dispatch_lua("on_click") == Status::ok);
    VELLUM_RMLUI_CHECK(backend.dispatch_lua("missing") == Status::rejected);
    VELLUM_RMLUI_CHECK(!lua.failed());
    // A sandbox escape attempt is a script error, and errors latch.
    VELLUM_RMLUI_CHECK(backend.dispatch_lua("escape") == Status::rejected);
    VELLUM_RMLUI_CHECK(lua.failed());
    VELLUM_RMLUI_CHECK(backend.dispatch_lua("on_click") == Status::rejected);
    lua.reset();
    VELLUM_RMLUI_CHECK(!lua.failed());
    VELLUM_RMLUI_CHECK(backend.load_document("skin.lua") == Status::ok);
    // A runaway callback ends in a budget error instead of hanging.
    VELLUM_RMLUI_CHECK(backend.dispatch_lua("spin") == Status::rejected);
    VELLUM_RMLUI_CHECK(lua.failed());
    backend.close();

    // Precompiled chunks are refused; only source text is admitted. The
    // bytecode is real, dumped from the same Lua, so only the mode check can
    // turn it away.
    std::string bytecode;
    lua_State* compiler = luaL_newstate();
    VELLUM_RMLUI_CHECK(luaL_loadstring(compiler, "marker = 1") == LUA_OK);
    VELLUM_RMLUI_CHECK(lua_dump(
                           compiler,
                           [](lua_State*, const void* data, size_t size, void* out) {
                               static_cast<std::string*>(out)->append(
                                   static_cast<const char*>(data), size);
                               return 0;
                           },
                           &bytecode, 0) == 0);
    lua_close(compiler);
    VELLUM_RMLUI_CHECK(bytecode.size() > 4 && bytecode[0] == '\x1b');
    SandboxedLua fresh;
    VELLUM_RMLUI_CHECK(fresh.load(bytecode, "chunk") == Status::rejected);
    SandboxedLua source;
    VELLUM_RMLUI_CHECK(source.load("marker = 1", "chunk") == Status::ok);

    // Two hosts never share globals.
    SandboxedLua first;
    SandboxedLua second;
    VELLUM_RMLUI_CHECK(first.load("function only_first() end", "a") == Status::ok);
    VELLUM_RMLUI_CHECK(first.dispatch("only_first") == Status::ok);
    VELLUM_RMLUI_CHECK(second.dispatch("only_first") == Status::rejected);
}
} // namespace

int main() {
    test_sandbox_guards();
    test_host_through_backend();
    std::puts("vellum-rmlui lua sandbox: ok");
    return 0;
}
