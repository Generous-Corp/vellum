if(NOT APPLE)
    message(FATAL_ERROR "The initial Vellum graphics backend requires Apple CoreGraphics")
endif()

add_library(vellum-graphics
    src/core_graphics_canvas.mm
    src/capture_stats.cpp)
add_library(Vellum::Graphics ALIAS vellum-graphics)

set_target_properties(vellum-graphics PROPERTIES
    EXPORT_NAME Graphics)

target_include_directories(vellum-graphics
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>)

target_compile_features(vellum-graphics PUBLIC cxx_std_20)
target_link_libraries(vellum-graphics
    PUBLIC Vellum::Runtime
    PRIVATE
        "-framework CoreGraphics"
        "-framework CoreText"
        "-framework Foundation")

if(VELLUM_HAS_SKIA_DAWN)
    add_library(vellum-gpu SHARED
        src/skia_dawn_surface.mm)
    add_library(Vellum::Gpu ALIAS vellum-gpu)

    set_target_properties(vellum-gpu PROPERTIES
        EXPORT_NAME Gpu
        MACOSX_RPATH ON
        INSTALL_RPATH "@loader_path"
        OSX_ARCHITECTURES arm64
        MACOSX_DEPLOYMENT_TARGET 15.0)

    target_include_directories(vellum-gpu
        PUBLIC
            $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
            $<INSTALL_INTERFACE:include>)
    target_compile_features(vellum-gpu PUBLIC cxx_std_20)
    target_compile_definitions(vellum-gpu PUBLIC VELLUM_HAS_SKIA_DAWN=1)
    target_link_libraries(vellum-gpu
        PUBLIC Vellum::Graphics
        PRIVATE VellumSkiaDawn)
endif()
