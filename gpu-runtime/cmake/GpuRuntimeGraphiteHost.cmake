# Picks the renderer-lock row the Graphite host qualification checks its Skia
# library against: the row of the platform being configured, never a fixed one.
# A platform with no row is a configure error that names it.
include("${CMAKE_CURRENT_LIST_DIR}/../../cmake/VellumSkiaDawnPlatform.cmake")
include("${CMAKE_CURRENT_LIST_DIR}/GpuRuntimeRendererLock.cmake")

function(gpu_runtime_graphite_lock_platform system processor android_abi output)
    vellum_skia_dawn_platform_key("${system}" "${processor}" "${android_abi}" _key QUIET)
    # The macos-arm64 row names the universal macOS archive: its libskia.a and
    # libdawn_combined.a are fat files holding both x86_64 and arm64 slices
    # (lipo -info), and the row's libskia.a digest is that fat file's digest. An
    # Intel Mac therefore links the very archive this row pins, which is why the
    # platform key has no Darwin/x86_64 entry of its own and the qualification
    # maps it here.
    if(NOT _key AND "${system}" STREQUAL "Darwin" AND "${processor}" STREQUAL "x86_64")
        set(_key "macos-arm64")
    endif()
    if(_key)
        gpu_runtime_renderer_lock_has_platform("${_key}" _locked)
    else()
        set(_locked OFF)
    endif()
    if(NOT _locked)
        message(FATAL_ERROR "Graphite host qualification: the renderer lock has no row "
            "for ${system}/${processor}")
    endif()
    set(${output} "${_key}" PARENT_SCOPE)
endfunction()
