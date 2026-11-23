if(NOT ANDROID)
    return()
endif()
if(NOT TARGET Vellum::Gpu OR NOT TARGET VellumRmlUi::VellumRenderer)
    message(FATAL_ERROR
        "Android GPU smoke needs Vellum::Gpu and VellumRmlUi::VellumRenderer")
endif()

foreach(_name IN ITEMS vellum-android-gpu-smoke vellum-android-gpu-smoke-negative)
    add_executable(${_name} "${CMAKE_CURRENT_LIST_DIR}/android_gpu_smoke.cpp")
    target_compile_features(${_name} PRIVATE cxx_std_20)
    # The smoke names gpu_runtime::GpuSurface directly. Its definitions come
    # from the GPU provider inside Vellum::Gpu; DeviceApi supplies the headers.
    target_link_libraries(${_name} PRIVATE
        VellumRmlUi::VellumRenderer Vellum::Gpu VellumGpuRuntime::DeviceApi)
    target_link_libraries(${_name} PRIVATE log android)
    set_target_properties(${_name} PROPERTIES POSITION_INDEPENDENT_CODE ON)
endforeach()
target_compile_definitions(vellum-android-gpu-smoke-negative
    PRIVATE VELLUM_ANDROID_GPU_SMOKE_NEGATIVE=1)
