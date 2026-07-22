if(NOT APPLE)
    message(FATAL_ERROR "The initial Vellum graphics backend requires Apple CoreGraphics")
endif()

add_library(vellum-graphics
    src/core_graphics_canvas.mm)
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
