include("${TEST_ROOT}/gpu-runtime/cmake/GpuRuntimeRendererLock.cmake")
gpu_runtime_read_renderer_lock(PLATFORM android-x86_64 PREFIX _t LOCK "${ZERO_LOCK}")
