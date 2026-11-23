cmake_minimum_required(VERSION 3.24)

if(NOT DEFINED SOURCE_ROOT)
    message(FATAL_ERROR "SOURCE_ROOT is required")
endif()

set(_required
    "tools/android/android_gpu_smoke.cpp"
    "tools/android/CMakeLists.txt")
foreach(_path IN LISTS _required)
    if(NOT EXISTS "${SOURCE_ROOT}/${_path}")
        message(FATAL_ERROR "android-gpu-smoke: missing ${_path}")
    endif()
endforeach()

file(READ "${SOURCE_ROOT}/CMakeLists.txt" _android_cmake)
file(READ "${SOURCE_ROOT}/tools/android/AndroidGpuSmoke.cmake" _android_smoke_cmake)
string(APPEND _android_cmake "\n${_android_smoke_cmake}")
foreach(_needle IN ITEMS
    "vellum-android-gpu-smoke"
    "vellum-android-gpu-smoke-negative"
    "add_executable")
    if(NOT _android_cmake MATCHES "${_needle}")
        message(FATAL_ERROR "android-gpu-smoke: CMake does not declare ${_needle}")
    endif()
endforeach()

file(READ "${SOURCE_ROOT}/tools/android/android_gpu_smoke.cpp" _smoke)
foreach(_needle IN ITEMS
    "backend_type"
    "VellumRenderer"
    "return 77"
    "expected triangles")
    if(NOT _smoke MATCHES "${_needle}")
        message(FATAL_ERROR "android-gpu-smoke: executable misses ${_needle}")
    endif()
endforeach()

if(NOT EXISTS "${SOURCE_ROOT}/../NEXT.md")
    message(FATAL_ERROR "android-gpu-smoke: missing NEXT.md beside clone")
endif()
file(READ "${SOURCE_ROOT}/../NEXT.md" _next)
foreach(_needle IN ITEMS
    "-no-window -no-audio -gpu host -no-snapshot"
    "adb wait-for-device"
    "boot_completed"
    "/data/local/tmp/vellum-android-gpu-smoke"
    "adb emu kill")
    if(NOT _next MATCHES "${_needle}")
        message(FATAL_ERROR "android-gpu-smoke: NEXT.md misses ${_needle}")
    endif()
endforeach()

message(STATUS "android-gpu-smoke: contract passed")
