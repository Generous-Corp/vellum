# Child of test_skia_dawn_platform_selection.cmake: runs one reader call so the
# parent can observe its FATAL_ERROR. MODE is "read" (gpu_runtime_read_renderer_lock)
# or "has" (gpu_runtime_renderer_lock_has_platform).
include("${TEST_ROOT}/gpu-runtime/cmake/GpuRuntimeRendererLock.cmake")
if(MODE STREQUAL "has")
    gpu_runtime_renderer_lock_has_platform("${PLATFORM}" _result LOCK "${LOCK}")
else()
    gpu_runtime_read_renderer_lock(PLATFORM "${PLATFORM}" PREFIX _t LOCK "${LOCK}")
endif()
