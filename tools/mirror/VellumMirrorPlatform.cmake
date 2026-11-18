# The platform arguments tools/mirror/mirror_package.py plans from.
#
# Only macOS (CMAKE_SYSTEM_NAME Darwin) plans from the export receipt's Darwin
# arm64 closures. Every other system, the other Apple systems included, passes
# its configure to mirror_package.py, which plans it only from a verified
# platform closure record (tools/mirror/platform_closures.py) and refuses it
# without one.
function(vellum_mirror_platform_args out_var)
    if(CMAKE_SYSTEM_NAME STREQUAL "Darwin")
        set(${out_var} --platform macos-arm64 PARENT_SCOPE)
    else()
        set(${out_var}
            --system "${CMAKE_SYSTEM_NAME}" --processor "${CMAKE_SYSTEM_PROCESSOR}"
            --apple-sdk "${CMAKE_OSX_SYSROOT}" PARENT_SCOPE)
    endif()
endfunction()
