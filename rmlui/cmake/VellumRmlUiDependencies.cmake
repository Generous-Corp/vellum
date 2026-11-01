# Pinned third-party inputs for the RmlUi module. Each is admitted in
# DEPENDENCIES.md, NOTICE.md and provenance/third-party-lock.json; keep the
# identities below and those records in step.
#
# Every dependency is built from source as a static, position-independent,
# hidden-visibility archive and linked privately into the VellumRmlUi::Core
# shared library, so none of their symbols or headers reach a consumer. Their
# own install rules are routed to the VellumRmlUiUpstream component, which the
# VellumRmlUi package never installs.

include(FetchContent)

set(VELLUM_RMLUI_RMLUI_COMMIT 58c751531f4c1a1eb2dc56f409b8e8a323a0c9ad)
set(VELLUM_RMLUI_FREETYPE_URL
    https://download.savannah.gnu.org/releases/freetype/freetype-2.13.3.tar.gz)
set(VELLUM_RMLUI_FREETYPE_SHA256
    5c3a8e78f7b24c20b25b54ee575d6daa40007a5f4eea2845861c3409b3021747)
set(VELLUM_RMLUI_LUA_URL https://www.lua.org/ftp/lua-5.4.7.tar.gz)
set(VELLUM_RMLUI_LUA_SHA256
    9fbf5e28ef86c69858f6d3d34eccc32e911c1a28b4120ff3e84aaa70cfbf1e30)
set(VELLUM_RMLUI_STB_COMMIT d14d46f6b44456eb88beac58cba833a4135d4c89)
set(VELLUM_RMLUI_STB_IMAGE_WRITE_SHA256
    cbd5f0ad7a9cf4468affb36354a1d2338034f2c12473cf1a8e32053cb6914a05)

set(VELLUM_RMLUI_STB_IMAGE_WRITE "" CACHE FILEPATH
    "Local stb_image_write.h to use instead of downloading it (still hash-checked)")

function(vellum_rmlui_fetch_dependencies)
    set(CMAKE_POSITION_INDEPENDENT_CODE ON)
    set(CMAKE_C_VISIBILITY_PRESET hidden)
    set(CMAKE_CXX_VISIBILITY_PRESET hidden)
    set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)
    set(BUILD_SHARED_LIBS OFF)
    set(CMAKE_INSTALL_DEFAULT_COMPONENT_NAME VellumRmlUiUpstream)

    if(VELLUM_RMLUI_FONTS)
        # FreeType under the FreeType License (FTL). Only the font formats
        # RmlUi needs are built; PNG, zlib, bzip2, Brotli and HarfBuzz are
        # disabled so no further library is admitted through it.
        set(FT_DISABLE_ZLIB ON CACHE BOOL "" FORCE)
        set(FT_DISABLE_BZIP2 ON CACHE BOOL "" FORCE)
        set(FT_DISABLE_PNG ON CACHE BOOL "" FORCE)
        set(FT_DISABLE_HARFBUZZ ON CACHE BOOL "" FORCE)
        set(FT_DISABLE_BROTLI ON CACHE BOOL "" FORCE)
        set(SKIP_INSTALL_ALL ON CACHE BOOL "" FORCE)
        FetchContent_Declare(vellum_rmlui_freetype
            URL "${VELLUM_RMLUI_FREETYPE_URL}"
            URL_HASH "SHA256=${VELLUM_RMLUI_FREETYPE_SHA256}")
        FetchContent_MakeAvailable(vellum_rmlui_freetype)
        if(NOT TARGET freetype)
            message(FATAL_ERROR "the pinned FreeType source did not define its freetype target")
        endif()
        add_library(Freetype::Freetype ALIAS freetype)
        # RmlUi probes for FreeType with find_package; the pinned target above
        # must win over any FreeType installed on the build machine.
        set(CMAKE_DISABLE_FIND_PACKAGE_Freetype ON)
        set(RMLUI_FONT_ENGINE "freetype" CACHE STRING "" FORCE)
    else()
        set(RMLUI_FONT_ENGINE "none" CACHE STRING "" FORCE)
    endif()

    set(RMLUI_SAMPLES OFF CACHE BOOL "" FORCE)
    set(RMLUI_THIRDPARTY_CONTAINERS OFF CACHE BOOL "" FORCE)
    set(RMLUI_LOTTIE_PLUGIN OFF CACHE BOOL "" FORCE)
    set(RMLUI_SVG_PLUGIN OFF CACHE BOOL "" FORCE)
    set(RMLUI_HARFBUZZ_SAMPLE OFF CACHE BOOL "" FORCE)
    set(RMLUI_TRACY_PROFILING OFF CACHE BOOL "" FORCE)
    set(RMLUI_INSTALL_LICENSES_AND_BUILD_INFO OFF CACHE BOOL "" FORCE)
    # RmlUi's Lua plugin keeps one process-wide lua_State, so it is not built;
    # VellumRmlUi::Lua drives its own per-instance sandboxed state instead.
    set(RMLUI_LUA_BINDINGS OFF CACHE BOOL "" FORCE)
    FetchContent_Declare(vellum_rmlui_rmlui
        GIT_REPOSITORY https://github.com/mikke89/RmlUi.git
        GIT_TAG "${VELLUM_RMLUI_RMLUI_COMMIT}")
    FetchContent_MakeAvailable(vellum_rmlui_rmlui)
    if(NOT TARGET RmlUi::Core)
        message(FATAL_ERROR "the pinned RmlUi source did not define RmlUi::Core")
    endif()
    get_target_property(_rmlui_type rmlui_core TYPE)
    if(NOT _rmlui_type STREQUAL "STATIC_LIBRARY")
        message(FATAL_ERROR "RmlUi must build as a static library here, got ${_rmlui_type}")
    endif()

    if(VELLUM_RMLUI_LUA)
        # Lua 5.4.7 from the official source archive. Only the library is
        # built; the lua and luac front ends are not.
        FetchContent_Declare(vellum_rmlui_lua
            URL "${VELLUM_RMLUI_LUA_URL}"
            URL_HASH "SHA256=${VELLUM_RMLUI_LUA_SHA256}")
        FetchContent_MakeAvailable(vellum_rmlui_lua)
        file(GLOB _lua_sources "${vellum_rmlui_lua_SOURCE_DIR}/src/*.c")
        list(REMOVE_ITEM _lua_sources
            "${vellum_rmlui_lua_SOURCE_DIR}/src/lua.c"
            "${vellum_rmlui_lua_SOURCE_DIR}/src/luac.c")
        add_library(vellum-rmlui-lua54 STATIC ${_lua_sources})
        set_target_properties(vellum-rmlui-lua54 PROPERTIES
            C_VISIBILITY_PRESET hidden
            POSITION_INDEPENDENT_CODE ON)
        if(NOT WIN32)
            target_compile_definitions(vellum-rmlui-lua54 PRIVATE LUA_USE_POSIX)
        endif()
        target_include_directories(vellum-rmlui-lua54 PUBLIC
            $<BUILD_INTERFACE:${vellum_rmlui_lua_SOURCE_DIR}/src>)
        if(UNIX AND NOT APPLE)
            target_link_libraries(vellum-rmlui-lua54 PRIVATE m)
        endif()
    endif()

    # stb_image_write, used only by the soft renderer's PNG encoder.
    set(_stb_dir "${CMAKE_CURRENT_BINARY_DIR}/_deps/vellum_rmlui_stb")
    set(_stb_header "${_stb_dir}/stb_image_write.h")
    if(VELLUM_RMLUI_STB_IMAGE_WRITE)
        file(MAKE_DIRECTORY "${_stb_dir}")
        configure_file("${VELLUM_RMLUI_STB_IMAGE_WRITE}" "${_stb_header}" COPYONLY)
    elseif(NOT EXISTS "${_stb_header}")
        file(DOWNLOAD
            "https://raw.githubusercontent.com/nothings/stb/${VELLUM_RMLUI_STB_COMMIT}/stb_image_write.h"
            "${_stb_header}"
            EXPECTED_HASH "SHA256=${VELLUM_RMLUI_STB_IMAGE_WRITE_SHA256}"
            TLS_VERIFY ON)
    endif()
    file(SHA256 "${_stb_header}" _stb_actual)
    if(NOT _stb_actual STREQUAL VELLUM_RMLUI_STB_IMAGE_WRITE_SHA256)
        message(FATAL_ERROR
            "stb_image_write.h does not match its pinned SHA-256 (got ${_stb_actual})")
    endif()
    set(VELLUM_RMLUI_STB_INCLUDE_DIR "${_stb_dir}" PARENT_SCOPE)
endfunction()

vellum_rmlui_fetch_dependencies()
