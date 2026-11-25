# Picks the renderer-lock row the Graphite host qualification checks its Skia
# library against: the row of the platform being configured, never a fixed one.
# A platform with no row is a configure error that names it.
include("${CMAKE_CURRENT_LIST_DIR}/../../cmake/VellumSkiaDawnPlatform.cmake")
include("${CMAKE_CURRENT_LIST_DIR}/GpuRuntimeRendererLock.cmake")

function(gpu_runtime_graphite_lock_platform system processor android_abi output)
    vellum_skia_dawn_platform_key("${system}" "${processor}" "${android_abi}" _key QUIET)
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
