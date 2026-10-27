add_library(vellum-graphics
    src/capture_stats.cpp
    src/paint_command.cpp)
add_library(Vellum::Graphics ALIAS vellum-graphics)

set_target_properties(vellum-graphics PROPERTIES
    EXPORT_NAME Graphics)

target_include_directories(vellum-graphics
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>)

target_compile_features(vellum-graphics PUBLIC cxx_std_20)
target_link_libraries(vellum-graphics
    PUBLIC Vellum::Runtime)

if(APPLE)
    target_sources(vellum-graphics PRIVATE src/core_graphics_canvas.mm)
    target_link_libraries(vellum-graphics PRIVATE
        "-framework CoreGraphics"
        "-framework CoreText"
        "-framework Foundation")
endif()

if(VELLUM_HAS_SKIA_DAWN)
    file(RELATIVE_PATH _vellum_datadir_from_libdir
        "${CMAKE_INSTALL_FULL_LIBDIR}" "${CMAKE_INSTALL_FULL_DATADIR}")
    file(RELATIVE_PATH _vellum_build_datadir_from_provider_dir
        "${CMAKE_BINARY_DIR}"
        "${CMAKE_BINARY_DIR}/${CMAKE_INSTALL_DATADIR}")
    # The provider group is the only image that contains Skia-consuming
    # implementation objects. Keeping these objects beside the pinned Skia
    # definitions avoids a private Skia symbol crossing a dylib boundary.
    target_sources(vellum-skia-dawn-provider PRIVATE
        src/dawn_bootstrap.cpp
        src/skia_dawn_surface.mm)
    target_include_directories(vellum-skia-dawn-provider PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_compile_features(vellum-skia-dawn-provider PRIVATE cxx_std_20)
    target_compile_definitions(vellum-skia-dawn-provider PRIVATE
        VELLUM_HAS_SKIA_DAWN=1
        VELLUM_DATADIR_FROM_LIBDIR="${_vellum_datadir_from_libdir}"
        VELLUM_BUILD_DATADIR_FROM_PROVIDER_DIR="${_vellum_build_datadir_from_provider_dir}")
    target_link_libraries(vellum-skia-dawn-provider PUBLIC Vellum::Graphics)

    # Preserve the Vellum::Gpu binary identity for existing consumers. It is a
    # forwarding frontend; all GPU implementation symbols live in the group.
    add_library(vellum-gpu SHARED src/gpu_frontend_anchor.cpp)
    add_library(Vellum::Gpu ALIAS vellum-gpu)

    set_target_properties(vellum-gpu PROPERTIES
        EXPORT_NAME Gpu
        MACOSX_RPATH ON
        INSTALL_RPATH "@loader_path"
        OSX_ARCHITECTURES arm64
        MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET})

    target_include_directories(vellum-gpu
        PUBLIC
            $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
            $<INSTALL_INTERFACE:include>)
    target_compile_features(vellum-gpu PUBLIC cxx_std_20)
    # Preserve the installed consumer compile contract from the pre-provider
    # GPU target. Public headers and downstream feature gates use this marker.
    target_compile_definitions(vellum-gpu PUBLIC VELLUM_HAS_SKIA_DAWN=1)
    target_link_libraries(vellum-gpu
        PUBLIC Vellum::Graphics Vellum::SkiaDawnProvider)
    # Preserve the legacy direct-link ABI while the provider remains the sole
    # owner of the GPU implementation symbols.
    target_link_options(vellum-gpu PRIVATE
        "LINKER:-reexport_library,$<TARGET_FILE:vellum-skia-dawn-provider>")
    add_custom_command(TARGET vellum-gpu POST_BUILD
        COMMAND "${CMAKE_COMMAND}" -E make_directory
            "${CMAKE_BINARY_DIR}/${CMAKE_INSTALL_DATADIR}/vellum/fonts"
        COMMAND "${CMAKE_COMMAND}" -E copy_directory
            "${PROJECT_SOURCE_DIR}/assets/fonts"
            "${CMAKE_BINARY_DIR}/${CMAKE_INSTALL_DATADIR}/vellum/fonts"
        VERBATIM)
endif()
